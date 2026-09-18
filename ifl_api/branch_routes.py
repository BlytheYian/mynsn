"""分支覆蓋 API 路由：/v1/branch/parse 與 /v1/branch/jobs 系列。"""
from __future__ import annotations

import asyncio
import datetime

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect

from ifl_branch.layer1.ast_parser import ASTParser

from .branch_jobs import JOB_STORE, Job, cancel_job, submit_job
from .branch_schemas import (
    BranchJobCreateRequest,
    BranchJobProgress,
    BranchJobResultResponse,
    BranchJobStatusResponse,
    BranchParseRequest,
    BranchParseResponse,
)
from .branch_serialization import serialize_decision_node

router = APIRouter()


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()


def _to_status_response(job: Job) -> BranchJobStatusResponse:
    return BranchJobStatusResponse(
        job_id=job.id,
        status=job.status,  # type: ignore[arg-type]
        created_at=_iso(job.created_at) or "",
        started_at=_iso(job.started_at),
        finished_at=_iso(job.finished_at),
        progress=BranchJobProgress(**job.progress),
        error=job.error,
    )


# ── /v1/branch/parse ──────────────────────────────────────────────────────────


@router.post("/v1/branch/parse", response_model=BranchParseResponse)
def parse_source(req: BranchParseRequest) -> BranchParseResponse:
    try:
        decision_nodes = ASTParser().parse_source(req.source_code)
    except Exception as exc:
        raise HTTPException(400, f"parse failed: {exc}") from exc
    return BranchParseResponse(
        decision_nodes=[serialize_decision_node(dn) for dn in decision_nodes]  # type: ignore[arg-type]
    )


# ── /v1/branch/jobs ────────────────────────────────────────────────────────────


@router.post("/v1/branch/jobs", status_code=202, response_model=BranchJobStatusResponse)
async def create_branch_job(
    req: BranchJobCreateRequest,
    x_llm_api_key: str = Header("", alias="X-LLM-Api-Key"),
) -> BranchJobStatusResponse:
    job = await submit_job(req, api_key=x_llm_api_key)
    return _to_status_response(job)


@router.get("/v1/branch/jobs", response_model=list[BranchJobStatusResponse])
def list_branch_jobs() -> list[BranchJobStatusResponse]:
    return [_to_status_response(j) for j in JOB_STORE.list()]


@router.get("/v1/branch/jobs/{job_id}", response_model=BranchJobStatusResponse)
def get_branch_job(job_id: str) -> BranchJobStatusResponse:
    job = JOB_STORE.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return _to_status_response(job)


@router.get("/v1/branch/jobs/{job_id}/result", response_model=BranchJobResultResponse)
def get_branch_job_result(job_id: str) -> BranchJobResultResponse:
    job = JOB_STORE.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status != "succeeded" or job.result is None:
        raise HTTPException(409, {
            "message": "result not available",
            "job_status": job.status,
            "error": job.error,
        })
    return BranchJobResultResponse(job_id=job.id, **job.result)


@router.delete("/v1/branch/jobs/{job_id}", response_model=BranchJobStatusResponse)
def delete_branch_job(job_id: str) -> BranchJobStatusResponse:
    """要求取消 job。取消為協作式：worker 執行緒會在下一次測試執行後才真正停止。"""
    job = cancel_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return _to_status_response(job)


@router.websocket("/v1/branch/jobs/{job_id}/stream")
async def stream_branch_job(ws: WebSocket, job_id: str) -> None:
    job = JOB_STORE.get(job_id)
    await ws.accept()
    if job is None:
        await ws.send_json({"type": "error", "detail": "job not found"})
        await ws.close()
        return

    # 先掛上訂閱佇列，再檢查狀態：避免「檢查時還在跑、送出前就跑完」導致事件遺失。
    queue: asyncio.Queue = asyncio.Queue()
    job.subscribers.append(queue)
    try:
        await ws.send_json({"type": "status", **_to_status_response(job).model_dump()})
        if job.status in ("succeeded", "failed", "cancelled"):
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
