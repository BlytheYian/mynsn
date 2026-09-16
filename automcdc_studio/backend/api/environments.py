from __future__ import annotations

import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.database import get_db, new_id
from backend.models.orm import (
    TestEnvironment, TestCase, CoverageSnapshot,
    StubRule, ParameterConstraint, LLMConfig, TestResult,
)
from backend.engine import adapter

router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=4)

# 原始碼需長期保存，不能放在系統暫存目錄（重開機/磁碟清理會被清掉），
# 改存在專案內固定目錄，並以環境 id 命名，方便往後直接覆寫。
_SOURCE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "sources"
_SOURCE_DIR.mkdir(parents=True, exist_ok=True)


# ── Schemas ──────────────────────────────────────────────────────────────────

class ParseRequest(BaseModel):
    source_code: str
    language: str = "python"


class EnvironmentCreate(BaseModel):
    name: str
    language: str
    source_code: str
    function_name: str
    func_signature: str
    domain_context: str = ""
    domain_types: dict[str, str] = {}
    domain_bounds: dict[str, list[int]] = {}
    preceding_direction: str = "sequential"
    min_initial_random: int = 6


class GenerateRequest(BaseModel):
    max_iterations: int = 50
    target_decision: str | None = None


class StubCreate(BaseModel):
    call_site: str
    strategy: str = "fixed"
    return_value: str | None = None
    return_sequence: list | None = None
    exception_type: str | None = None
    exception_message: str | None = None


class ConstraintCreate(BaseModel):
    description: str
    z3_expression: str
    created_by: str = "user"


class SuggestBoundsRequest(BaseModel):
    description: str


class SuggestBoundsPreviewRequest(BaseModel):
    source_code: str
    language: str = "python"
    domain_types: dict[str, str] = {}
    domain_context: str = ""
    description: str = ""


@router.post("/suggest-bounds")
async def suggest_bounds_preview(req: SuggestBoundsPreviewRequest,
                                  db: AsyncSession = Depends(get_db)):
    """建立環境前呼叫，不需要 envId，直接使用表單的 source_code 與 types。"""
    llm_cfg = (await db.execute(select(LLMConfig))).scalar_one_or_none()

    # 先用 AST 分析門檻值
    bounds = adapter.suggest_bounds_from_ast(
        req.source_code, req.language, req.domain_types
    )

    # 若有 LLM 設定，再用 LLM 補強（覆蓋 AST 建議）
    if llm_cfg:
        params = "\n".join(f"- {k}: {v}" for k, v in req.domain_types.items())
        prompt = f"""根據以下函數參數和語境，為每個 int/float 參數推薦合理的值域範圍。
以 JSON 格式輸出，只輸出 JSON，格式：{{"param_name": [min, max]}}

參數：
{params}

語境：{req.domain_context}
補充說明：{req.description}
"""
        loop = asyncio.get_event_loop()

        def _call():
            from ifl_mcdc.config import IFLConfig
            config = IFLConfig(
                llm_provider=llm_cfg.provider,
                llm_model=llm_cfg.model,
                llm_api_key=llm_cfg.api_key_encrypted,
                llm_base_url=llm_cfg.base_url,
                func_name="suggest", func_signature="suggest()", domain_types={},
            )
            return config.llm_backend.complete(prompt, max_tokens=256)

        try:
            import json
            raw = await asyncio.wait_for(loop.run_in_executor(_executor, _call), timeout=90.0)
            raw = raw.strip()
            s, e = raw.find('{'), raw.rfind('}')
            if s != -1 and e != -1:
                llm_bounds = json.loads(raw[s:e+1])
                # 合併：取較小的下界、較大的上界，確保 AST 門檻值一定在範圍內
                for name, llm_range in llm_bounds.items():
                    if not isinstance(llm_range, list) or len(llm_range) < 2:
                        continue
                    if name in bounds:
                        ast_lo, ast_hi = bounds[name]
                        bounds[name] = [
                            min(ast_lo, llm_range[0]),
                            max(ast_hi, llm_range[1]),
                        ]
                    else:
                        bounds[name] = llm_range
        except Exception:
            pass  # LLM 失敗時使用 AST 建議

    return {"suggested_bounds": bounds}


class GenCodeRequest(BaseModel):
    description: str
    language: str = "python"


# ── 解析原始碼 ───────────────────────────────────────────────────────────────

@router.post("/parse")
async def parse_source(req: ParseRequest):
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            _executor, adapter.parse_source, req.source_code, req.language
        )
        # 自動從 AST 推薦值域（合併所有函數的 param_types）
        all_types: dict[str, str] = {}
        for types in result.param_types.values():
            all_types.update(types)
        suggested = adapter.suggest_bounds_from_ast(req.source_code, req.language, all_types)
        return {
            "functions":       result.functions,
            "param_types":     result.param_types,
            "decision_count":  result.decision_count,
            "suggested_bounds": suggested,
        }
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))


# ── UC-08：自然語言生成程式碼 ─────────────────────────────────────────────────

@router.post("/gen-code")
async def gen_code_from_nl(req: GenCodeRequest, db: AsyncSession = Depends(get_db)):
    """以自然語言描述生成被測函數程式碼，使用已儲存的 LLM 設定。"""
    llm_cfg = (await db.execute(select(LLMConfig))).scalar_one_or_none()
    if not llm_cfg:
        raise HTTPException(400, "請先至 LLM 設定頁儲存 API key 與模型設定")

    prompt = f"""請根據以下描述，生成一個 {req.language} 函數。
只輸出函數程式碼，不要說明、不要 markdown。

描述：
{req.description}
"""
    loop = asyncio.get_event_loop()

    def _call_llm():
        from ifl_mcdc.config import IFLConfig
        config = IFLConfig(
            llm_provider=llm_cfg.provider,
            llm_model=llm_cfg.model,
            llm_api_key=llm_cfg.api_key_encrypted,
            llm_base_url=llm_cfg.base_url,
            func_name="generated",
            func_signature="generated()",
            domain_types={},
        )
        return config.llm_backend.complete(prompt, max_tokens=512)

    try:
        code = await loop.run_in_executor(_executor, _call_llm)
        code = code.strip().strip("```python").strip("```").strip()
        return {"code": code}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_environment(req: EnvironmentCreate, db: AsyncSession = Depends(get_db)):
    source_hash = hashlib.sha256(req.source_code.encode()).hexdigest()
    env_id = new_id()
    source_path = _SOURCE_DIR / f"{env_id}.py"
    source_path.write_text(req.source_code, encoding="utf-8")

    env = TestEnvironment(
        id=env_id, name=req.name, language=req.language,
        source_path=str(source_path), source_hash=source_hash,
        function_name=req.function_name, func_signature=req.func_signature,
        domain_context=req.domain_context,
        domain_types=req.domain_types, domain_bounds=req.domain_bounds,
        preceding_direction=req.preceding_direction,
        min_initial_random=req.min_initial_random,
    )
    db.add(env)
    await db.commit()
    await db.refresh(env)
    return {"id": env.id, "name": env.name, "func_signature": env.func_signature}


@router.get("")
async def list_environments(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(TestEnvironment))).scalars().all()
    return [{"id": e.id, "name": e.name, "language": e.language,
             "function_name": e.function_name, "created_at": e.created_at} for e in rows]


@router.get("/{env_id}")
async def get_environment(env_id: str, db: AsyncSession = Depends(get_db)):
    env = await db.get(TestEnvironment, env_id)
    if not env:
        raise HTTPException(404, "環境不存在")
    return env


@router.put("/{env_id}")
async def update_environment(env_id: str, req: EnvironmentCreate,
                             db: AsyncSession = Depends(get_db)):
    env = await db.get(TestEnvironment, env_id)
    if not env:
        raise HTTPException(404, "環境不存在")
    source_hash = hashlib.sha256(req.source_code.encode()).hexdigest()
    source_path = _SOURCE_DIR / f"{env.id}.py"
    source_path.write_text(req.source_code, encoding="utf-8")
    env.source_path = str(source_path)
    env.name               = req.name
    env.language           = req.language
    env.source_hash        = source_hash
    env.function_name      = req.function_name
    env.func_signature     = req.func_signature
    env.domain_context     = req.domain_context
    env.domain_types       = req.domain_types
    env.domain_bounds      = req.domain_bounds
    env.preceding_direction = req.preceding_direction
    env.min_initial_random  = req.min_initial_random
    await db.commit()
    return {"id": env.id, "name": env.name}


@router.get("/{env_id}/source")
async def get_source(env_id: str, db: AsyncSession = Depends(get_db)):
    env = await db.get(TestEnvironment, env_id)
    if not env:
        raise HTTPException(404)
    try:
        src = Path(env.source_path).read_text(encoding="utf-8")
    except Exception:
        src = ""
    return {"source_code": src}


@router.delete("/{env_id}", status_code=204)
async def delete_environment(env_id: str, db: AsyncSession = Depends(get_db)):
    env = await db.get(TestEnvironment, env_id)
    if not env:
        raise HTTPException(404, "環境不存在")
    await db.delete(env)
    await db.commit()


# ── UC-02：CFG ────────────────────────────────────────────────────────────────

@router.get("/{env_id}/cfg")
async def get_cfg(env_id: str, db: AsyncSession = Depends(get_db)):
    env = await db.get(TestEnvironment, env_id)
    if not env:
        raise HTTPException(404, "環境不存在")
    source_code = Path(env.source_path).read_text(encoding="utf-8")
    loop = asyncio.get_event_loop()
    try:
        nodes = await loop.run_in_executor(
            _executor, adapter.parse_cfg, source_code, env.language
        )
        return {"nodes": nodes}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── UC-04：LLM 推薦邊界值 ─────────────────────────────────────────────────────

@router.post("/{env_id}/suggest-bounds")
async def suggest_bounds(env_id: str, req: SuggestBoundsRequest,
                         db: AsyncSession = Depends(get_db)):
    env = await db.get(TestEnvironment, env_id)
    if not env:
        raise HTTPException(404, "環境不存在")

    llm_cfg = (await db.execute(select(LLMConfig))).scalar_one_or_none()
    if not llm_cfg:
        raise HTTPException(400, "請先設定 LLM")

    params = "\n".join(f"- {k}: {v}" for k, v in env.domain_types.items())
    prompt = f"""根據以下函數參數和語境，為每個 int/float 參數推薦合理的值域範圍。
以 JSON 格式輸出，只輸出 JSON，格式：{{"param_name": [min, max]}}

參數：
{params}

語境：{env.domain_context}
補充說明：{req.description}
"""
    import json
    loop = asyncio.get_event_loop()

    def _call():
        from ifl_mcdc.config import IFLConfig
        config = IFLConfig(
            llm_provider=llm_cfg.provider,
            llm_model=llm_cfg.model,
            llm_api_key=llm_cfg.api_key_encrypted,
            llm_base_url=llm_cfg.base_url,
            func_name="suggest",
            func_signature="suggest()",
            domain_types={},
        )
        return config.llm_backend.complete(prompt, max_tokens=256)

    try:
        raw = await asyncio.wait_for(
            loop.run_in_executor(_executor, _call),
            timeout=90.0,
        )
        raw = raw.strip()
        start, end = raw.find('{'), raw.rfind('}')
        bounds = json.loads(raw[start:end+1]) if start != -1 and end != -1 else {}
    except (asyncio.TimeoutError, Exception):
        bounds = {}

    # 若 LLM 未能回傳，用啟發式規則補全
    for name, typ in env.domain_types.items():
        if typ == "bool" or name in bounds:
            continue
        nl = name.lower()
        if any(k in nl for k in ("age", "yr", "year")):
            bounds[name] = [0, 130]
        elif any(k in nl for k in ("alt", "altitude", "sep", "separation")):
            bounds[name] = [0, 10000]
        elif any(k in nl for k in ("rate", "speed", "velocity")):
            bounds[name] = [-1000, 1000]
        elif any(k in nl for k in ("day", "time", "hour")):
            bounds[name] = [0, 3650]
        elif any(k in nl for k in ("thresh", "limit", "max", "min")):
            bounds[name] = [0, 1000]
        else:
            bounds[name] = [0, 100]

    return {"suggested_bounds": bounds}


# ── UC-03：Stub / Mock ────────────────────────────────────────────────────────

@router.get("/{env_id}/stubs")
async def list_stubs(env_id: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(StubRule).where(StubRule.env_id == env_id)
    )).scalars().all()
    return [{"id": s.id, "call_site": s.call_site, "strategy": s.strategy,
             "return_value": s.return_value, "exception_type": s.exception_type} for s in rows]


@router.post("/{env_id}/stubs", status_code=201)
async def create_stub(env_id: str, req: StubCreate, db: AsyncSession = Depends(get_db)):
    stub = StubRule(
        id=new_id(), env_id=env_id,
        call_site=req.call_site, strategy=req.strategy,
        return_value=req.return_value, return_sequence=req.return_sequence,
        exception_type=req.exception_type, exception_message=req.exception_message,
    )
    db.add(stub)
    await db.commit()
    return {"id": stub.id}


@router.delete("/{env_id}/stubs/{stub_id}", status_code=204)
async def delete_stub(env_id: str, stub_id: str, db: AsyncSession = Depends(get_db)):
    stub = await db.get(StubRule, stub_id)
    if not stub:
        raise HTTPException(404)
    await db.delete(stub)
    await db.commit()


# ── UC-05：參數間約束 ──────────────────────────────────────────────────────────

@router.get("/{env_id}/constraints")
async def list_constraints(env_id: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(ParameterConstraint).where(ParameterConstraint.env_id == env_id)
    )).scalars().all()
    return [{"id": c.id, "description": c.description,
             "z3_expression": c.z3_expression, "created_by": c.created_by} for c in rows]


@router.post("/{env_id}/constraints", status_code=201)
async def create_constraint(env_id: str, req: ConstraintCreate,
                            db: AsyncSession = Depends(get_db)):
    c = ParameterConstraint(
        id=new_id(), env_id=env_id,
        description=req.description, z3_expression=req.z3_expression,
        created_by=req.created_by,
    )
    db.add(c)
    await db.commit()
    return {"id": c.id}


@router.delete("/{env_id}/constraints/{cid}", status_code=204)
async def delete_constraint(env_id: str, cid: str, db: AsyncSession = Depends(get_db)):
    c = await db.get(ParameterConstraint, cid)
    if not c:
        raise HTTPException(404)
    await db.delete(c)
    await db.commit()


# ── UC-06：自動生成（WebSocket） ────────────────────────────────────────────────

@router.websocket("/{env_id}/generate")
async def generate_ws(env_id: str, ws: WebSocket, db: AsyncSession = Depends(get_db)):
    await ws.accept()
    env = await db.get(TestEnvironment, env_id)
    if not env:
        await ws.send_json({"error": "環境不存在"})
        await ws.close()
        return

    try:
        data = await ws.receive_json()
        req = GenerateRequest(**data)
    except Exception:
        req = GenerateRequest()

    llm_cfg = (await db.execute(select(LLMConfig))).scalar_one_or_none()
    if not llm_cfg:
        await ws.send_json({"type": "error", "detail": "請先至 LLM 設定頁儲存模型設定"})
        await ws.close()
        return

    source_code = Path(env.source_path).read_text(encoding="utf-8")
    loop = asyncio.get_event_loop()
    progress_queue: asyncio.Queue = asyncio.Queue()

    def _progress_cb(info: dict):
        asyncio.run_coroutine_threadsafe(progress_queue.put(info), loop)

    async def _run():
        return await loop.run_in_executor(
            _executor,
            lambda: adapter.run_generation(
                source_code=source_code, language=env.language,
                func_name=env.function_name, func_signature=env.func_signature,
                domain_context=env.domain_context,
                domain_types=env.domain_types, domain_bounds=env.domain_bounds,
                max_iterations=req.max_iterations,
                llm_provider=llm_cfg.provider, llm_model=llm_cfg.model,
                llm_api_key=llm_cfg.api_key_encrypted,
                llm_base_url=llm_cfg.base_url,
                preceding_direction=env.preceding_direction,
                min_initial_random=env.min_initial_random,
                progress_callback=_progress_cb,
            )
        )

    gen_task = asyncio.create_task(_run())

    try:
        while not gen_task.done():
            try:
                info = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                await ws.send_json({"type": "progress", **info})
            except asyncio.TimeoutError:
                continue

        ifl_result = gen_task.result()

        import datetime
        from backend.models.orm import TestRun

        # 建立對應的 TestRun，讓覆蓋率查詢可以 JOIN
        gen_run = TestRun(
            id=new_id(), env_id=env_id,
            triggered_by="generation",
            status="complete",
            started_at=datetime.datetime.utcnow(),
            finished_at=datetime.datetime.utcnow(),
        )
        db.add(gen_run)
        await db.flush()   # 取得 gen_run.id

        # 建立 orch_test_id → case_id 的映射，供 probe_log 儲存使用
        case_id_map: dict[str, str] = {}
        for tc in ifl_result.test_suite:
            orch_tid = tc.get("__test_id", "")
            case_id  = new_id()
            case_id_map[orch_tid] = case_id
            inputs = {k: v for k, v in tc.items() if not k.startswith("__")}
            db.add(TestCase(
                id=case_id, env_id=env_id,
                name=f"auto_{orch_tid}",
                created_by="auto", inputs=inputs,
                expected_output=None, expected_source="none",
            ))

        # 把 probe_records 依 orch_test_id 分組，存入 TestResult
        probe_by_tid: dict[str, list[dict]] = {}
        for rec in ifl_result.probe_records:
            probe_by_tid.setdefault(rec["test_id"], []).append(rec)

        for orch_tid, case_id in case_id_map.items():
            db.add(TestResult(
                id=new_id(),
                run_id=gen_run.id,
                case_id=case_id,
                actual_output=None,
                status="coverage_only",
                error_message=None,
                execution_time_ms=0.0,
                trace_log={"probe_log": probe_by_tid.get(orch_tid, [])},
            ))

        snap = CoverageSnapshot(
            id=new_id(), run_id=gen_run.id,
            mcdc_coverage=ifl_result.final_coverage,
            effective_coverage=ifl_result.final_coverage,
            covered_pairs=[k for k, v in ifl_result.gap_coverage_map.items()
                           if v.get("status") == "covered"],
            uncovered_pairs=[k for k, v in ifl_result.gap_coverage_map.items()
                             if v.get("status") == "uncovered"],
            infeasible_pairs=[
                f"{cid}_{flip}"
                for cid in ifl_result.infeasible_paths
                for flip in ("F2T", "T2F")
            ],
            decision_details={
                "truth_tables": ifl_result.decision_truth_tables,
                "gap_map": ifl_result.gap_coverage_map,
            },
        )
        db.add(snap)
        await db.commit()

        llm_failures = [f for f in ifl_result.failure_log if f.startswith("LLM_FAIL")]
        await ws.send_json({
            "type": "complete",
            "final_coverage": ifl_result.final_coverage,
            "case_count": len(ifl_result.test_suite),
            "converged": ifl_result.converged,
            "llm_failed": len(llm_failures) > 0,
            "llm_error": llm_failures[0][9:] if llm_failures else None,
            "failure_log": ifl_result.failure_log[:10],
        })
    except WebSocketDisconnect:
        gen_task.cancel()
    except Exception as e:
        await ws.send_json({"type": "error", "detail": str(e)})
    finally:
        await ws.close()
