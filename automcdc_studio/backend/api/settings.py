from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.database import get_db, new_id
from backend.models.orm import LLMConfig

router = APIRouter()


class LLMConfigUpdate(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4.1-mini"
    api_key: str = ""
    base_url: str = "http://localhost:11434"
    max_iterations: int = 50
    token_budget: int = 100_000


@router.get("/llm")
async def get_llm_config(db: AsyncSession = Depends(get_db)):
    config = (await db.execute(select(LLMConfig))).scalar_one_or_none()
    if not config:
        return {
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "max_iterations": 50,
            "token_budget": 100_000,
            "total_tokens_used": 0,
        }
    return {
        "provider": config.provider,
        "model": config.model,
        "base_url": config.base_url,
        "max_iterations": config.max_iterations,
        "token_budget": config.token_budget,
        "total_tokens_used": config.total_tokens_used,
    }


@router.put("/llm")
async def update_llm_config(req: LLMConfigUpdate, db: AsyncSession = Depends(get_db)):
    config = (await db.execute(select(LLMConfig))).scalar_one_or_none()

    if not config:
        config = LLMConfig(id=new_id())
        db.add(config)

    config.provider = req.provider
    config.model = req.model
    config.api_key_encrypted = req.api_key
    config.base_url = req.base_url
    config.max_iterations = req.max_iterations
    config.token_budget = req.token_budget
    config.updated_at = datetime.datetime.utcnow()

    await db.commit()
    return {"status": "updated"}
