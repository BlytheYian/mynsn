from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from backend.database import get_db, new_id
from backend.models.orm import CoverageSnapshot, ExclusionRecord, TestEnvironment
from backend.engine import adapter

router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=2)


class ExcludeRequest(BaseModel):
    reason: str
    excluded_by: str = "user"


# ── 查詢覆蓋率 ───────────────────────────────────────────────────────────────

@router.get("/environments/{env_id}/coverage")
async def get_coverage(env_id: str, run_id: str | None = None,
                       db: AsyncSession = Depends(get_db)):
    from backend.models.orm import TestRun
    if run_id:
        snap = (await db.execute(
            select(CoverageSnapshot).where(CoverageSnapshot.run_id == run_id)
        )).scalar_one_or_none()
    else:
        snap = (await db.execute(
            select(CoverageSnapshot)
            .join(TestRun, CoverageSnapshot.run_id == TestRun.id)
            .where(TestRun.env_id == env_id)
            .order_by(desc(TestRun.started_at))
            .limit(1)
        )).scalar_one_or_none()

    if not snap:
        return {
            "mcdc_coverage": 0.0,
            "effective_coverage": 0.0,
            "covered_pairs": [],
            "uncovered_pairs": [],
            "infeasible_pairs": [],
            "flip_pair_matrix": {},
        }

    exclusions = (await db.execute(
        select(ExclusionRecord).where(ExclusionRecord.snapshot_id == snap.id)
    )).scalars().all()
    excluded_keys = {f"{e.condition_id}_{e.flip_direction}" for e in exclusions}

    return {
        "snapshot_id": snap.id,
        "mcdc_coverage": snap.mcdc_coverage,
        "effective_coverage": snap.effective_coverage,
        "covered_pairs": snap.covered_pairs,
        "uncovered_pairs": [p for p in snap.uncovered_pairs if p not in excluded_keys],
        "infeasible_pairs": snap.infeasible_pairs,
        "excluded_pairs": list(excluded_keys),
        "flip_pair_matrix": snap.decision_details,
    }


# ── 趨勢統計 ─────────────────────────────────────────────────────────────────

@router.get("/environments/{env_id}/trends")
async def get_trends(env_id: str, db: AsyncSession = Depends(get_db)):
    from backend.models.orm import TestRun
    snaps = (await db.execute(
        select(CoverageSnapshot)
        .join(TestRun, CoverageSnapshot.run_id == TestRun.id)
        .where(TestRun.env_id == env_id)
        .order_by(TestRun.started_at)
    )).scalars().all()

    return {
        "coverage_trend": [
            {"run_id": s.run_id, "mcdc_coverage": s.mcdc_coverage}
            for s in snaps
        ],
    }


# ── 缺口 Z3 建議 ─────────────────────────────────────────────────────────────

@router.get("/environments/{env_id}/gaps/{cond_id}/{flip}/hint")
async def get_gap_hint(env_id: str, cond_id: str, flip: str,
                       db: AsyncSession = Depends(get_db)):
    env = await db.get(TestEnvironment, env_id)
    if not env:
        raise HTTPException(404, "環境不存在")

    from pathlib import Path
    source_code = Path(env.source_path).read_text(encoding="utf-8")
    loop = asyncio.get_event_loop()

    try:
        hint: adapter.GapHint = await loop.run_in_executor(
            _executor,
            lambda: adapter.analyze_gap(
                source_code=source_code,
                func_name=env.function_name,
                domain_types=env.domain_types,
                domain_bounds=env.domain_bounds,
                condition_id=cond_id,
                flip_direction=flip,
            )
        )
        return {
            "condition_id": hint.condition_id,
            "flip_direction": hint.flip_direction,
            "z3_hint": hint.z3_hint,
            "bound_specs": hint.bound_specs,
        }
    except Exception as e:
        raise HTTPException(500, str(e))


# ── 覆蓋 pair 詳情 & 候選案例 ────────────────────────────────────────────────

@router.get("/environments/{env_id}/pairs/{cond_id}/{flip}")
async def get_pair_detail(env_id: str, cond_id: str, flip: str,
                          run_id: str | None = None,
                          db: AsyncSession = Depends(get_db)):
    """
    O 已覆蓋：回傳實際形成 MC/DC pair 的兩個測試案例
    X 未覆蓋：回傳條件值符合目標的候選案例
    """
    from backend.models.orm import TestRun, TestResult, TestCase
    from sqlalchemy import select, desc

    key = f"{cond_id}_{flip}"

    # ── 取最新快照（含 gap_map）──────────────────────────────────────────────
    snap = (await db.execute(
        select(CoverageSnapshot)
        .join(TestRun, CoverageSnapshot.run_id == TestRun.id)
        .where(TestRun.env_id == env_id)
        .order_by(desc(TestRun.started_at))
        .limit(1)
    )).scalar_one_or_none()

    cases = (await db.execute(
        select(TestCase).where(TestCase.env_id == env_id)
    )).scalars().all()
    case_map = {c.id: c.inputs for c in cases}

    # ── 統一用 probe_log，scoped 到指定 run ────────────────────────────────
    if run_id:
        latest_run = await db.get(TestRun, run_id)
    else:
        latest_run = (await db.execute(
            select(TestRun)
            .where(TestRun.env_id == env_id)
            .order_by(desc(TestRun.started_at))
            .limit(1)
        )).scalar_one_or_none()

    test_cond_map: dict[str, dict] = {}

    if latest_run:
        results = (await db.execute(
            select(TestResult).where(TestResult.run_id == latest_run.id)
        )).scalars().all()
        for r in results:
            probe_log = (r.trace_log or {}).get("probe_log", [])
            if probe_log:
                test_cond_map[r.case_id] = {p["cond_id"]: p for p in probe_log}

    all_cond_ids: set[str] = set()
    for vals in test_cond_map.values():
        all_cond_ids.update(vals.keys())

    covering_pairs: list[dict] = []
    test_ids = list(test_cond_map.keys())
    for i, id_a in enumerate(test_ids):
        map_a = test_cond_map[id_a]
        rec_a = map_a.get(cond_id)
        if not rec_a:
            continue
        for id_b in test_ids[i+1:]:
            map_b = test_cond_map[id_b]
            rec_b = map_b.get(cond_id)
            if not rec_b or rec_a["value"] == rec_b["value"]:
                continue
            if rec_a["decision"] == rec_b["decision"]:
                continue
            others_ok = all(
                map_a.get(o, {}).get("value") == map_b.get(o, {}).get("value")
                for o in all_cond_ids if o != cond_id
            )
            if others_ok:
                pair_flip = "F2T" if (not rec_a["value"] and rec_b["value"]) else "T2F"
                covering_pairs.append({
                    "case_a": {"id": id_a, "inputs": case_map.get(id_a, {})},
                    "case_b": {"id": id_b, "inputs": case_map.get(id_b, {})},
                    "flip": pair_flip,
                })

    pair_for_flip = [p for p in covering_pairs if p["flip"] == flip]

    # 候選案例：依條件值分成兩側，各取最多 3 個
    # F2T：False side（條件=False）+ True side（條件=True）
    # T2F：True side（條件=True） + False side（條件=False）
    false_side = next((
        {"id": cid, "inputs": case_map.get(cid, {}),
         "decision": cond_vals.get(cond_id, {}).get("decision")}
        for cid, cond_vals in test_cond_map.items()
        if cond_vals.get(cond_id, {}).get("value") is False
    ), None)
    true_side = next((
        {"id": cid, "inputs": case_map.get(cid, {}),
         "decision": cond_vals.get(cond_id, {}).get("decision")}
        for cid, cond_vals in test_cond_map.items()
        if cond_vals.get(cond_id, {}).get("value") is True
    ), None)
    candidates = {
        "false_side": [false_side] if false_side else [],
        "true_side":  [true_side]  if true_side  else [],
    }

    status = "covered" if pair_for_flip else "uncovered"
    if not test_cond_map:
        status = "no_data"

    return {
        "key": key,
        "status": status,
        "pairs":      pair_for_flip[:3],
        "candidates": candidates,
    }


# ── 排除缺口 ─────────────────────────────────────────────────────────────────

@router.post("/gaps/{cond_id}/{flip}/exclude", status_code=200)
async def exclude_gap(cond_id: str, flip: str, req: ExcludeRequest,
                      snapshot_id: str,
                      db: AsyncSession = Depends(get_db)):
    snap = await db.get(CoverageSnapshot, snapshot_id)
    if not snap:
        raise HTTPException(404, "快照不存在")

    record = ExclusionRecord(
        id=new_id(),
        snapshot_id=snapshot_id,
        condition_id=cond_id,
        flip_direction=flip,
        reason=req.reason,
        excluded_by=req.excluded_by,
    )
    db.add(record)

    # 更新 uncovered_pairs（從分母移除）
    key = f"{cond_id}_{flip}"
    snap.uncovered_pairs = [p for p in snap.uncovered_pairs if p != key]
    total_feasible = len(snap.covered_pairs) + len(snap.uncovered_pairs)
    snap.effective_coverage = (
        len(snap.covered_pairs) / total_feasible if total_feasible else 1.0
    )

    await db.commit()
    return {"excluded": key, "new_effective_coverage": snap.effective_coverage}


# ── 不可行路徑正當性說明 ──────────────────────────────────────────────────────

class JustifyRequest(BaseModel):
    justification: str
    signed_by: str = "user"


@router.post("/gaps/{cond_id}/{flip}/justify", status_code=200)
async def justify_infeasible(cond_id: str, flip: str, req: JustifyRequest,
                             snapshot_id: str,
                             db: AsyncSession = Depends(get_db)):
    from backend.models.orm import InfeasibleJustification
    snap = await db.get(CoverageSnapshot, snapshot_id)
    if not snap:
        raise HTTPException(404, "快照不存在")

    record = InfeasibleJustification(
        id=new_id(),
        snapshot_id=snapshot_id,
        condition_id=cond_id,
        flip_direction=flip,
        z3_proof=None,
        justification=req.justification,
        signed_by=req.signed_by,
    )
    db.add(record)
    await db.commit()
    return {"status": "justified"}
