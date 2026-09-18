"""非同步 Job 管理：背景執行 BranchOrchestrator，提供進度回報與取消。

跟 jobs.py（MC/DC）平行、獨立的一份，各自一組 JOB_STORE，
避免 job id 空間互相干擾。
"""
from __future__ import annotations

import asyncio
import dataclasses
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from ifl_branch.config import BranchConfig
from ifl_branch.orchestrator import BranchOrchestrator

from .branch_schemas import BranchJobCreateRequest

_EXECUTOR = ThreadPoolExecutor(max_workers=4)
JOB_TTL_SECONDS = 3600.0


class JobCancelledError(Exception):
    """Job 被使用者取消時，由 _TrackedOrchestrator 主動拋出以中止 run() 迴圈。"""


class _TrackedOrchestrator(BranchOrchestrator):
    """覆寫 _run_test 以掛上進度回報與取消檢查，其餘流程完全繼承。"""

    def __init__(
        self,
        config: BranchConfig,
        on_progress: Callable[[dict[str, Any]], None],
        cancel_event: threading.Event,
    ) -> None:
        super().__init__(config)
        self._on_progress = on_progress
        self._cancel_event = cancel_event
        self._test_case_count = 0

    def _run_test(self, module: Any, test_case: dict[str, Any], log: Any) -> str:
        test_id = super()._run_test(module, test_case, log)
        self._test_case_count += 1
        self._on_progress({
            "test_case_count": self._test_case_count,
            "latest_case": {k: v for k, v in test_case.items() if not str(k).startswith("__")},
        })
        if self._cancel_event.is_set():
            raise JobCancelledError("job cancelled by client")
        return test_id


@dataclasses.dataclass
class Job:
    id: str
    status: str = "queued"
    created_at: float = dataclasses.field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    progress: dict[str, Any] = dataclasses.field(
        default_factory=lambda: {"iteration": 0, "max_iterations": 0, "test_case_count": 0, "latest_case": None}
    )
    result: dict[str, Any] | None = None
    error: str | None = None
    cancel_event: threading.Event = dataclasses.field(default_factory=threading.Event)
    subscribers: list[asyncio.Queue] = dataclasses.field(default_factory=list)


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self) -> Job:
        job = Job(id=uuid.uuid4().hex)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return list(self._jobs.values())

    def purge_expired(self, ttl: float = JOB_TTL_SECONDS) -> None:
        now = time.time()
        expired = [
            jid for jid, j in self._jobs.items()
            if j.finished_at is not None and now - j.finished_at > ttl
        ]
        for jid in expired:
            del self._jobs[jid]


JOB_STORE = JobStore()


def _publish(job: Job, loop: asyncio.AbstractEventLoop, event: dict[str, Any]) -> None:
    """從背景執行緒安全地把事件送進每個訂閱者的 asyncio.Queue。"""
    for q in list(job.subscribers):
        loop.call_soon_threadsafe(q.put_nowait, event)


def _make_config(req: BranchJobCreateRequest, api_key: str) -> BranchConfig:
    return BranchConfig(
        func_name=req.func_name,
        func_signature=req.func_signature,
        domain_context=req.domain_context,
        domain_types=req.domain_types,
        domain_bounds=req.domain_bounds,
        preceding_direction=req.preceding_direction,
        min_initial_random=req.min_initial_random,
        max_iterations=req.max_iterations,
        llm_provider=req.llm.provider,
        llm_model=req.llm.model,
        llm_api_key=api_key,
        llm_base_url=req.llm.base_url,
        llm_temperature=req.llm.temperature,
        llm_num_ctx=req.llm.num_ctx,
    )


def _execute(
    job: Job, req: BranchJobCreateRequest, api_key: str, loop: asyncio.AbstractEventLoop
) -> None:
    job.status = "running"
    job.started_at = time.time()
    job.progress["max_iterations"] = req.max_iterations
    _publish(job, loop, {"type": "status", "status": "running"})

    tmp_path: str | None = None
    try:
        config = _make_config(req, api_key)
        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write(req.source_code)
            tmp_path = f.name

        def on_progress(info: dict[str, Any]) -> None:
            job.progress.update(info)
            _publish(job, loop, {"type": "progress", **info})

        orchestrator = _TrackedOrchestrator(config, on_progress, job.cancel_event)
        result = orchestrator.run(tmp_path)

        job.result = dataclasses.asdict(result)
        job.status = "succeeded"
    except JobCancelledError:
        job.status = "cancelled"
        job.error = "cancelled by client"
    except Exception as exc:  # noqa: BLE001  # 任何引擎內部例外都要轉為 job 失敗狀態，不能讓 worker 執行緒吞掉
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        job.finished_at = time.time()
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
        _publish(job, loop, {"type": "done", "status": job.status, "error": job.error})


async def submit_job(req: BranchJobCreateRequest, api_key: str = "") -> Job:
    JOB_STORE.purge_expired()
    job = JOB_STORE.create()
    job.progress["max_iterations"] = req.max_iterations
    loop = asyncio.get_event_loop()
    loop.run_in_executor(_EXECUTOR, _execute, job, req, api_key, loop)
    return job


def cancel_job(job_id: str) -> Job | None:
    job = JOB_STORE.get(job_id)
    if job is None:
        return None
    if job.status in ("queued", "running"):
        job.cancel_event.set()
    return job
