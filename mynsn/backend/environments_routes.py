"""環境（Environment）持久化路由：建立／選擇環境、程式庫列表、版本歷程與 diff、
以及環境底下的執行歷史。實際送測試生成仍是呼叫既有的 ifl_client.create_job
（跟 routes.py 的 /api/generate 共用 build_job_payload），這裡只是多包一層
「記住這是哪個環境、哪個版本」的持久化，讓結果不再受 ifl_api 的 1 小時 TTL、
純記憶體 JobStore 限制。"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Query

from . import ifl_client, storage
from .routes import build_job_payload, build_result_summary
from .schemas import CreateEnvironmentRequest, CreateRunRequest, UpdateEnvironmentSourceRequest

router = APIRouter(prefix="/api/environments")
runs_router = APIRouter(prefix="/api/runs")


def _require_environment(env_id: str) -> dict:
    env = storage.get_environment(env_id)
    if env is None:
        raise HTTPException(404, "找不到這個環境")
    return env


async def _reconcile_run(run: dict) -> dict:
    """run 還沒存到結果快照時，主動跟 ifl_api 對一次現況再回傳。

    結果快照本來是靠 WebSocket 轉發（routes.py 的 job_stream）在事件當下順便存檔，
    但那個掛勾點只有「前端真的開著那條 WS 連線看到 done」才會觸發——如果使用者
    中途關掉分頁、或根本沒點開進度畫面，run 會一直卡在 running，即使 ifl_api
    背景其實早就跑完了。這裡補一個「使用者之後回來看執行紀錄時」的被動校正：
    查一次 ifl_api 現在的真實狀態，成功就把結果存下來，失敗／取消就更新狀態，
    避免永遠卡在 running。ifl_api 已經重啟／job 被 1 小時 TTL 清掉時查不到，
    這裡就放棄、維持原本存的狀態——那是 ifl_api 本身記憶體限制造成的資訊遺失，
    不是這裡能挽救的。
    """
    if run["result"] is not None or not run["ifl_job_id"]:
        return run
    try:
        status_info = await ifl_client.get_job(run["ifl_job_id"])
    except httpx.HTTPError:
        return run

    ifl_status = status_info.get("status")
    if ifl_status == "succeeded":
        try:
            raw = await ifl_client.get_job_result(run["ifl_job_id"])
        except httpx.HTTPError:
            return run
        storage.snapshot_run_result_by_ifl_job_id(run["ifl_job_id"], "succeeded", raw)
        return storage.get_run(run["id"]) or run
    if ifl_status in ("failed", "cancelled"):
        storage.update_run_status_by_ifl_job_id(run["ifl_job_id"], ifl_status)
        return storage.get_run(run["id"]) or run
    return run


@router.post("")
async def create_environment(req: CreateEnvironmentRequest) -> dict:
    if not req.name.strip():
        raise HTTPException(400, "請輸入環境名稱")
    env_id = storage.create_environment(
        name=req.name.strip(),
        language=req.language,
        func_name=req.func_name,
        func_signature=req.func_signature,
        domain_context=req.domain_context,
        domain_types=req.domain_types,
        domain_bounds=req.domain_bounds,
        source_code=req.source_code,
        test_type=req.test_type,
    )
    return {"env_id": env_id}


@router.get("")
async def list_environments() -> list[dict]:
    return storage.list_environments()


@router.get("/{env_id}")
async def get_environment(env_id: str) -> dict:
    return _require_environment(env_id)


@router.put("/{env_id}/source")
async def update_environment_source(env_id: str, req: UpdateEnvironmentSourceRequest) -> dict:
    _require_environment(env_id)
    version_id, is_new_version = storage.get_or_create_version(env_id, req.source_code)
    storage.update_environment_meta(
        env_id,
        func_name=req.func_name,
        func_signature=req.func_signature,
        domain_context=req.domain_context,
        domain_types=req.domain_types,
        domain_bounds=req.domain_bounds,
    )
    return {"env_id": env_id, "version_id": version_id, "is_new_version": is_new_version}


@router.delete("/{env_id}")
async def delete_environment(env_id: str) -> dict:
    _require_environment(env_id)
    storage.delete_environment(env_id)
    return {"ok": True}


@router.get("/{env_id}/versions")
async def list_versions(env_id: str) -> list[dict]:
    _require_environment(env_id)
    return storage.list_versions(env_id)


@router.get("/{env_id}/diff")
async def diff_versions(
    env_id: str,
    from_version: str = Query(..., alias="from"),
    to_version: str = Query(..., alias="to"),
) -> dict:
    _require_environment(env_id)
    return {"diff": storage.diff_versions(from_version, to_version)}


@router.post("/{env_id}/runs")
async def create_environment_run(env_id: str, req: CreateRunRequest) -> dict:
    env = _require_environment(env_id)

    payload = build_job_payload(
        env["source_code"], env["func_name"], env["func_signature"], env["domain_context"],
        env["domain_types"], env["domain_bounds"], req.max_iterations, req.llm,
    )
    try:
        job = await ifl_client.create_job(payload, req.llm.api_key)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, exc.response.text) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"無法連線到核心引擎 API：{exc}") from exc

    run_id = storage.create_run(
        env_id, env["current_version_id"], job["job_id"], req.llm.provider, req.llm.model
    )
    return {"run_id": run_id, "ifl_job_id": job["job_id"]}


@router.get("/{env_id}/runs")
async def list_environment_runs(env_id: str) -> list[dict]:
    _require_environment(env_id)
    runs = storage.list_runs(env_id)
    # 列表用的是精簡欄位（沒有完整 result），還在 running 的才需要對一次現況；
    # 用 get_run 重新查一次完整資料再校正，避免列表跟詳情兩邊各寫一套校正邏輯。
    for i, r in enumerate(runs):
        if r["status"] == "running":
            full = await _reconcile_run(storage.get_run(r["id"]))
            runs[i] = {
                **r,
                "status": full["status"],
                "finished_at": full["finished_at"],
                "coverage_pct": (
                    round(full["result"].get("final_coverage", 0.0) * 100, 1)
                    if full["result"] else None
                ),
            }
    return runs


@runs_router.get("/{run_id}")
async def get_run(run_id: str) -> dict:
    """優先回傳存檔快照（不受 ifl_api TTL／重啟影響）；還沒存好時先跟 ifl_api
    對一次現況（見 _reconcile_run），真的還在跑才退回去即時代理目前進度。"""
    run = storage.get_run(run_id)
    if run is None:
        raise HTTPException(404, "找不到這個執行紀錄")

    run = await _reconcile_run(run)
    if run["result"] is not None:
        response = build_result_summary(run["result"])
        response["run"] = {k: v for k, v in run.items() if k != "result"}
        return response

    if not run["ifl_job_id"]:
        raise HTTPException(409, "這次執行尚未產生結果")
    raise HTTPException(409, f"這次執行尚未完成（目前狀態：{run['status']}）")
