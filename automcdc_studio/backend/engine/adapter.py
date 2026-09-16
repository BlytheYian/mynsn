"""
IFL-MCDC 引擎包裝層。

將 ifl_mcdc 的內部介面轉換為系統 API 層所需的簡單呼叫介面。
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import time
import traceback
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# 將 ifl_mcdc 的父目錄加入 sys.path，確保可以 import
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ifl_mcdc.config import IFLConfig
from ifl_mcdc.layer1.ast_parser import ASTParser
from ifl_mcdc.layer1.coverage_engine import MCDCCoverageEngine
from ifl_mcdc.layer1.probe_injector import ProbeInjector
from ifl_mcdc.layer2.gap_analyzer import GapAnalyzer
from ifl_mcdc.layer2.smt_synthesizer import SMTConstraintSynthesizer
from ifl_mcdc.models.probe_record import ProbeLog
from ifl_mcdc.orchestrator import IFLOrchestrator, IFLResult


@dataclass
class ParseResult:
    functions: list[str]
    param_types: dict[str, dict[str, str]]   # func_name → {param: type}
    decision_count: int


@dataclass
class ExecuteResult:
    actual_output: Any
    status: str                              # "pass" | "fail" | "error" | "coverage_only"
    error_message: str | None
    execution_time_ms: float
    probe_log: list[dict]                    # 序列化的 ProbeRecord 清單
    trace_log: list[dict]                    # sys.settrace 局部變量快照


@dataclass
class GapHint:
    condition_id: str
    flip_direction: str
    z3_hint: str
    bound_specs: list[dict]


# ── 從 AST 自動推薦值域 ────────────────────────────────────────────────────────

def suggest_bounds_from_ast(source_code: str, language: str,
                             domain_types: dict[str, str]) -> dict[str, list[int]]:
    """分析條件中的比較門檻值，自動推薦合理的參數值域。

    例：credit_score >= 700 → 建議 credit_score ∈ [0, 1050]（門檻的 1.5 倍）
    """
    if language == "c":
        return {}

    import ast as _ast

    try:
        tree = _ast.parse(source_code)
    except SyntaxError:
        return {}

    # 收集每個變數名出現的比較門檻值
    thresholds: dict[str, list[float]] = {}

    class _Visitor(_ast.NodeVisitor):
        def visit_Compare(self, node):
            # 處理 a >= N、a > N、a <= N、a < N
            if isinstance(node.left, _ast.Name):
                name = node.left.id
                for comp, val in zip(node.ops, node.comparators):
                    if isinstance(val, _ast.Constant) and isinstance(val.value, (int, float)):
                        thresholds.setdefault(name, []).append(float(val.value))
            # 處理 N >= a（反向）
            for comp, val in zip(node.ops, node.comparators):
                if isinstance(val, _ast.Name):
                    name = val.id
                    if isinstance(node.left, _ast.Constant) and isinstance(node.left.value, (int, float)):
                        thresholds.setdefault(name, []).append(float(node.left.value))
            self.generic_visit(node)

    _Visitor().visit(tree)

    bounds = {}
    for name, typ in domain_types.items():
        if typ == "bool":
            continue
        vals = thresholds.get(name, [])
        if not vals:
            # 無門檻，用啟發式名稱規則
            nl = name.lower()
            if any(k in nl for k in ("age", "yr")):
                bounds[name] = [0, 130]
            elif any(k in nl for k in ("day", "time")):
                bounds[name] = [0, 3650]
            elif any(k in nl for k in ("alt", "sep", "altitude")):
                bounds[name] = [0, 10000]
            elif any(k in nl for k in ("rate", "speed")):
                bounds[name] = [-1000, 1000]
            else:
                bounds[name] = [0, 100]
        else:
            max_thresh = max(vals)
            min_thresh = min(vals)
            lo = max(0, int(min_thresh * 0.5)) if min_thresh > 0 else int(min_thresh * 1.5)
            hi = int(max_thresh * 1.5)
            bounds[name] = [lo, hi]

    return bounds


# ── CFG 詳細解析 ─────────────────────────────────────────────────────────────

def parse_cfg(source_code: str, language: str) -> list[dict]:
    """解析原始碼，回傳完整決策節點結構（CFG + 條件樹）。"""
    if language == "c":
        raise NotImplementedError("C 語言解析尚未整合")

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w",
                                     encoding="utf-8", delete=False) as f:
        f.write(source_code)
        tmp_path = f.name

    try:
        parser = ASTParser()
        decision_nodes = parser.parse_file(tmp_path)
        result = []
        for dn in decision_nodes:
            conditions = [
                {
                    "cond_id":    c.cond_id,
                    "expression": c.expression,
                    "var_names":  c.var_names,
                    "negated":    c.negated,
                }
                for c in dn.condition_set.conditions
            ]
            k = dn.condition_set.k
            coupling = [
                [dn.condition_set.coupling_matrix[i][j] for j in range(k)]
                for i in range(k)
            ]
            result.append({
                "node_id":        dn.node_id,
                "node_type":      dn.node_type,
                "line_no":        dn.line_no,
                "expression":     dn.expression_str,
                "conditions":     conditions,
                "coupling_matrix": coupling,
                "k":              k,
            })
        return result
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ── 解析 ────────────────────────────────────────────────────────────────────

def parse_source(source_code: str, language: str) -> ParseResult:
    """解析原始碼，回傳可測試函數清單與參數型別。"""
    if language == "c":
        raise NotImplementedError("C 語言解析尚未整合（需要 libclang）")

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w",
                                     encoding="utf-8", delete=False) as f:
        f.write(source_code)
        tmp_path = f.name

    try:
        parser = ASTParser()
        decision_nodes = parser.parse_file(tmp_path)

        func_names: list[str] = []
        param_types: dict[str, dict[str, str]] = {}

        for dn in decision_nodes:
            fname = _extract_func_name(tmp_path, dn.line_no)
            if fname and fname not in func_names:
                func_names.append(fname)
                param_types[fname] = _infer_param_types(tmp_path, fname)

        return ParseResult(
            functions=func_names,
            param_types=param_types,
            decision_count=len(decision_nodes),
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _extract_func_name(path: str, line_no: int) -> str | None:
    """從原始碼中找到指定行號所屬的函數名稱。"""
    import ast
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                end = getattr(node, "end_lineno", node.lineno + 100)
                if node.lineno <= line_no <= end:
                    return node.name
    except Exception:
        pass
    return None


def _infer_param_types(path: str, func_name: str) -> dict[str, str]:
    """從函數的型別註解或預設值推斷參數型別。"""
    import ast
    result: dict[str, str] = {}
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                for arg in node.args.args:
                    ann = arg.annotation
                    if ann is None:
                        result[arg.arg] = "int"
                    elif isinstance(ann, ast.Name):
                        result[arg.arg] = ann.id.lower()
                    else:
                        result[arg.arg] = "int"
    except Exception:
        pass
    return result


# ── 生成 ────────────────────────────────────────────────────────────────────

def run_generation(
    source_code: str,
    language: str,
    func_name: str,
    func_signature: str,
    domain_context: str,
    domain_types: dict[str, str],
    domain_bounds: dict[str, list[int]],
    max_iterations: int,
    llm_provider: str,
    llm_model: str,
    llm_api_key: str,
    llm_base_url: str = "http://localhost:11434",
    preceding_direction: str = "sequential",
    min_initial_random: int = 6,
    progress_callback: Callable[[dict], None] | None = None,
) -> IFLResult:
    """啟動 IFL-MCDC 引擎，生成測試向量。

    progress_callback 每次迭代完成後被呼叫，傳入 {"coverage": float, "iter": int}。
    """
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w",
                                     encoding="utf-8", delete=False) as f:
        f.write(source_code)
        tmp_path = f.name

    try:
        import os
        os.environ.setdefault("IFL_LLM_API_KEY", llm_api_key)

        config = IFLConfig(
            func_name=func_name,
            func_signature=func_signature,
            domain_context=domain_context,
            domain_types=domain_types,
            domain_bounds=domain_bounds,
            max_iterations=max_iterations,
            llm_provider=llm_provider,
            llm_model=llm_model,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            language=language,
            preceding_direction=preceding_direction,
            min_initial_random=min_initial_random,
        )

        # 若有進度回呼，包裝 orchestrator 的迭代
        if progress_callback:
            orchestrator = _ProgressOrchestrator(config, progress_callback)
        else:
            orchestrator = IFLOrchestrator(config)

        return orchestrator.run(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


class _ProgressOrchestrator(IFLOrchestrator):
    """繼承 IFLOrchestrator，在每次迭代後呼叫進度回呼。"""

    def __init__(self, config: IFLConfig, cb: Callable[[dict], None]) -> None:
        super().__init__(config)
        self._cb = cb
        self._iter = 0

    def _run_test(self, module, test_case, log):
        result = super()._run_test(module, test_case, log)
        self._iter += 1
        self._cb({"iter": self._iter, "latest_case": test_case})
        return result


# ── 執行 ────────────────────────────────────────────────────────────────────

def execute_case(
    source_code: str,
    language: str,
    func_name: str,
    domain_types: dict[str, str],
    domain_bounds: dict[str, list[int]],
    inputs: dict[str, Any],
    expected_output: Any | None = None,
) -> ExecuteResult:
    """以給定輸入執行被測函數，回傳實際輸出與覆蓋率探針紀錄。"""
    if language == "c":
        raise NotImplementedError("C 語言執行尚未整合")

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w",
                                     encoding="utf-8", delete=False) as f:
        f.write(source_code)
        tmp_path = f.name

    try:
        parser = ASTParser()
        decision_nodes = parser.parse_file(tmp_path)
        injector = ProbeInjector(decision_nodes)
        instrumented = injector.inject(source_code)

        module_name = f"_exec_{hashlib.md5(source_code.encode()).hexdigest()[:8]}"
        mod = types.ModuleType(module_name)
        exec(compile(instrumented, module_name, "exec"), mod.__dict__)  # noqa: S102
        sys.modules[module_name] = mod

        log = ProbeLog()
        import ifl_mcdc.layer1.probe_injector as pi
        pi._GLOBAL_LOG = log
        mod._ifl_probe = pi._ifl_probe
        mod._ifl_record_decision = pi._ifl_record_decision
        mod._ifl_probe_ifexp = pi._ifl_probe_ifexp

        trace_snapshots: list[dict] = []
        actual_output = None
        error_message = None

        def _tracer(frame, event, arg):
            if frame.f_code.co_name == func_name and event in ("line", "return"):
                trace_snapshots.append({
                    "line": frame.f_lineno,
                    "event": event,
                    "locals": {k: repr(v) for k, v in frame.f_locals.items()},
                })
            return _tracer

        start = time.perf_counter()
        try:
            sys.settrace(_tracer)
            actual_output = getattr(mod, func_name)(**inputs)
        except Exception as exc:
            error_message = traceback.format_exc()
        finally:
            sys.settrace(None)
        elapsed_ms = (time.perf_counter() - start) * 1000

        probe_records = [
            {
                "test_id": r.test_id,
                "cond_id": r.cond_id,
                "value": r.value,
                "decision": r.decision,
            }
            for r in log.records
        ]

        if error_message:
            status = "error"
        elif expected_output is None:
            status = "coverage_only"
        elif str(actual_output) == str(expected_output):
            status = "pass"
        else:
            status = "fail"

        return ExecuteResult(
            actual_output=actual_output,
            status=status,
            error_message=error_message,
            execution_time_ms=elapsed_ms,
            probe_log=probe_records,
            trace_log=trace_snapshots,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ── 缺口分析 ─────────────────────────────────────────────────────────────────

def analyze_gap(
    source_code: str,
    func_name: str,
    domain_types: dict[str, str],
    domain_bounds: dict[str, list[int]],
    condition_id: str,
    flip_direction: str,
) -> GapHint:
    """對指定缺口呼叫 Z3，回傳建議的輸入範圍。"""
    from ifl_mcdc.models.coverage_matrix import GapEntry

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w",
                                     encoding="utf-8", delete=False) as f:
        f.write(source_code)
        tmp_path = f.name

    try:
        parser = ASTParser()
        decision_nodes = parser.parse_file(tmp_path)
        target_dn = next(
            (dn for dn in decision_nodes
             if any(c.cond_id == condition_id for c in dn.condition_set.conditions)),
            None,
        )
        if target_dn is None:
            return GapHint(condition_id, flip_direction, "找不到對應條件", [])

        smt = SMTConstraintSynthesizer(domain_bounds=domain_bounds)
        gap = GapEntry(
            condition_id=condition_id,
            flip_direction=flip_direction,
            missing_pair_type="unique_cause",
            estimated_difficulty=0.5,
        )
        result = smt.synthesize(target_dn, gap, domain_types)

        if not result.satisfiable:
            return GapHint(condition_id, flip_direction, "Z3 判定此路徑不可行", [])

        specs = []
        hint_parts = []
        for bs in (result.bound_specs or []):
            spec = {"var": bs.var_name, "type": bs.var_type}
            if bs.interval:
                lo, hi = bs.interval
                spec["min"] = lo
                spec["max"] = hi
                hint_parts.append(f"{bs.var_name} 需在 [{int(lo)}, {int(hi)}]")
            elif bs.valid_set:
                spec["valid"] = list(bs.valid_set)
                hint_parts.append(f"{bs.var_name} 需為 {list(bs.valid_set)}")
            specs.append(spec)

        return GapHint(
            condition_id=condition_id,
            flip_direction=flip_direction,
            z3_hint="，".join(hint_parts) if hint_parts else "請參考 bound_specs",
            bound_specs=specs,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
