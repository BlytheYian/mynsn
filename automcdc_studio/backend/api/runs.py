from __future__ import annotations

import asyncio
import datetime
import hashlib
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.database import get_db, new_id
from backend.models.orm import (
    TestCase, TestEnvironment, TestRun, TestResult,
    CoverageSnapshot, VersionRecord,
)
from backend.engine import adapter

router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=4)


class RunRequest(BaseModel):
    env_id: str


# ── 觸發執行 ─────────────────────────────────────────────────────────────────

@router.get("")
async def list_runs(env_id: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(TestRun).where(TestRun.env_id == env_id)
        .order_by(TestRun.started_at.desc())
    )).scalars().all()
    return [{"id": r.id, "status": r.status, "started_at": r.started_at,
             "finished_at": r.finished_at} for r in rows]


@router.post("", status_code=202)
async def create_run(req: RunRequest, background: BackgroundTasks,
                     db: AsyncSession = Depends(get_db)):
    env = await db.get(TestEnvironment, req.env_id)
    if not env:
        raise HTTPException(404, "環境不存在")

    run = TestRun(
        id=new_id(),
        env_id=req.env_id,
        triggered_by="user",
        status="running",
    )
    db.add(run)

    version = _make_version_record(run.id, env.source_path)
    db.add(version)
    await db.commit()

    background.add_task(_execute_run, run.id, req.env_id)
    return {"run_id": run.id}


async def _execute_run(run_id: str, env_id: str):
    from backend.database import SessionLocal
    async with SessionLocal() as db:
        env = await db.get(TestEnvironment, env_id)
        run = await db.get(TestRun, run_id)
        if not env or not run:
            return

        source_code = Path(env.source_path).read_text(encoding="utf-8")
        cases = (await db.execute(
            select(TestCase).where(TestCase.env_id == env_id, TestCase.enabled == True)
        )).scalars().all()

        all_probe_logs: list[list[dict]] = []
        loop = asyncio.get_event_loop()

        for tc in cases:
            try:
                result: adapter.ExecuteResult = await loop.run_in_executor(
                    _executor,
                    lambda tc=tc: adapter.execute_case(
                        source_code=source_code,
                        language=env.language,
                        func_name=env.function_name,
                        domain_types=env.domain_types,
                        domain_bounds=env.domain_bounds,
                        inputs=tc.inputs,
                        expected_output=tc.expected_output,
                    )
                )
                all_probe_logs.append(result.probe_log)
                db.add(TestResult(
                    id=new_id(),
                    run_id=run_id,
                    case_id=tc.id,
                    actual_output=str(result.actual_output) if result.actual_output is not None else None,
                    status=result.status,
                    error_message=result.error_message,
                    execution_time_ms=result.execution_time_ms,
                    trace_log={
                        "frames": result.trace_log,
                        "probe_log": result.probe_log,
                    },
                ))
            except Exception as e:
                db.add(TestResult(
                    id=new_id(),
                    run_id=run_id,
                    case_id=tc.id,
                    status="error",
                    error_message=str(e),
                    execution_time_ms=0.0,
                ))

        # 覆蓋率快照：使用 IFL-MCDC 引擎的 MCDCCoverageEngine 計算（與生成時一致）
        cov_result = await loop.run_in_executor(
            _executor,
            lambda: _compute_engine_coverage(
                source_code, env.language, env.function_name, env.func_signature,
                env.domain_context, env.domain_types, env.domain_bounds,
                env.preceding_direction, all_probe_logs,
            )
        )
        db.add(CoverageSnapshot(
            id=new_id(),
            run_id=run_id,
            mcdc_coverage=cov_result["mcdc_coverage"],
            effective_coverage=cov_result["effective_coverage"],
            covered_pairs=cov_result["covered_pairs"],
            uncovered_pairs=cov_result["uncovered_pairs"],
            infeasible_pairs=cov_result["infeasible_pairs"],
            decision_details=cov_result["decision_details"],
        ))

        run.status = "complete"
        run.finished_at = datetime.datetime.utcnow()
        await db.commit()


def _make_version_record(run_id: str, source_path: str) -> VersionRecord:
    try:
        content = Path(source_path).read_bytes()
        file_hash = hashlib.sha256(content).hexdigest()
    except Exception:
        file_hash = "unknown"

    git_hash = git_msg = None
    try:
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(source_path).parent,
            stderr=subprocess.DEVNULL, timeout=3
        ).decode().strip()
        git_msg = subprocess.check_output(
            ["git", "log", "-1", "--pretty=%s"], cwd=Path(source_path).parent,
            stderr=subprocess.DEVNULL, timeout=3
        ).decode().strip()
    except Exception:
        pass

    return VersionRecord(
        id=new_id(),
        run_id=run_id,
        file_hash=file_hash,
        git_hash=git_hash,
        git_commit_message=git_msg,
    )


def _compute_engine_coverage(
    source_code: str,
    language: str,
    func_name: str,
    func_signature: str,
    domain_context: str,
    domain_types: dict,
    domain_bounds: dict,
    preceding_direction: str,
    all_probe_logs: list[list[dict]],
) -> dict:
    """依 IFL-MCDC 本身的覆蓋率定義計算（與生成時完全一致，包含 Z3 結構性不可行排除）。

    舊版只用 MCDCCoverageEngine.build_matrix() 重播 probe log，但該函式從不呼叫
    mark_infeasible，等於分母永遠包含所有 pair，跟生成時 orchestrator 用 Z3 證明
    UNSAT 後排除分母的計算方式不一致，導致同一份測試套件在「生成完成」與「執行測試」
    顯示出不同的覆蓋率數字。這裡改成重建 orchestrator 並呼叫它真正用的
    _precompute_structural_infeasible，確保兩邊使用同一套定義。
    """
    import sys, tempfile
    from pathlib import Path as _Path
    _root = _Path(__file__).resolve().parents[3]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    from ifl_mcdc.layer1.ast_parser import ASTParser
    from ifl_mcdc.layer1.coverage_engine import MCDCCoverageEngine
    from ifl_mcdc.models.coverage_matrix import MCDCMatrix
    from ifl_mcdc.models.probe_record import ProbeRecord, ProbeLog
    from ifl_mcdc.config import IFLConfig
    from ifl_mcdc.orchestrator import IFLOrchestrator
    from ifl_mcdc.layer3.llm_sampler import MockLLMBackend

    # 重建 ProbeLog
    log = ProbeLog()
    test_ids: list[str] = []
    for case_log in all_probe_logs:
        for r in case_log:
            log.append(ProbeRecord(
                test_id=r["test_id"],
                cond_id=r["cond_id"],
                value=r["value"],
                decision=r["decision"],
            ))
            if r["test_id"] not in test_ids:
                test_ids.append(r["test_id"])

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w",
                                     encoding="utf-8", delete=False) as f:
        f.write(source_code)
        tmp = f.name

    try:
        decision_nodes = ASTParser().parse_file(tmp)
    finally:
        _Path(tmp).unlink(missing_ok=True)

    engine = MCDCCoverageEngine()
    matrices = [MCDCMatrix(condition_set=dn.condition_set) for dn in decision_nodes]
    for matrix in matrices:
        for tid in test_ids:
            engine._update_one(matrix, log, tid)

    # 不需要真的呼叫 LLM，只是借用 orchestrator 內部 Z3 結構性不可行判定，
    # 故用 MockLLMBackend 避免依賴/驗證任何 LLM 設定。
    config = IFLConfig(
        func_name=func_name, func_signature=func_signature,
        domain_context=domain_context, domain_types=domain_types,
        domain_bounds=domain_bounds, preceding_direction=preceding_direction,
        language=language,
    )
    orchestrator = IFLOrchestrator(config, backend=MockLLMBackend([]))
    orchestrator._precompute_structural_infeasible(decision_nodes, matrices)

    covered_all, uncovered_all, infeasible_all = set(), set(), set()
    for dn, matrix in zip(decision_nodes, matrices):
        for cond in dn.condition_set.conditions:
            for flip in ("F2T", "T2F"):
                key = f"{cond.cond_id}_{flip}"
                if (cond.cond_id, flip) in matrix._infeasible:
                    infeasible_all.add(key)
                elif (cond.cond_id, flip) in matrix._covered:
                    covered_all.add(key)
                else:
                    uncovered_all.add(key)

    structural_feasible = sum(m.feasible_count for m in matrices)
    covered_feasible = sum(len(m._covered - m._infeasible) for m in matrices)
    mcdc = covered_feasible / structural_feasible if structural_feasible else 1.0

    return {
        "mcdc_coverage":      mcdc,
        "effective_coverage": mcdc,
        "covered_pairs":      list(covered_all),
        "uncovered_pairs":    list(uncovered_all),
        "infeasible_pairs":   list(infeasible_all),
        "decision_details":   {},
    }


# ── 查詢結果 ─────────────────────────────────────────────────────────────────

@router.get("/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    run = await db.get(TestRun, run_id)
    if not run:
        raise HTTPException(404, "執行紀錄不存在")

    results = (await db.execute(
        select(TestResult).where(TestResult.run_id == run_id)
    )).scalars().all()

    snap = (await db.execute(
        select(CoverageSnapshot).where(CoverageSnapshot.run_id == run_id)
    )).scalar_one_or_none()

    return {
        "run_id": run_id,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "summary": {
            "pass": sum(1 for r in results if r.status == "pass"),
            "fail": sum(1 for r in results if r.status == "fail"),
            "error": sum(1 for r in results if r.status == "error"),
            "coverage_only": sum(1 for r in results if r.status == "coverage_only"),
            "mcdc_coverage": snap.mcdc_coverage if snap else None,
        },
        "results": [
            {
                "case_id": r.case_id,
                "actual_output": r.actual_output,
                "status": r.status,
                "error_message": r.error_message,
                "execution_time_ms": r.execution_time_ms,
            }
            for r in results
        ],
    }


# ── 迴歸比對 ─────────────────────────────────────────────────────────────────

@router.get("/{curr_id}/compare/{base_id}")
async def compare_runs(curr_id: str, base_id: str,
                       db: AsyncSession = Depends(get_db)):
    curr_results = (await db.execute(
        select(TestResult).where(TestResult.run_id == curr_id)
    )).scalars().all()
    base_results = (await db.execute(
        select(TestResult).where(TestResult.run_id == base_id)
    )).scalars().all()

    curr_map = {r.case_id: r.status for r in curr_results}
    base_map = {r.case_id: r.status for r in base_results}

    regressions = [
        {"case_id": cid, "before": base_map[cid], "after": curr_map[cid]}
        for cid in curr_map if cid in base_map
        and base_map[cid] == "pass" and curr_map[cid] == "fail"
    ]
    fixes = [
        {"case_id": cid, "before": base_map[cid], "after": curr_map[cid]}
        for cid in curr_map if cid in base_map
        and base_map[cid] == "fail" and curr_map[cid] == "pass"
    ]

    curr_snap = (await db.execute(
        select(CoverageSnapshot).where(CoverageSnapshot.run_id == curr_id)
    )).scalar_one_or_none()
    base_snap = (await db.execute(
        select(CoverageSnapshot).where(CoverageSnapshot.run_id == base_id)
    )).scalar_one_or_none()

    coverage_delta = None
    if curr_snap and base_snap:
        coverage_delta = curr_snap.mcdc_coverage - base_snap.mcdc_coverage

    return {
        "regressions": regressions,
        "fixes": fixes,
        "coverage_delta": coverage_delta,
    }
