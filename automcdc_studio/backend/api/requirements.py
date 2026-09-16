from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.database import get_db, new_id
from backend.models.orm import Requirement, RequirementLink

router = APIRouter()


class RequirementCreate(BaseModel):
    req_id: str
    description: str
    source: str = ""


@router.get("")
async def list_requirements(env_id: str = "", db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Requirement))).scalars().all()
    result = []
    for r in rows:
        links = (await db.execute(
            select(RequirementLink).where(RequirementLink.requirement_id == r.id)
        )).scalars().all()
        result.append({
            "id": r.id, "req_id": r.req_id,
            "description": r.description, "source": r.source,
            "linked_cases": [lk.test_case_id for lk in links],
        })
    return result


@router.post("", status_code=201)
async def create_requirement(req: RequirementCreate, db: AsyncSession = Depends(get_db)):
    r = Requirement(id=new_id(), req_id=req.req_id,
                    description=req.description, source=req.source)
    db.add(r)
    await db.commit()
    return {"id": r.id}


@router.delete("/{req_id}", status_code=204)
async def delete_requirement(req_id: str, db: AsyncSession = Depends(get_db)):
    r = await db.get(Requirement, req_id)
    if not r:
        raise HTTPException(404)
    await db.delete(r)
    await db.commit()


@router.post("/{req_id}/link/{case_id}", status_code=201)
async def link_case(req_id: str, case_id: str, db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(
        select(RequirementLink).where(
            RequirementLink.requirement_id == req_id,
            RequirementLink.test_case_id == case_id,
        )
    )).scalar_one_or_none()
    if not existing:
        db.add(RequirementLink(requirement_id=req_id, test_case_id=case_id))
        await db.commit()
    return {"status": "linked"}


@router.delete("/{req_id}/link/{case_id}", status_code=204)
async def unlink_case(req_id: str, case_id: str, db: AsyncSession = Depends(get_db)):
    lk = (await db.execute(
        select(RequirementLink).where(
            RequirementLink.requirement_id == req_id,
            RequirementLink.test_case_id == case_id,
        )
    )).scalar_one_or_none()
    if lk:
        await db.delete(lk)
        await db.commit()
