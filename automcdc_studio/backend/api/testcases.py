from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.database import get_db, new_id
from backend.models.orm import TestCase

router = APIRouter()


class TestCaseCreate(BaseModel):
    env_id: str
    name: str = ""
    inputs: dict
    expected_output: str | None = None
    expected_source: str = "manual"


class TestCaseUpdate(BaseModel):
    name: str | None = None
    inputs: dict | None = None
    expected_output: str | None = None
    expected_source: str | None = None
    enabled: bool | None = None


@router.get("")
async def list_testcases(env_id: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(TestCase).where(TestCase.env_id == env_id)
    )).scalars().all()
    return [
        {
            "id": tc.id,
            "name": tc.name,
            "created_by": tc.created_by,
            "inputs": tc.inputs,
            "expected_output": tc.expected_output,
            "expected_source": tc.expected_source,
            "enabled": tc.enabled,
            "created_at": tc.created_at,
        }
        for tc in rows
    ]


@router.post("", status_code=201)
async def create_testcase(req: TestCaseCreate, db: AsyncSession = Depends(get_db)):
    tc = TestCase(
        id=new_id(),
        env_id=req.env_id,
        name=req.name,
        created_by="manual",
        inputs=req.inputs,
        expected_output=req.expected_output,
        expected_source=req.expected_source if req.expected_output else "none",
    )
    db.add(tc)
    await db.commit()
    await db.refresh(tc)
    return {"id": tc.id}


@router.put("/{case_id}")
async def update_testcase(case_id: str, req: TestCaseUpdate,
                          db: AsyncSession = Depends(get_db)):
    tc = await db.get(TestCase, case_id)
    if not tc:
        raise HTTPException(404, "測試案例不存在")

    if req.name is not None:
        tc.name = req.name
    if req.inputs is not None:
        tc.inputs = req.inputs
    if req.expected_output is not None:
        tc.expected_output = req.expected_output
        tc.expected_source = req.expected_source or "manual"
    if req.enabled is not None:
        tc.enabled = req.enabled

    await db.commit()
    return {"id": tc.id}


@router.delete("/{case_id}", status_code=204)
async def delete_testcase(case_id: str, db: AsyncSession = Depends(get_db)):
    tc = await db.get(TestCase, case_id)
    if not tc:
        raise HTTPException(404, "測試案例不存在")
    await db.delete(tc)
    await db.commit()
