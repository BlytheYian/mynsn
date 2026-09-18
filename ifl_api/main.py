"""IFL-MCDC 核心引擎 API 進入點。

啟動方式：
    uvicorn ifl_api.main:app --reload --port 8100
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .branch_routes import router as branch_router
from .routes import router

app = FastAPI(title="IFL-MCDC Core API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(branch_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
