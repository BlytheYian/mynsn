"""呼叫既有 ifl_api（核心引擎 API）的薄客戶端。

mynsn 不 import ifl_mcdc；所有解析／生成／覆蓋率計算都透過 HTTP 呼叫
ifl_api，避免重複核心邏輯或版本漂移。
"""
from __future__ import annotations

import os
from typing import Any

import httpx

IFL_API_BASE_URL = os.environ.get("IFL_API_BASE_URL", "http://127.0.0.1:8100")
IFL_API_WS_BASE_URL = (
    IFL_API_BASE_URL.replace("https://", "wss://").replace("http://", "ws://")
)

_TIMEOUT = httpx.Timeout(30.0, read=30.0)


async def parse_source(source_code: str, language: str = "python") -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{IFL_API_BASE_URL}/v1/parse",
            json={"source_code": source_code, "language": language},
        )
        resp.raise_for_status()
        return resp.json()


async def create_job(payload: dict[str, Any], llm_api_key: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{IFL_API_BASE_URL}/v1/jobs",
            json=payload,
            headers={"X-LLM-Api-Key": llm_api_key},
        )
        resp.raise_for_status()
        return resp.json()


async def list_jobs() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(f"{IFL_API_BASE_URL}/v1/jobs")
        resp.raise_for_status()
        return resp.json()


async def get_job(job_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(f"{IFL_API_BASE_URL}/v1/jobs/{job_id}")
        resp.raise_for_status()
        return resp.json()


async def get_job_result(job_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(f"{IFL_API_BASE_URL}/v1/jobs/{job_id}/result")
        resp.raise_for_status()
        return resp.json()


async def cancel_job(job_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.delete(f"{IFL_API_BASE_URL}/v1/jobs/{job_id}")
        resp.raise_for_status()
        return resp.json()
