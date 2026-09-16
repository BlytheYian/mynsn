"""API 路由：/v1/parse 與 /v1/jobs 系列。"""
from __future__ import annotations

import asyncio
import datetime

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect

from ifl_mcdc.layer1.ast_parser import ASTParser

from .jobs import JOB_STORE, Job, cancel_job, submit_job
from .schemas import (
    JobCreateRequest,
    JobProgress,
    JobResultResponse,
    JobStatusResponse,
    ParseRequest,
    ParseResponse,
)
from .serialization import serialize_decision_node

router = APIRouter()


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()


def _to_status_response(job: Job) -> JobStatusResponse:
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,  # type: ignore[arg-type]
        created_at=_iso(job.created_at) or "",
        started_at=_iso(job.started_at),
        finished_at=_iso(job.finished_at),
        progress=JobProgress(**job.progress),
        error=job.error,
    )


# ── /v1/parse ────────────────────────────────────────────────────────────────


@router.post("/v1/parse", response_model=ParseResponse)
def parse_source(req: ParseRequest) -> ParseResponse:
    try:
        decision_nodes = ASTParser().parse_source(req.source_code)
    except Exception as exc:
        raise HTTPException(400, f"parse failed: {exc}") from exc
    return ParseResponse(
        decision_nodes=[serialize_decision_node(dn) for dn in decision_nodes]  # type: ignore[arg-type]
    )


# ── /v1/jobs ─────────────────────────────────────────────────────────────────


@router.post("/v1/jobs", status_code=202, response_model=JobStatusResponse)
async def create_job(
    req: JobCreateRequest,
    x_llm_api_key: str = Header("", alias="X-LLM-Api-Key"),
) -> JobStatusResponse:
    job = await submit_job(req, api_key=x_llm_api_key)
    return _to_status_response(job)


@router.get("/v1/jobs", response_model=list[JobStatusResponse])
def list_jobs() -> list[JobStatusResponse]:
    return [_to_status_response(j) for j in JOB_STORE.list()]


@router.get("/v1/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str) -> JobStatusResponse:
    job = JOB_STORE.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return _to_status_response(job)


@router.get("/v1/jobs/{job_id}/result", response_model=JobResultResponse)
def get_job_result(job_id: str) -> JobResultResponse:
    job = JOB_STORE.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status != "succeeded" or job.result is None:
        raise HTTPException(409, {
            "message": "result not available",
            "job_status": job.status,
            "error": job.error,
        })
    return JobResultResponse(job_id=job.id, **job.result)


@router.delete("/v1/jobs/{job_id}", response_model=JobStatusResponse)
def delete_job(job_id: str) -> JobStatusResponse:
    """要求取消 job。取消為協作式：worker 執行緒會在下一次測試執行後才真正停止。"""
    job = cancel_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return _to_status_response(job)


@router.websocket("/v1/jobs/{job_id}/stream")
async def stream_job(ws: WebSocket, job_id: str) -> None:
    job = JOB_STORE.get(job_id)
    await ws.accept()
    if job is None:
        await ws.send_json({"type": "error", "detail": "job not found"})
        await ws.close()
        return

    # 先掛上訂閱佇列，再檢查狀態：避免「檢查時還在跑、送出前就跑完」導致事件遺失、
    # 用戶端永遠收不到 done 而卡死的競態。
    queue: asyncio.Queue = asyncio.Queue()
    job.subscribers.append(queue)
    try:
        await ws.send_json({"type": "status", **_to_status_response(job).model_dump()})
        if job.status in ("succeeded", "failed", "cancelled"):
            # job 在訂閱前就已跑完：不會再有人發佈事件，直接補一個 done 收尾。
            await ws.send_json({"type": "done", "status": job.status, "error": job.error})
            return
        while True:
            event = await queue.get()
            await ws.send_json(event)
            if event.get("type") == "done":
                break
    except WebSocketDisconnect:
        pass
    finally:
        if queue in job.subscribers:
            job.subscribers.remove(queue)
