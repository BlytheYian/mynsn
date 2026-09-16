"""ifl_api 核心 API 的端對端測試。

成功案例刻意選用 k=1（單一原子條件）的 fixture：MC/DC 對於單一條件只需要
True/False 各一筆測試，IFLOrchestrator.run() 的 Phase 1（初始隨機測試）就
會滿足收斂條件，完全不必呼叫 LLM，測試才能快速且不依賴網路。

WebSocket 測試刻意不用 fastapi.testclient.TestClient：目前這個版本的
httpx+starlette 組合，一般 HTTP 呼叫與 websocket_connect() 各自跑在不同的
event loop 上（deprecation warning 已提示這個行為），導致背景 job 執行緒用
call_soon_threadsafe 發佈的進度事件送到錯誤的 loop、永遠叫不醒等待中的
websocket，測試會整個卡死。改為在背景執行緒啟動一個真正的 uvicorn server，
用 httpx + websockets 走真實的 TCP/WebSocket 連線，行為才會等同正式部署
（單一 process 只有一個 event loop）。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx
import uvicorn
import websockets
from fastapi.testclient import TestClient

from ifl_api.jobs import JOB_STORE, Job, _execute, _make_config
from ifl_api.main import app
from ifl_api.schemas import JobCreateRequest

client = TestClient(app)

K1_SOURCE = "def check(flag):\n    if flag:\n        return 1\n    return 0\n"
K2_SOURCE = "def check(a, b):\n    if a and b:\n        return 1\n    return 0\n"
NO_BRANCH_SOURCE = "def add(a, b):\n    return a + b\n"


def _wait_for_terminal(job_id: str, timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/v1/jobs/{job_id}")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            return body
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not reach a terminal state in {timeout}s")


# ── /v1/parse ────────────────────────────────────────────────────────────────


def test_parse_returns_decision_nodes_with_coupling():
    resp = client.post("/v1/parse", json={"source_code": K2_SOURCE, "language": "python"})
    assert resp.status_code == 200
    nodes = resp.json()["decision_nodes"]
    assert len(nodes) == 1
    node = nodes[0]
    assert node["node_type"] == "If"
    cs = node["condition_set"]
    assert cs["k"] == 2
    assert [c["cond_id"] for c in cs["conditions"]] == ["D1.c1", "D1.c2"]
    assert cs["coupling_matrix"] == [[None, "AND"], ["AND", None]]


def test_parse_invalid_syntax_returns_400():
    resp = client.post("/v1/parse", json={"source_code": "def broken(:\n", "language": "python"})
    assert resp.status_code == 400


# ── /v1/jobs：成功／失敗流程 ───────────────────────────────────────────────────


def test_job_succeeds_without_llm_for_trivial_condition():
    create = client.post("/v1/jobs", json=JobCreateRequest(
        source_code=K1_SOURCE,
        func_name="check",
        func_signature="check(flag)",
        domain_types={"flag": "bool"},
        max_iterations=5,
    ).model_dump())
    assert create.status_code == 202
    job_id = create.json()["job_id"]

    status = _wait_for_terminal(job_id)
    assert status["status"] == "succeeded", status

    result = client.get(f"/v1/jobs/{job_id}/result")
    assert result.status_code == 200
    body = result.json()
    assert body["converged"] is True
    assert body["final_coverage"] == 1.0
    assert len(body["test_suite"]) >= 2


def test_job_fails_when_no_decision_node_found():
    create = client.post("/v1/jobs", json=JobCreateRequest(
        source_code=NO_BRANCH_SOURCE,
        func_name="add",
        func_signature="add(a, b)",
        domain_types={"a": "int", "b": "int"},
        max_iterations=5,
    ).model_dump())
    assert create.status_code == 202
    job_id = create.json()["job_id"]

    status = _wait_for_terminal(job_id)
    assert status["status"] == "failed"
    assert "決策節點" in status["error"] or "ValueError" in status["error"]

    result = client.get(f"/v1/jobs/{job_id}/result")
    assert result.status_code == 409
    detail = result.json()["detail"]
    assert detail["job_status"] == "failed"
    assert detail["error"]


def test_job_and_result_404_for_unknown_id():
    assert client.get("/v1/jobs/does-not-exist").status_code == 404
    assert client.get("/v1/jobs/does-not-exist/result").status_code == 404
    assert client.delete("/v1/jobs/does-not-exist").status_code == 404


def test_list_jobs_includes_created_job():
    create = client.post("/v1/jobs", json=JobCreateRequest(
        source_code=K1_SOURCE,
        func_name="check",
        func_signature="check(flag)",
        domain_types={"flag": "bool"},
        max_iterations=5,
    ).model_dump())
    job_id = create.json()["job_id"]
    _wait_for_terminal(job_id)

    listed = client.get("/v1/jobs")
    assert listed.status_code == 200
    assert any(j["job_id"] == job_id for j in listed.json())


# ── /v1/jobs：WebSocket 進度串流（需要真實 server，見檔頭說明）──────────────────


class _LiveServer:
    """在背景執行緒跑一個真正的 uvicorn server，供 WebSocket 測試使用。"""

    def __init__(self) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.01)
        assert self.server.started, "live server 啟動逾時"
        port = self.server.servers[0].sockets[0].getsockname()[1]
        return f"127.0.0.1:{port}"

    def __exit__(self, *exc_info: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


def test_ws_stream_emits_progress_then_done():
    async def _run(host_port: str) -> list[dict]:
        async with httpx.AsyncClient(base_url=f"http://{host_port}") as hc:
            create = await hc.post("/v1/jobs", json=JobCreateRequest(
                source_code=K1_SOURCE,
                func_name="check",
                func_signature="check(flag)",
                domain_types={"flag": "bool"},
                max_iterations=5,
            ).model_dump())
            job_id = create.json()["job_id"]

        events: list[dict] = []
        uri = f"ws://{host_port}/v1/jobs/{job_id}/stream"
        async with websockets.connect(uri) as ws:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                events.append(msg)
                if msg["type"] == "done":
                    break
        return events

    with _LiveServer() as host_port:
        events = asyncio.run(_run(host_port))

    types = [e["type"] for e in events]
    assert types[0] == "status"
    assert types[-1] == "done"
    assert events[-1]["status"] == "succeeded"


# ── jobs.py：api_key 走 header 不走 body 的線路確認 ──────────────────────────────


def test_make_config_uses_header_api_key_not_body():
    req = JobCreateRequest(
        source_code=K1_SOURCE,
        func_name="check",
        func_signature="check(flag)",
        domain_types={"flag": "bool"},
    )
    config = _make_config(req, api_key="secret-from-header")
    assert config.llm_api_key == "secret-from-header"


def test_job_create_request_has_no_api_key_field():
    assert "api_key" not in JobCreateRequest.model_fields["llm"].annotation.model_fields


# ── jobs.py：取消機制（直接呼叫 _execute，避免真實排程的時序不確定性）──────────────


def test_cancel_stops_execution_before_first_iteration_completes():
    job = JOB_STORE.create()
    job.cancel_event.set()  # 模擬「job 開始執行前就已被要求取消」
    req = JobCreateRequest(
        source_code=K2_SOURCE,
        func_name="check",
        func_signature="check(a, b)",
        domain_types={"a": "bool", "b": "bool"},
        max_iterations=5,
    )
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        _execute(job, req, "", loop)
    finally:
        loop.close()

    assert job.status == "cancelled"
    assert job.error == "cancelled by client"
