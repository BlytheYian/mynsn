"""
IFL 主控迴圈：協調三層模組，執行完整的 IFL 迭代回饋迴圈。

參考 SDD 第 6 章。
"""
from __future__ import annotations

import inspect
import random
import sys
import types
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from ifl_mcdc.config import IFLConfig
from ifl_mcdc.exceptions import LLMSamplingError, Z3TimeoutError, Z3UNSATError
from ifl_mcdc.layer1.ast_parser import ASTParser
from ifl_mcdc.layer1.coverage_engine import MCDCCoverageEngine
from ifl_mcdc.layer1.probe_injector import ProbeInjector
from ifl_mcdc.layer2.gap_analyzer import GapAnalyzer
from ifl_mcdc.layer2.smt_synthesizer import SMTConstraintSynthesizer
from ifl_mcdc.layer3.acceptance_gate import AcceptanceGate
from ifl_mcdc.layer3.domain_validator import DomainValidator
from ifl_mcdc.layer3.llm_sampler import LLMBackend, LLMSampler
from ifl_mcdc.layer3.prompt_builder import PromptConstructor
from ifl_mcdc.models.decision_node import DecisionNode
from ifl_mcdc.models.probe_record import ProbeLog
from ifl_mcdc.models.validation import DomainRule


@dataclass
class IFLResult:
    """IFL 主迴圈的執行結果。"""

    converged: bool                       # True = 有效覆蓋率 100%（compute_effective_loss == 0，不計 infeasible）
    final_coverage: float                 # 有效覆蓋率（已覆蓋可行對 / 可行對總數，排除 infeasible）
    test_suite: list[dict[str, object]]   # 通過 AcceptanceGate 的案例
    iteration_count: int
    total_tokens: int                     # LLM API 消耗的估算 token 數
    infeasible_paths: list[str]           # 被 Z3 正式證明為結構性不可行的條件 ID（SMT_FAIL）
    loss_history: list[int]              # 每次迭代後的損失值（2k - covered）
    all_generated_cases: list[dict[str, object]] = field(default_factory=list)
    gate_exhausted_paths: list[str] = field(default_factory=list)
    # 理論上可達但本次 LLM 窮盡嘗試後放棄的條件 ID（GATE_EXHAUSTED）
    # 不代表真正不可行，僅表示本次實驗中未能生成有效配對
    # Gate 不論通過與否，所有已生成的案例（含隨機初始、LLM True 側、Z3 補集 False 側）
    # 供多樣性分析使用；不影響覆蓋率計算與 test_suite
    failure_log: list[str] = field(default_factory=list)
    # 每次 LLMSamplingError 或 SMT UNSAT/Timeout 的原因字串，供失敗率統計使用
    iteration_details: list[dict] = field(default_factory=list)
    # 🌟 [新增] 每次迭代的詳細日誌：生成結果、Gate 判決、拒絕原因
    gap_coverage_map: dict[str, dict] = field(default_factory=dict)
    # 以缺口為主軸的覆蓋狀態：{"{cond_id}_{flip}" -> {status, condition_expr, test_case, ...}}
    decision_truth_tables: dict = field(default_factory=dict)
    # 各決策節點的靜態 2^k 真值表：{node_id: {line_no, expression, conditions, rows}}
    probe_records: list[dict] = field(default_factory=list)
    # 所有測試案例的探針紀錄（含隨機初始）：[{test_id, cond_id, value, decision}, ...]


class IFLOrchestrator:
    """IFL 主控迴圈：協調 Layer1/2/3，驅動迭代直至收斂或預算耗盡。"""

    def __init__(
        self,
        config: IFLConfig,
        backend: LLMBackend | None = None,
    ) -> None:
        self.config = config
        self.parser = ASTParser()
        self.engine = MCDCCoverageEngine()
        self.analyzer = GapAnalyzer()
        self.smt = SMTConstraintSynthesizer(domain_bounds=config.domain_bounds)
        self.prompt = PromptConstructor()
        actual_backend = backend if backend is not None else config.llm_backend
        self.sampler = LLMSampler(actual_backend, config.domain_validator, config.llm_retry_delay)
        self.gate = AcceptanceGate(self.engine)
        self._infeasible: set[str] = set()           # 結構性不可行（SMT UNSAT）
        self._gate_exhausted: set[str] = set()       # 實用性放棄（GATE_EXHAUSTED，理論上可達）
        self._gap_map: dict[str, dict] = {}          # 缺口覆蓋狀態（供 gap_coverage_map 輸出）
        self._comp_failures: dict[str, int] = {}     # 補集連續失敗計數
        self._temp_skipped: set[str] = set()         # 暫時跳過（comp_failures>=3），等其他 gap 完成後回來重試
        self._skip_retry_count: dict[str, int] = {}  # 每個 gap 被解除 temp_skip 並重試的次數
        self._MAX_SKIP_RETRIES = 2                   # 重試超過此次數 → 升級為 gate_exhausted
        self._gate_failures: dict[str, int] = {}  # Gate 連續拒絕計數（兩側均拒）
        self._solution_history: dict[str, list[dict]] = {}  # gap → 已找到的 true 側解
        self._reachability_patches: list[dict] = []
        # 決策可達性修正紀錄：{"gap", "iteration", "patched_fields"}
        # 每筆代表 LLM 生成的非目標欄位遮蔽了目標條件的獨立效果，事後用 SMT 值補正

    def run(self, source_path: str | Path) -> IFLResult:
        """執行 IFL 主流程。

        步驟：
          1. Layer 1 解析原始碼
          2. 探針注入 & 動態載入
          3. 執行 3 個隨機初始測試案例
          4. 建立初始 MCDCMatrix
          5. IFL 迭代迴圈（直至收斂或預算耗盡）
          6. 組裝並回傳 IFLResult

        Args:
            source_path: 目標 Python 原始碼路徑。

        Returns:
            IFLResult 含覆蓋率、測試套件、損失歷程等資訊。

        Raises:
            ValueError: 原始碼中找不到任何決策節點。
        """
        # ── 步驟 1+2：解析 & 探針注入（語言相關，由 _prepare 封裝可被子類別覆寫）──
        decision_nodes, runtime, log = self._prepare(source_path)
        if not decision_nodes:
            raise ValueError(f"在 {source_path} 中找不到任何決策節點")

        # 保留 decision_nodes[0] 供初始隨機測試生成使用（_generate_random_test 主要靠 domain_types）
        dn = decision_nodes[0]

        # ── 步驟 3：初始隨機測試 ──
        test_suite: list[dict[str, object]] = []
        all_generated: list[dict[str, object]] = []
        failure_log: list[str] = []
        
        # 🌟 [關鍵改進] 強制至少生成一個 True 和一個 False 結果
        # 這是 MC/DC 配對的前置條件
        covered_true = False
        covered_false = False
        attempts = 0
        max_attempts = 30  # 隨機嘗試的上限——原本是 100，正常函式通常遠早於這個數字
        # 就湊到 True/False 兩側；真的一路撞到上限，多半代表函式本身有系統性問題
        # （例如呼叫時缺參數），不是單純運氣不好，調低上限讓這種情況更快浮現出來。
        
        # Phase 1: 不斷嘗試直到覆蓋 True 和 False 兩側
        while attempts < max_attempts and (not covered_true or not covered_false):
            test_case = self._generate_random_test(
                dn, self.config.domain_types, self.config.domain_bounds
            )
            test_id = self._run_test(runtime, test_case, log)
            entry = {**test_case, "__test_id": test_id, "__source": "random"}
            test_suite.append(entry)
            all_generated.append(entry)
            
            # 檢查此測試是否覆蓋了 True 或 False 側
            test_recs = log.get_by_test(test_id)
            for rec in test_recs:
                if rec.decision is not None:  # decision 表示整體決策結果
                    if rec.decision:
                        covered_true = True
                    else:
                        covered_false = True
            
            attempts += 1
        
        # Phase 2: 如果仍未成功，增加到至少 6 個測試
        min_initial = max(6, self.config.min_initial_random)
        while attempts < max_attempts and len(test_suite) < min_initial:
            test_case = self._generate_random_test(
                dn, self.config.domain_types, self.config.domain_bounds
            )
            test_id = self._run_test(runtime, test_case, log)
            entry = {**test_case, "__test_id": test_id, "__source": "random"}
            test_suite.append(entry)
            all_generated.append(entry)
            attempts += 1
        
        # 🌟 [改進] 診斷初始測試覆蓋情況 - 現在強制顯示（重點信息）
        print(f"\n【初始隨機測試】")
        print(f"  生成數量: {len(test_suite)} 個 (嘗試 {attempts})")
        print(f"  True 側: {'OK' if covered_true else 'NO'}")
        print(f"  False 側: {'OK' if covered_false else 'NO'}")
        if not (covered_true and covered_false):
            print(f"  WARNING: 無法生成 True/False 配對，覆蓋率可能為 0%")
            print(f"      建議: 函式邏輯可能過於受限，嘗試了 {attempts} 次")
        print()
        
        # Phase 3: 應急措施 - 如果仍未成功生成 False，嘗試手動構造
        if not covered_false and len(test_suite) >= min_initial:
            print(f"【應急措施】嘗試強制投放一個 False 側測試...")
            # 嘗試用全 False 的布林值
            emergency_test = {}
            seen: set[str] = set()
            for cond in dn.condition_set.conditions:
                for var_name in cond.var_names:
                    if var_name in seen:
                        continue
                    seen.add(var_name)
                    var_type = self.config.domain_types.get(var_name, "int")
                    if var_type == "bool":
                        emergency_test[var_name] = False  # 全設 False
                    else:
                        bounds = (self.config.domain_bounds or {}).get(var_name, [0, 130])
                        emergency_test[var_name] = bounds[0]  # 最小值
            
            test_id = self._run_test(runtime, emergency_test, log)
            entry = {**emergency_test, "__test_id": test_id, "__source": "emergency"}
            test_suite.append(entry)
            all_generated.append(entry)
            
            # 檢查是否成功
            test_recs = log.get_by_test(test_id)
            for rec in test_recs:
                if rec.decision is not None and not rec.decision:
                    covered_false = True
                    print(f"  OK 應急措施成功！取得 False 側結果")
                    break

        # ── 步驟 4：增量建立初始矩陣，同步記錄每個 pair 首次被哪個案例覆蓋 ──
        from ifl_mcdc.models.coverage_matrix import MCDCMatrix
        matrices = [MCDCMatrix(condition_set=dn_i.condition_set) for dn_i in decision_nodes]
        _covered_by: dict[str, str] = {}   # "{cond_id}_{flip}" → test_id

        for _entry in test_suite:
            _tid = _entry.get("__test_id")
            if not _tid:
                continue
            for _mat in matrices:
                _before = set(_mat._covered)
                self.engine._update_one(_mat, log, _tid)
                for (_cid, _flip) in (_mat._covered - _before):
                    _key = f"{_cid}_{_flip}"
                    if _key not in _covered_by:
                        _covered_by[_key] = _tid

        def _total_loss() -> int:
            return sum(m.compute_effective_loss() for m in matrices)

        def _total_coverage() -> float:
            # 分母：結構性不可行排除，gate_exhausted 仍計入（它們是「可達但未覆蓋」）
            # gate_exhausted 雖已被 mark_infeasible（供迴圈控制），
            # 但報告時應視為未覆蓋的可行條件，每個條件貢獻 T2F + F2T = 2 配對
            structural_feasible = sum(m.feasible_count for m in matrices)
            gate_pairs = len(self._gate_exhausted) * 2
            total_feasible = structural_feasible + gate_pairs
            if total_feasible == 0:
                return 1.0
            covered_feasible = sum(len(m._covered - m._infeasible) for m in matrices)
            # gate_exhausted 不在 covered（mark_infeasible 已從 covered 排除），
            # 故分子不需調整——它們就是分母中「未覆蓋」的部分
            return covered_feasible / total_feasible

        # ── 步驟 4.5：Z3 預算結構性不可行（確保 infeasible_paths 固定不浮動）──
        self._precompute_structural_infeasible(decision_nodes, matrices)

        loss_history: list[int] = [_total_loss()]

        # ── 步驟 5：IFL 迭代迴圈 ──
        iteration = 0
        error_history: list[str] = []  # 🌟 [新增] 用於回傳給 LLM 的前幾輪錯誤訊息
        iteration_details: list[dict] = []  # 🌟 [新增] 保存所有迭代的詳細日誌

        while _total_loss() > 0 and iteration < self.config.max_iterations:
            iteration += 1
            self._on_iteration_start(iteration)

            # 取難度最低的非不可行缺口（跨所有決策節點比較）
            best_gap = None
            best_dn = dn
            best_matrix = matrices[0]
            best_preceding: list[DecisionNode] = []

            # 第一趟：只選不在 _infeasible 且不在 _temp_skipped 的 gap
            for dn_idx, (dn_i, mat_i) in enumerate(zip(decision_nodes, matrices)):
                gaps = self.analyzer.analyze(mat_i)
                g = next((g for g in gaps
                           if g.condition_id not in self._infeasible
                           and g.condition_id not in self._temp_skipped), None)
                if g is None:
                    continue
                if best_gap is None or g.estimated_difficulty < best_gap.estimated_difficulty:
                    best_gap = g
                    best_dn = dn_i
                    best_matrix = mat_i
                    best_preceding = decision_nodes[:dn_idx]

            # 第二趟：若所有剩餘 gap 都暫時跳過，回頭重試一個（給新的機會）
            if best_gap is None and self._temp_skipped:
                for dn_idx, (dn_i, mat_i) in enumerate(zip(decision_nodes, matrices)):
                    gaps = self.analyzer.analyze(mat_i)
                    g = next((g for g in gaps if g.condition_id not in self._infeasible), None)
                    if g is None:
                        continue
                    if best_gap is None or g.estimated_difficulty < best_gap.estimated_difficulty:
                        best_gap = g
                        best_dn = dn_i
                        best_matrix = mat_i
                        best_preceding = decision_nodes[:dn_idx]
                if best_gap is not None:
                    # 解除暫停，重設各失敗計數，給它新的嘗試機會
                    self._temp_skipped.discard(best_gap.condition_id)
                    self._comp_failures.pop(best_gap.condition_id, None)
                    # gate_failures 已在 GATE_TEMP_SKIP 時重設為 0，此處無需再清
                    failure_log.append(
                        f"RETRY:{best_gap.condition_id}"
                        f"(skip_cnt={self._skip_retry_count.get(best_gap.condition_id, 0)})"
                    )

            if best_gap is None:
                break  # 所有剩餘缺口均結構性不可行

            gap = best_gap
            dn = best_dn
            target_matrix = best_matrix

            # 步驟 2：Z3 合成約束 Φ_gap（含前置路徑約束，確保函數可達目標決策節點）
            # none 模式：模式漸進（mode escalation）以處理多層巢狀 if
            #   mode 0 (failures < 3):  D1=True（外層 gate）
            #   mode 1 (3 <= failures < 6): D1=True, D2=True（進入 D2 的 True 分支）
            #   mode 2 (failures >= 6):    D1=True, D2=False（進入 D2 的 else 分支）
            #   failures >= 9: 三種模式均窮盡 → 標記不可行
            _gf = self._gate_failures.get(gap.condition_id, 0)
            _in_none = self.config.preceding_direction == "none"
            _has_inner = _in_none and len(best_preceding) > 1

            if _in_none and _gf >= 9:
                _retry_cnt = self._skip_retry_count.get(gap.condition_id, 0)
                if _retry_cnt >= self._MAX_SKIP_RETRIES:
                    # 重試上限已到 → 正式 GATE_EXHAUST（永久放棄）
                    self._gate_exhausted.add(gap.condition_id)
                    self._infeasible.add(gap.condition_id)
                    target_matrix.mark_infeasible(gap.condition_id, gap.flip_direction)
                    target_matrix.mark_infeasible(gap.condition_id, "T2F" if gap.flip_direction == "F2T" else "F2T")
                    failure_log.append(f"GATE_EXHAUSTED:{gap.condition_id}")
                    self._gap_map[f"{gap.condition_id}_T2F"] = {"status": "gate_exhausted", "condition_id": gap.condition_id, "flip_direction": "T2F"}
                    self._gap_map[f"{gap.condition_id}_F2T"] = {"status": "gate_exhausted", "condition_id": gap.condition_id, "flip_direction": "F2T"}
                else:
                    # 先放著，讓其他缺口優先，之後再回來重試
                    self._skip_retry_count[gap.condition_id] = _retry_cnt + 1
                    self._gate_failures[gap.condition_id] = 0  # 重設，給重試新的開始
                    self._temp_skipped.add(gap.condition_id)
                    failure_log.append(
                        f"GATE_TEMP_SKIP:{gap.condition_id}"
                        f"({_retry_cnt + 1}/{self._MAX_SKIP_RETRIES})"
                    )
                loss_history.append(_total_loss())
                continue

            _cmode = 0
            if _has_inner:
                if _gf >= 6:
                    _cmode = 2
                elif _gf >= 3:
                    _cmode = 1

            _positive: list[DecisionNode] | None = None
            _negative: list[DecisionNode] | None = None
            if _in_none and best_preceding:
                if _cmode == 0:
                    _positive = [decision_nodes[0]]
                elif _cmode == 1:
                    _positive = [decision_nodes[0], best_preceding[1]]
                elif _cmode == 2:
                    _positive = [decision_nodes[0]]
                    _negative = [best_preceding[1]]

            try:
                smt_result = self.smt.synthesize(
                    dn, gap, self.config.domain_types,
                    excluded_solutions=self._solution_history.get(gap.condition_id),
                    positive_constraints=_positive,
                    negative_constraints=_negative,
                )
            except Z3UNSATError as _smt_exc:
                if gap.condition_id in self._infeasible:
                    loss_history.append(_total_loss())
                    continue
                # 預算說可解 → UNSAT 由路徑約束造成（none 模式）或解空間耗盡
                if _in_none:
                    # none 模式：路徑約束問題，清空 solution_history 避免 excluded_solutions 累積阻擋
                    self._solution_history.pop(gap.condition_id, None)
                    self._gate_failures[gap.condition_id] = (
                        self._gate_failures.get(gap.condition_id, 0) + 3
                    )
                    failure_log.append(f"PATH_UNSAT:{gap.condition_id}")
                else:
                    # sequential 模式：可能是 excluded_solutions 耗盡，清空後 temp_skip 重試
                    self._solution_history.pop(gap.condition_id, None)
                    self._temp_skipped.add(gap.condition_id)
                    failure_log.append(f"SMT_UNSAT_SKIPPED:{gap.condition_id}")
                loss_history.append(_total_loss())
                continue
            except Z3TimeoutError as _smt_exc:
                # 超時不代表真的不可行：清空 solution history，暫時跳過重試
                self._solution_history.pop(gap.condition_id, None)
                self._temp_skipped.add(gap.condition_id)
                failure_log.append(f"SMT_TIMEOUT_SKIPPED:{gap.condition_id}")
                loss_history.append(_total_loss())
                continue

            if not smt_result.satisfiable:
                if gap.condition_id in self._infeasible:
                    loss_history.append(_total_loss())
                    continue
                # 預算說可解 → UNSAT 同 Z3UNSATError 邏輯
                if _in_none:
                    self._solution_history.pop(gap.condition_id, None)
                    self._gate_failures[gap.condition_id] = (
                        self._gate_failures.get(gap.condition_id, 0) + 3
                    )
                    failure_log.append(f"PATH_UNSAT:{gap.condition_id}")
                else:
                    self._solution_history.pop(gap.condition_id, None)
                    self._temp_skipped.add(gap.condition_id)
                    failure_log.append(f"SMT_UNSAT_SKIPPED:{gap.condition_id}")
                loss_history.append(_total_loss())
                continue

            # 步驟 2.5：目標條件自己的變數需要驗證，主提示詞的機制不足以保證：
            # (a) 耦合多變數（同一原子條件用到 2 個以上變數，例如 ALIM 查表：
            #     Down_Separation 的合法範圍依 Alt_Layer_Value 而定）——各自獨立
            #     給範圍表達不出依賴關係，會讓 LLM 湊出「各自合法、搭配起來
            #     不合法」的組合。
            # (b) 單一布林變數——DomainValidator 只檢查「是不是合法布林值」，
            #     True/False 都會通過，等於沒檢查「這次 gap 需要的到底是哪個」；
            #     不像整數還有範圍提示可依循，LLM 忽略提示、選錯值完全沒人攔。
            #     單一整數變數則維持現狀不動：主提示詞的 bound_specs 範圍提示
            #     目前運作良好，多加一次驗證呼叫只是不必要的開銷。
            # 兩種情況都先用 _resolve_coupled_group 解好，再從主提示詞排除。
            _target_cond = next(
                (c for c in dn.condition_set.conditions if c.cond_id == gap.condition_id),
                None,
            )
            _target_group_vars = (
                list(dict.fromkeys(_target_cond.var_names)) if _target_cond else []
            )
            _preresolved: dict[str, object] = {}
            if len(_target_group_vars) >= 2 or (
                len(_target_group_vars) == 1
                and self.config.domain_types.get(_target_group_vars[0]) == "bool"
            ):
                _preresolved = self._resolve_coupled_group(
                    dn, gap, _target_group_vars, smt_result, _positive, _negative,
                )

            # 步驟 3：將 Ω 轉化為自然語言提示，呼叫 LLM 產生測試案例
            # LLMSampler 內部處理 JSON 解析失敗與 DomainValidator 驗證（最多重試 MAX_RETRIES 次）
            scenarios = self.config.scenarios
            scenario_hint = scenarios[iteration % len(scenarios)] if scenarios else ""
            _prompt_bound_specs = [
                bs for bs in (smt_result.bound_specs or []) if bs.var_name not in _preresolved
            ]
            p_prompt = self.prompt.build(
                dn,
                gap,
                _prompt_bound_specs,
                self.config.func_signature,
                self.config.domain_context,
                clinical_profile=self.config.clinical_profile,
                scenario_hint=scenario_hint,
                domain_types=self.config.domain_types,
                domain_bounds=self.config.domain_bounds,
                error_history=error_history or None,
                smt_example=smt_result.model_python,
                preceding_nodes=best_preceding if self.config.preceding_direction == "sequential" else None,
                language=getattr(self.config, "language", "python"),
                preresolved=_preresolved or None,
            )
            # 步驟 4：LLM 生成 True 側 + Z3 直接合成 False 側（保證 MC/DC 配對有效）
            log_size_before_tests = len(log.records)
            iter_detail = {
                "iteration": iteration,
                "gap": gap.condition_id,
                "generated_cases": [],
                "gate_results": [],
            }
            try:
                new_case, _ = self.sampler.sample(
                    p_prompt, fallback_values=smt_result.model_python
                )

                # 耦合變數已事先解好、從主提示詞排除，這裡直接套用決定值
                # （LLM 不會輸出這些欄位，用 update 也能覆蓋掉萬一它還是輸出的舊值）
                if _preresolved:
                    new_case.update(_preresolved)

                # LLM 可能只輸出目標節點的變數，先用 SMT 模型值補全缺失變數，
                # 確保 positive_constraints（如 D1=True）所需變數值正確，
                # 剩餘缺少的變數再由 _fill_missing_params 以隨機值補全。
                if smt_result.model_python:
                    for _var, _val in smt_result.model_python.items():
                        if _var not in new_case:
                            new_case[_var] = _val

                # 補全 domain_types 中缺少的函數參數（LLM 可能只輸出部分欄位）
                new_case = self._fill_missing_params(
                    new_case, self.config.domain_types, self.config.domain_bounds
                )

                # 路徑可達性檢查：sequential 模式——前置節點應為 False，若 LLM 輸出為 True 則覆蓋
                if self.config.preceding_direction == "sequential" and best_preceding and smt_result.model_python:
                    for _pn in best_preceding:
                        try:
                            if bool(eval(_pn.expression_str, {"__builtins__": {}}, dict(new_case))):  # noqa: S307
                                path_vars = {v for _c in _pn.condition_set.conditions for v in _c.var_names}
                                new_case = dict(new_case)
                                for _var in path_vars:
                                    if _var in smt_result.model_python:
                                        new_case[_var] = smt_result.model_python[_var]
                        except Exception:
                            pass

                # 正向路徑修正：none 模式——positive_constraints（如 D1, D2）應為 True，
                # 若 LLM 輸出讓它為 False，強制用 SMT 值覆蓋，確保測試能到達目標節點
                if _positive and smt_result.model_python:
                    for _pn in _positive:
                        try:
                            if not bool(eval(_pn.expression_str, {"__builtins__": {}}, dict(new_case))):  # noqa: S307
                                path_vars = {v for _c in _pn.condition_set.conditions for v in _c.var_names}
                                new_case = dict(new_case)
                                for _var in path_vars:
                                    if _var in smt_result.model_python:
                                        new_case[_var] = smt_result.model_python[_var]
                        except Exception:
                            pass

                # 負向路徑修正：none 模式 mode=2——negative_constraints（如 D2）應為 False，
                # 若 LLM 輸出讓它為 True，強制用 SMT 值覆蓋（確保進入 else 分支）
                if _negative and smt_result.model_python:
                    for _pn in _negative:
                        try:
                            if bool(eval(_pn.expression_str, {"__builtins__": {}}, dict(new_case))):  # noqa: S307
                                path_vars = {v for _c in _pn.condition_set.conditions for v in _c.var_names}
                                new_case = dict(new_case)
                                for _var in path_vars:
                                    if _var in smt_result.model_python:
                                        new_case[_var] = smt_result.model_python[_var]
                        except Exception:
                            pass

                # 🌟 決策可達性檢查：LLM 填的非目標欄位可能無意間遮蔽（mask）
                # 目標條件的獨立效果（例如 AND 鏈裡某個必要欄位被設成 False），
                # 導致這組配對執行後決策根本不是預期值，白測一次。
                # 只在驗證失敗時才介入。先在一份「試跑副本」上用 Z3 解逐欄位補到剛好
                # 可達為止，藉此找出「最少需要修正的欄位集合」（順序即後續逐一修補的順序）。
                # 修補時「逐一」進行，不整批丟給 LLM：每補一個欄位就馬上驗證可達性，
                # 可達就停；不可達才繼續下一個欄位——此時前面已定案的欄位當作固定上下文，
                # 讓後面的欄位有機會跟它搭配一致（欄位彼此有數值關聯時尤其重要，例如
                # tcas_sir_copy 的 Own_Tracked_Alt/Other_Tracked_Alt，各自獨立在合法區間
                # 內選值不保證兩者搭配後仍可達）。整組逐一試過仍不可達時，放棄目前累積的
                # 部分 LLM 值，整組欄位改用 Z3 原始解覆蓋——因為 Z3 那組解是互相搭配好、
                # 保證可達的一組值，若只保留其中幾個欄位混用 LLM 的值，Z3 解不再保證有效。
                # 其餘未被判定為遮蔽的欄位（含 LLM 選的 OR 群組組合）維持原樣，保留多樣性。
                if smt_result.model_python:
                    try:
                        _reachable = bool(
                            eval(dn.expression_str, {"__builtins__": {}}, dict(new_case))  # noqa: S307
                        )
                    except Exception:
                        _reachable = False
                    if not _reachable:
                        # 試跑：只是為了找出最少需要修正的欄位集合與順序，不影響 new_case
                        _dry_case = dict(new_case)
                        _masked_vars: list[str] = []
                        for _cond in dn.condition_set.conditions:
                            if _cond.cond_id == gap.condition_id:
                                continue
                            for _var in _cond.var_names:
                                if (_var in smt_result.model_python
                                        and _dry_case.get(_var) != smt_result.model_python[_var]):
                                    _dry_case[_var] = smt_result.model_python[_var]
                                    _masked_vars.append(_var)
                                    try:
                                        if bool(eval(dn.expression_str, {"__builtins__": {}}, dict(_dry_case))):  # noqa: S307
                                            break
                                    except Exception:
                                        pass
                            else:
                                continue
                            break

                        _patched_fields: list[str] = []
                        _patch_methods: list[str] = []
                        _llm_attempts = 0
                        if _masked_vars:
                            _candidate = dict(new_case)
                            _orig_validator = self.sampler.validator
                            _became_reachable = False
                            # 案例裡「非遮蔽」欄位的目前實際值——這些不會再變動，
                            # 每次條件式重新求解都要一併釘死，否則 Z3 會把它們當自由
                            # 變數重新配置，算出的區間只保證「配合 Z3 自己選的其他欄位
                            # 值」才成立，不保證配合這個案例實際已固定的其他欄位值。
                            _context_fixed: dict[str, object] = {
                                k: v for k, v in new_case.items()
                                if k in self.config.domain_types and k not in _masked_vars
                            }
                            _fixed_so_far: dict[str, object] = {}
                            for _var in _masked_vars:
                                # 條件式重新求解：把「非遮蔽欄位的實際值」+「前面已定案的
                                # 遮蔽欄位＝實際選定的值」都釘死，只留目前這個欄位自由，
                                # 這樣算出來的區間才是配合整個案例現況、保證可達的真實範圍
                                # ——數值上互相耦合的欄位（例如 tcas_sir_copy 的
                                # Own_Tracked_Alt/Other_Tracked_Alt）才有機會湊出一致的組合。
                                _step_result = smt_result
                                try:
                                    _cond_result = self.smt.synthesize(
                                        dn, gap, self.config.domain_types,
                                        excluded_solutions=self._solution_history.get(gap.condition_id),
                                        positive_constraints=_positive,
                                        negative_constraints=_negative,
                                        fixed_values={**_context_fixed, **_fixed_so_far},
                                    )
                                    if _cond_result.satisfiable:
                                        _step_result = _cond_result
                                except (Z3UNSATError, Z3TimeoutError):
                                    pass  # 條件式求解失敗：退回原始邊際區間，仍可嘗試

                                _bound_by_var = {
                                    bs.var_name: bs for bs in (_step_result.bound_specs or [])
                                }
                                _exact_value = (_step_result.model_python or {}).get(
                                    _var, smt_result.model_python[_var]
                                )

                                if self.config.domain_types.get(_var) == "bool":
                                    _req = bool(_exact_value)
                                    _required_bools = {_var: _req}
                                    _patch_rules = [DomainRule(
                                        field=_var, description=f"{_var} must be {_req}",
                                        validator=lambda v, req=_req: isinstance(v, bool) and v == req,
                                    )]
                                else:
                                    _bs = _bound_by_var.get(_var)
                                    if _bs is not None and _bs.interval is not None:
                                        _lo, _hi = _bs.interval
                                    elif _var in self.config.domain_bounds:
                                        _lo, _hi = self.config.domain_bounds[_var]
                                    else:
                                        _lo, _hi = (0, 9999999)
                                    _required_bools = {}
                                    _patch_rules = [DomainRule(
                                        field=_var, description=f"{_var} in [{_lo},{_hi}]",
                                        validator=lambda v, lo=_lo, hi=_hi: (
                                            isinstance(v, (int, float)) and not isinstance(v, bool)
                                            and lo <= v <= hi
                                        ),
                                    )]

                                _patch_prompt = self.prompt.build_mask_patch(
                                    _candidate, [_var], self.config.domain_types,
                                    _required_bools, _bound_by_var,
                                    self.config.func_signature, self.config.domain_context,
                                )
                                _repair_before = len(self.sampler.repair_log)
                                self.sampler.validator = DomainValidator(_patch_rules)
                                try:
                                    _partial, _ = self.sampler.sample(
                                        _patch_prompt, fallback_values={_var: _exact_value}
                                    )
                                    _llm_attempts += 1
                                    _used_fallback = len(self.sampler.repair_log) > _repair_before
                                    if _var in _partial:
                                        _candidate[_var] = _partial[_var]
                                    _patched_fields.append(_var)
                                    _patch_methods.append("z3_fallback" if _used_fallback else "llm")
                                except LLMSamplingError:
                                    # 這個欄位 LLM 完全生不出合法值：直接用條件式 Z3 精確值頂上
                                    _candidate[_var] = _exact_value
                                    _patched_fields.append(_var)
                                    _patch_methods.append("z3_hard_fallback")
                                finally:
                                    self.sampler.validator = _orig_validator

                                _fixed_so_far[_var] = _candidate[_var]

                                try:
                                    _became_reachable = bool(
                                        eval(dn.expression_str, {"__builtins__": {}}, dict(_candidate))  # noqa: S307
                                    )
                                except Exception:
                                    _became_reachable = False
                                if _became_reachable:
                                    new_case = _candidate
                                    break
                            else:
                                _became_reachable = False

                            if not _became_reachable:
                                # 保底：整組逐一試過仍不可達，放棄部分 LLM 值，
                                # 整組欄位改用 Z3 原始解覆蓋，保證流程能收斂
                                for _var in _masked_vars:
                                    new_case[_var] = smt_result.model_python[_var]
                                _patched_fields = list(_masked_vars)
                                _patch_methods = ["z3_hard_fallback"] * len(_masked_vars)

                        if _patched_fields:
                            self._reachability_patches.append({
                                "gap": gap.condition_id,
                                "iteration": iteration,
                                "patched_fields": _patched_fields,
                                "methods": _patch_methods,
                                "llm_attempts": _llm_attempts,
                            })
                            failure_log.append(
                                f"MASK_PATCHED:{gap.condition_id}:"
                                + ",".join(f"{f}={m}" for f, m in zip(_patched_fields, _patch_methods))
                            )

                iter_detail["generated_cases"].append({
                    "type": "llm_true",
                    "values": new_case,
                })

                # 記錄此次 true 側解（以 model_python 為錨點，因為它是 SMT 的合法解）
                # 下次同一 gap 的 synthesize 會排除此錨點的鄰域，確保多樣性
                anchor = smt_result.model_python or {}
                self._solution_history.setdefault(gap.condition_id, []).append(anchor)

                # Z3 合成 False 側：固定非目標條件的變數值 → 保證 _others_ok 成立
                comp_test = self.smt.synthesize_complement(
                    dn, gap, self.config.domain_types, new_case
                )

                if comp_test is None and smt_result.model_python:
                    # LLM True 側可能讓 OR 結構中的遮罩條件阻擋目標條件獨立翻轉
                    # 只覆蓋「與目標條件有 OR 耦合的其他條件」所使用的變數，保留 LLM 其餘輸出
                    try:
                        coupled = dn.condition_set.get_coupled(gap.condition_id)
                        mask_vars: set[str] = set()
                        for coupled_cond, coupling_type in coupled:
                            if coupling_type == "OR":
                                mask_vars.update(coupled_cond.var_names)
                        if mask_vars:
                            merged = dict(new_case)
                            for var in mask_vars:
                                if var in smt_result.model_python:
                                    merged[var] = smt_result.model_python[var]
                            comp_test = self.smt.synthesize_complement(
                                dn, gap, self.config.domain_types, merged
                            )
                            if comp_test is not None:
                                new_case = merged  # 部分覆蓋後的版本：LLM 主體 + SMT 解遮罩
                    except Exception:
                        pass

                if comp_test is None:
                    # 補集找不到：LLM True 側選擇不佳，條件理論上仍可達
                    cnt = self._comp_failures.get(gap.condition_id, 0) + 1
                    self._comp_failures[gap.condition_id] = cnt
                    if cnt >= 3:
                        # 暫時跳過此 gap，待其他 gap 完成後回來重試（不加入 _infeasible）
                        self._temp_skipped.add(gap.condition_id)
                        failure_log.append(f"COMP_SKIPPED:{gap.condition_id}")
                    # 仍執行 True 側（加入 log 備用，但不強制配對）
                    true_id = self._run_test(runtime, new_case, log)
                    loss_before_true = target_matrix.compute_loss()
                    true_accepted = self.gate.evaluate(target_matrix, log, true_id)
                    loss_after_true = target_matrix.compute_loss()
                    iter_detail["gate_results"].append({
                        "side": "true",
                        "test_id": true_id,
                        "case": new_case,
                        "accepted": true_accepted,
                        "loss_before": loss_before_true,
                        "loss_after": loss_after_true,
                        "reason": "coverage_improved" if true_accepted else f"no_coverage_gain（loss {loss_before_true} → {loss_after_true}）",
                    })
                    llm_entry = {**new_case, "__test_id": true_id, "__source": "llm"}
                    all_generated.append(llm_entry)
                    if true_accepted:
                        test_suite.append(llm_entry)
                        _gkey = f"{gap.condition_id}_{gap.flip_direction}"
                        if _gkey not in self._gap_map:
                            self._gap_map[_gkey] = {"status": "covered", "condition_id": gap.condition_id, "flip_direction": gap.flip_direction, "test_id": true_id, "test_case": {k: v for k, v in new_case.items() if not k.startswith("__")}}
                else:
                    # 補集可行：重設失敗計數，執行兩側後 Gate 驗證
                    self._comp_failures[gap.condition_id] = 0
                    iter_detail["generated_cases"].append({
                        "type": "smt_complement",
                        "values": comp_test,
                    })

                    true_id = self._run_test(runtime, new_case, log)
                    loss_before_true = target_matrix.compute_loss()
                    true_accepted = self.gate.evaluate(target_matrix, log, true_id)
                    loss_after_true = target_matrix.compute_loss()

                    false_id = self._run_test(runtime, comp_test, log)
                    loss_before_false = target_matrix.compute_loss()
                    false_accepted = self.gate.evaluate(target_matrix, log, false_id)
                    loss_after_false = target_matrix.compute_loss()
                    
                    iter_detail["gate_results"].append({
                        "side": "true",
                        "test_id": true_id,
                        "case": new_case,
                        "accepted": true_accepted,
                        "loss_before": loss_before_true,
                        "loss_after": loss_after_true,
                        "reason": "coverage_improved" if true_accepted else f"no_coverage_gain（loss {loss_before_true} → {loss_after_true}）",
                    })
                    iter_detail["gate_results"].append({
                        "side": "false",
                        "test_id": false_id,
                        "case": comp_test,
                        "accepted": false_accepted,
                        "loss_before": loss_before_false,
                        "loss_after": loss_after_false,
                        "reason": "coverage_improved" if false_accepted else f"no_coverage_gain（loss {loss_before_false} → {loss_after_false}）",
                    })
                    
                    llm_entry = {**new_case, "__test_id": true_id, "__source": "llm"}
                    comp_entry = {**comp_test, "__test_id": false_id, "__source": "smt_comp"}
                    # 不論 Gate 結果，all_generated 均記錄（供多樣性分析用）
                    all_generated.append(llm_entry)
                    all_generated.append(comp_entry)

                    # 注意：此處 Gate 已側執行完了，上面的 evaluate() 一併修改了矩陣
                    if true_accepted:
                        test_suite.append(llm_entry)
                        _gkey = f"{gap.condition_id}_{gap.flip_direction}"
                        if _gkey not in self._gap_map:
                            self._gap_map[_gkey] = {"status": "covered", "condition_id": gap.condition_id, "flip_direction": gap.flip_direction, "test_id": true_id, "test_case": {k: v for k, v in new_case.items() if not k.startswith("__")}}
                    if false_accepted:
                        test_suite.append(comp_entry)
                        _comp_flip = "F2T" if gap.flip_direction == "T2F" else "T2F"
                        _ckey = f"{gap.condition_id}_{_comp_flip}"
                        if _ckey not in self._gap_map:
                            self._gap_map[_ckey] = {"status": "covered", "condition_id": gap.condition_id, "flip_direction": _comp_flip, "test_id": false_id, "test_case": {k: v for k, v in comp_test.items() if not k.startswith("__")}}

                # 🌟 [新增] 收集此輪測試產生的錯誤訊息，供下輪 Prompt 使用
                new_error_msgs = []
                for record in log.records[log_size_before_tests:]:
                    if record.error_msg:
                        new_error_msgs.append(record.error_msg)
                # 檢查 LLM 輸出是否超出 SMT bound_specs 可行範圍，違反者反饋給下輪
                if smt_result.bound_specs:
                    for _bs in smt_result.bound_specs:
                        if _bs.interval is None:
                            continue
                        _val = new_case.get(_bs.var_name)
                        if _val is None:
                            continue
                        _lo, _hi = _bs.interval
                        try:
                            if not (_lo <= float(_val) <= _hi):
                                new_error_msgs.append(
                                    f"{_bs.var_name} 值為 {_val}，超出可行範圍 [{int(_lo)}, {int(_hi)}]，請修正"
                                )
                        except (TypeError, ValueError):
                            pass
                # 取最近 5 筆錯誤（避免 prompt 過長），更新 error_history
                error_history = new_error_msgs[-5:] if new_error_msgs else []
                # Gate 失敗計數：本輪兩側均被拒則遞增，任一側通過則重設
                _gate_accepted = any(r.get("accepted") for r in iter_detail.get("gate_results", []))
                if _gate_accepted:
                    self._gate_failures[gap.condition_id] = 0
                else:
                    self._gate_failures[gap.condition_id] = self._gate_failures.get(gap.condition_id, 0) + 1
                # 🌟 [新增] 記錄此次迭代的詳細日誌
                iteration_details.append(iter_detail)
            except LLMSamplingError as _llm_exc:
                failure_log.append(f"LLM_FAIL:{str(_llm_exc)[:80]}")
                # 清空錯誤史以記錄 LLM 層錯誤
                error_history = []
                # 🌟 [新增] 記錄此次迭代的詳細日誌（即使失敗）
                iter_detail["error"] = str(_llm_exc)[:200]
                iteration_details.append(iter_detail)

            loss_history.append(_total_loss())

        # ── 步驟 5.5：以完整 test_suite（含 IFL 生成）重建 _covered_by ──
        # 初始建立時只包含隨機測試，IFL 迴圈的側面覆蓋未追蹤，在此補齊
        _covered_by = {}
        _replay_mats = [
            MCDCMatrix(condition_set=dn_i.condition_set) for dn_i in decision_nodes
        ]
        for _e in all_generated:
            _tid = _e.get("__test_id")
            if not _tid:
                continue
            for _rm in _replay_mats:
                _before = set(_rm._covered)
                self.engine._update_one(_rm, log, _tid)
                for (_cid, _flip) in (_rm._covered - _before):
                    _k2 = f"{_cid}_{_flip}"
                    if _k2 not in _covered_by:
                        _covered_by[_k2] = _tid

        # ── 步驟 6：組裝結果 ──
        total_tokens = sum(
            cast(int, e.get("total_tokens", 0))
            for e in self.sampler.token_log
        )

        # ── 以矩陣地面真相修正 gap_map ──
        # _gap_map 只記錄「主動瞄準」的缺口；隨機初始測試或附帶覆蓋不在其中，
        # 需對照 MCDCMatrix._covered / _infeasible 補正，否則 UI 顯示錯誤狀態。
        for dn_i, mat_i in zip(decision_nodes, matrices):
            for _cond in dn_i.condition_set.conditions:
                _cid = _cond.cond_id
                for _flip in ("T2F", "F2T"):
                    _key = f"{_cid}_{_flip}"
                    _in_covered    = (_cid, _flip) in mat_i._covered
                    _in_infeasible = (_cid, _flip) in mat_i._infeasible
                    existing_status = self._gap_map.get(_key, {}).get("status")
                    if _in_covered and existing_status != "covered":
                        # 隱性覆蓋（隨機初始或其他缺口附帶覆蓋）
                        # 直接從增量記錄中取出首次覆蓋此 pair 的案例
                        _rep_case = None
                        _covering_tid = _covered_by.get(_key)
                        if _covering_tid:
                            _tc_entry = next(
                                (tc for tc in test_suite
                                 if tc.get("__test_id") == _covering_tid),
                                None
                            )
                            if _tc_entry:
                                _rep_case = {k: v for k, v in _tc_entry.items()
                                             if not k.startswith("__")}
                        self._gap_map[_key] = {
                            "status": "covered",
                            "condition_id": _cid,
                            "flip_direction": _flip,
                            "implicit": True,
                            "test_id": _covering_tid,
                            "test_case": _rep_case,
                        }
                    elif _in_infeasible and existing_status not in ("infeasible", "gate_exhausted"):
                        self._gap_map[_key] = {
                            "status": "infeasible",
                            "condition_id": _cid,
                            "flip_direction": _flip,
                            "proof": "Z3_UNSAT",
                        }

        # 補全 condition_expr 並填入剩餘真正未覆蓋缺口
        _cond_expr = {c.cond_id: c.expression for dn_i in decision_nodes for c in dn_i.condition_set.conditions}
        for _cid, _expr in _cond_expr.items():
            for _flip in ("T2F", "F2T"):
                _key = f"{_cid}_{_flip}"
                if _key not in self._gap_map:
                    self._gap_map[_key] = {"status": "uncovered", "condition_id": _cid, "flip_direction": _flip}
                self._gap_map[_key]["condition_expr"] = _expr

        # ── 建構各決策節點的靜態 2^k 真值表 ──
        # 從 ProbeLog 收集每個 test case 的條件真假值（以 decision_id+combo 為 key）
        _tested: dict[str, dict[tuple, str]] = {}   # node_id → {combo_tuple: test_id}
        for _tc_entry in test_suite:
            _tid = _tc_entry.get("__test_id", "")
            if not _tid:
                continue
            _recs = log.get_by_test(_tid)
            _rec_map = {r.cond_id: r for r in _recs}
            for _dn_i in decision_nodes:
                _conds_i = _dn_i.condition_set.conditions
                _combo_t = tuple(
                    _rec_map[c.cond_id].value if c.cond_id in _rec_map else None
                    for c in _conds_i
                )
                if None in _combo_t:
                    continue
                _node_tested = _tested.setdefault(_dn_i.node_id, {})
                if _combo_t not in _node_tested:
                    _node_tested[_combo_t] = _tid

        _decision_truth_tables: dict[str, dict] = {}
        for _dn_i in decision_nodes:
            _conds_i = _dn_i.condition_set.conditions
            _k = len(_conds_i)
            _node_tested = _tested.get(_dn_i.node_id, {})
            _rows = []
            for _mask in range(1 << _k):
                _combo = tuple(bool(_mask >> _i & 1) for _i in range(_k))
                _dec   = self._eval_decision(_dn_i.expression_str, _conds_i, _combo)
                _tid   = _node_tested.get(_combo)
                _rows.append({
                    "combo":          list(_combo),
                    "decision":       _dec,
                    "covered":        _tid is not None,
                    "covering_test":  _tid,
                })
            _decision_truth_tables[_dn_i.node_id] = {
                "line_no":    _dn_i.line_no,
                "expression": _dn_i.expression_str,
                "conditions": [{"cond_id": c.cond_id, "expression": c.expression}
                               for c in _conds_i],
                "rows":       _rows,
            }

        return IFLResult(
            converged=_total_loss() == 0 and not self._gate_exhausted,
            final_coverage=_total_coverage(),
            test_suite=test_suite,
            iteration_count=iteration,
            total_tokens=total_tokens,
            infeasible_paths=list(self._infeasible - self._gate_exhausted),
            loss_history=loss_history,
            all_generated_cases=all_generated,
            gate_exhausted_paths=list(self._gate_exhausted),
            failure_log=failure_log,
            iteration_details=iteration_details,
            gap_coverage_map=self._gap_map,
            decision_truth_tables=_decision_truth_tables,
            probe_records=[
                {
                    "test_id": r.test_id,
                    "cond_id": r.cond_id,
                    "value": r.value,
                    "decision": r.decision,
                }
                for r in log.records
            ],
        )

    def _precompute_structural_infeasible(
        self,
        decision_nodes: list[DecisionNode],
        matrices: list,
    ) -> None:
        """IFL 迴圈開始前，用 Z3 掃描所有條件，預先標記結構性不可行的配對。

        確保 infeasible_paths 在每次 run 中固定（不受 LLM 探索順序影響）。
        只標記 Z3 能直接證明 UNSAT 的條件；Timeout 不視為結構性不可行。
        """
        from ifl_mcdc.models.coverage_matrix import GapEntry
        from ifl_mcdc.exceptions import Z3UNSATError, Z3TimeoutError

        marked: set[str] = set()

        for dn, matrix in zip(decision_nodes, matrices):
            for cond in dn.condition_set.conditions:
                cid = cond.cond_id
                if cid in marked:
                    continue
                for flip in ("T2F", "F2T"):
                    try:
                        gap = GapEntry(
                            condition_id=cid, flip_direction=flip,
                            missing_pair_type=flip, estimated_difficulty=1.0,
                        )
                        result = self.smt.synthesize(
                            dn, gap, self.config.domain_types
                        )
                        if not result.satisfiable:
                            # Z3 直接回傳 UNSAT → 結構性不可行
                            self._infeasible.add(cid)
                            matrix.mark_infeasible(cid, "T2F")
                            matrix.mark_infeasible(cid, "F2T")
                            marked.add(cid)
                            self._gap_map[f"{cid}_T2F"] = {"status": "infeasible", "condition_id": cid, "flip_direction": "T2F", "proof": "Z3_UNSAT"}
                            self._gap_map[f"{cid}_F2T"] = {"status": "infeasible", "condition_id": cid, "flip_direction": "F2T", "proof": "Z3_UNSAT"}
                            break
                        # satisfiable → 可行，不標記
                    except Z3UNSATError:
                        self._infeasible.add(cid)
                        matrix.mark_infeasible(cid, "T2F")
                        matrix.mark_infeasible(cid, "F2T")
                        marked.add(cid)
                        self._gap_map[f"{cid}_T2F"] = {"status": "infeasible", "condition_id": cid, "flip_direction": "T2F", "proof": "Z3_UNSAT"}
                        self._gap_map[f"{cid}_F2T"] = {"status": "infeasible", "condition_id": cid, "flip_direction": "F2T", "proof": "Z3_UNSAT"}
                        break
                    except Z3TimeoutError:
                        # Timeout 不視為結構性不可行，跳過（讓 IFL 迴圈決定）
                        break
                    except Exception:
                        break

    def _prepare(
        self, source_path: str | Path
    ) -> tuple[list[DecisionNode], object, ProbeLog]:
        """解析原始碼並建立執行期環境。

        回傳 (decision_nodes, runtime, log)。
        Python 預設實作使用 AST 解析 + 動態載入；C 版本由子類別覆寫。
        """
        decision_nodes = self.parser.parse_file(str(source_path))
        source = Path(source_path).read_text(encoding="utf-8")
        injector = ProbeInjector(decision_nodes)
        instrumented_source = injector.inject(source)
        module_name = f"_ifl_inst_{Path(str(source_path)).stem}"
        runtime = self._load_from_string(instrumented_source, module_name)
        log = ProbeLog()
        self._inject_probes(runtime, log)
        return decision_nodes, runtime, log

    def _on_iteration_start(self, iteration: int) -> None:
        """IFL 迭代迴圈每跑一輪（iteration < max_iterations）就呼叫一次，預設不做事。
        給子類別（例如 API 層要回報進度）掛勾用——這個 iteration 才是真正跟
        max_iterations 同一把尺量出來的數字；_run_test 每次呼叫不能拿來當作
        「第幾輪」，因為初始隨機測試、單輪內的 true/false 配對測試都會呼叫
        好幾次 _run_test，次數遠比 max_iterations 多。"""

    def _run_test(
        self,
        module: types.ModuleType,
        test_case: dict[str, object],
        log: ProbeLog,
    ) -> str:
        """執行單一測試案例，記錄探針，回傳 test_id。"""
        import ifl_mcdc.layer1.probe_injector as pi

        test_id = f"T{uuid.uuid4().hex[:8]}"
        setattr(pi._CURRENT_TEST_ID, "value", test_id)
        func = getattr(module, self.config.func_name)
        try:
            # test_case 可能含函式簽名沒有宣告的自由變數（例如條件式引用的模組層級
            # 常數，如 THRESHOLD）。這種名字不能當關鍵字參數傳給函式——簽名沒有
            # 這個參數，多傳了 Python 會直接拋 TypeError，函式本體（跟裡面的探針）
            # 就永遠不會被執行到。
            # 但這種自由變數本身仍應該是「這次測試真正要套用的值」，不能就這樣
            # 丟掉、放任它解析回原始碼寫死的值——不然引擎（domain_bounds／SMT／
            # LLM）幫它決定的候選值全部失效，永遠只會測到同一個寫死的常數。
            # 做法：把它直接覆寫成模組屬性，讓函式內部透過一般作用域規則解析到
            # 的就是這次測試選定的值。
            sig_params = inspect.signature(func).parameters
            call_kwargs: dict[str, object] = {}
            for k, v in test_case.items():
                if k in sig_params:
                    call_kwargs[k] = v
                elif hasattr(module, k):
                    setattr(module, k, v)
            func(**call_kwargs)
        except Exception as _exc:  # noqa: BLE001  # 使用者函式可能拋出任意例外
            pass  # 測試失敗不影響探針記錄（已在 if 前記錄）
        return test_id

    @staticmethod
    def _load_from_string(source: str, module_name: str) -> types.ModuleType:
        """動態編譯並載入儀表板化模組。"""
        mod = types.ModuleType(module_name)
        exec(compile(source, module_name, "exec"), mod.__dict__)  # noqa: S102
        sys.modules[module_name] = mod
        return mod

    @staticmethod
    def _inject_probes(module: types.ModuleType, log: ProbeLog) -> None:
        """將探針函式注入儀表板化模組，並指向給定的 ProbeLog。"""
        import ifl_mcdc.layer1.probe_injector as pi

        pi._GLOBAL_LOG = log
        setattr(module, "_ifl_probe", pi._ifl_probe)
        setattr(module, "_ifl_record_decision", pi._ifl_record_decision)
        setattr(module, "_ifl_probe_ifexp", pi._ifl_probe_ifexp)

    @staticmethod
    def _random_bool(var_name: str, domain_bounds: dict[str, list[int]] | None) -> bool:
        """依 domain_bounds 決定布林變數的隨機值：[1,1] 固定 True、[0,0] 固定 False，
        其餘（含未設定）不受限制、自由隨機。"""
        bounds = (domain_bounds or {}).get(var_name)
        if bounds == [1, 1]:
            return True
        if bounds == [0, 0]:
            return False
        return random.choice([True, False])

    @classmethod
    def _generate_random_test(
        cls,
        dn: DecisionNode,
        domain_types: dict[str, str],
        domain_bounds: dict[str, list[int]] | None = None,
    ) -> dict[str, object]:
        """為決策節點的所有變數生成隨機值。

        int → randint(lo, hi)，邊界來自 domain_bounds；若無設定則 [0, 130]。
        bool → choice([True, False])，但 domain_bounds 為 [1,1]/[0,0] 時固定該值。

        使用 domain_types（含函數所有參數）而非僅目標決策節點的條件，
        確保多參數函數（如 TCAS）呼叫時不因缺少必要參數而在 Python 層拋出 TypeError。
        """
        test: dict[str, object] = {}
        seen: set[str] = set()
        # 先生成目標決策節點的條件變數（維持原有邏輯）
        for cond in dn.condition_set.conditions:
            for var_name in cond.var_names:
                if var_name in seen:
                    continue
                seen.add(var_name)
                var_type = domain_types.get(var_name, "int")
                if var_type == "bool":
                    test[var_name] = cls._random_bool(var_name, domain_bounds)
                else:
                    bounds = (domain_bounds or {}).get(var_name, [0, 130])
                    test[var_name] = random.randint(bounds[0], bounds[1])
        # 補充生成 domain_types 中其餘的函數參數（避免呼叫時缺少必要參數）
        for var_name, var_type in domain_types.items():
            if var_name in seen:
                continue
            seen.add(var_name)
            if var_type == "bool":
                test[var_name] = cls._random_bool(var_name, domain_bounds)
            else:
                bounds = (domain_bounds or {}).get(var_name, [0, 130])
                test[var_name] = random.randint(bounds[0], bounds[1])
        return test

    _SMALL_DOMAIN_WIDTH = 20
    # 耦合變數群組裡，域寬度 <= 這個門檻的變數視為「可窮舉」，改用依賴關係表格
    # 一次問完；否則走逐一問＋條件式重算（跟遮蔽修補同一套技術）。

    def _resolve_coupled_group(
        self,
        dn: DecisionNode,
        gap,
        group_vars: list[str],
        smt_result,
        positive: list[DecisionNode] | None,
        negative: list[DecisionNode] | None,
    ) -> dict[str, object]:
        """解決「同一條件用到 2 個以上變數」的耦合群組，回傳這些變數的決定值。

        問題背景：這些變數各自的邊際範圍（bound_specs）分開看都合法，但搭配
        起來不保證滿足真正的約束（例如 ALIM 查表：Down_Separation 的合法範圍
        其實依賴 Alt_Layer_Value 選了哪個值，各自獨立給範圍會讓 LLM 湊出「各自
        合法、搭配起來不合法」的組合）。

        群組裡若有變數域寬度小（可窮舉，例如 0~3），就把每個可能值對應的另一
        變數合法區間都用 Z3 算好，攤成「若 X 在 A~B 則 Y 需要在 C~D」的條件式
        表格一次問完；否則（例如兩邊都是大範圍連續整數）退回逐一問＋條件式
        重算，確保最終回傳的值彼此搭配、真正可達。失敗時一律退回 Z3 精確解，
        保證正確性。
        """
        domain_types = self.config.domain_types
        domain_bounds = self.config.domain_bounds
        fallback = {
            v: smt_result.model_python[v]
            for v in group_vars if smt_result.model_python and v in smt_result.model_python
        }
        if len(fallback) < len(group_vars):
            return fallback  # SMT 沒能給出完整解，沒有安全的窮舉/逐一問基礎，直接用能有的部分

        enum_var = None
        for v in group_vars:
            if domain_types.get(v) == "bool":
                continue
            bounds = domain_bounds.get(v)
            if bounds and (bounds[1] - bounds[0]) <= self._SMALL_DOMAIN_WIDTH:
                enum_var = v
                break
        other_vars = [v for v in group_vars if v != enum_var]

        if enum_var is not None and len(other_vars) == 1 and domain_types.get(other_vars[0]) != "bool":
            range_var = other_vars[0]
            lo_e, hi_e = domain_bounds[enum_var]
            zones: list[tuple[float, float, tuple[float, float]]] = []
            for v in range(int(lo_e), int(hi_e) + 1):
                try:
                    cond_result = self.smt.synthesize(
                        dn, gap, domain_types,
                        positive_constraints=positive, negative_constraints=negative,
                        fixed_values={enum_var: v},
                    )
                except (Z3UNSATError, Z3TimeoutError):
                    continue
                if not cond_result.satisfiable:
                    continue
                bs = next((b for b in (cond_result.bound_specs or []) if b.var_name == range_var), None)
                if bs is not None and bs.interval is not None:
                    zones.append((v, v, bs.interval))
            if zones:
                prompt = self.prompt.build_dependency_table(
                    enum_var, range_var, zones, domain_types,
                    self.config.func_signature, self.config.domain_context,
                )
                lo_e_i, hi_e_i = int(lo_e), int(hi_e)
                rules = [
                    DomainRule(
                        field=enum_var, description=f"{enum_var} in [{lo_e_i},{hi_e_i}]",
                        validator=lambda v, lo=lo_e_i, hi=hi_e_i: (
                            isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi
                        ),
                    ),
                    DomainRule(
                        field=range_var, description=f"{range_var} numeric",
                        validator=lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
                    ),
                ]
                orig_validator = self.sampler.validator
                self.sampler.validator = DomainValidator(rules)
                try:
                    partial, _ = self.sampler.sample(prompt, fallback_values=fallback)
                    chosen_e = partial.get(enum_var)
                    chosen_r = partial.get(range_var)
                    zone = next(
                        (z for z in zones if chosen_e is not None and z[0] <= chosen_e <= z[1]), None
                    )
                    if (zone is not None and chosen_r is not None
                            and zone[2][0] <= chosen_r <= zone[2][1]):
                        return {enum_var: chosen_e, range_var: chosen_r}
                except LLMSamplingError:
                    pass
                finally:
                    self.sampler.validator = orig_validator
            return fallback  # 窮舉表沒湊出合法組合，退回 Z3 精確解保證正確

        # 沒有可窮舉的變數（例如兩邊都是大範圍連續整數）：逐一問＋條件式重算
        candidate: dict[str, object] = {}
        fixed_so_far: dict[str, object] = {}
        for v in group_vars:
            step_result = smt_result
            if fixed_so_far:
                try:
                    cond_result = self.smt.synthesize(
                        dn, gap, domain_types,
                        positive_constraints=positive, negative_constraints=negative,
                        fixed_values=fixed_so_far,
                    )
                    if cond_result.satisfiable:
                        step_result = cond_result
                except (Z3UNSATError, Z3TimeoutError):
                    pass
            bound_by_var = {b.var_name: b for b in (step_result.bound_specs or [])}
            exact_value = (step_result.model_python or {}).get(v, fallback.get(v))

            # 布林變數只有一個正確值可言，沒有範圍、沒有多樣性好挑——問 LLM
            # 只會得到「剛好答對」（白問）或「答錯退回 Z3」（也是白問）兩種
            # 結果，兩種都不需要真的問，直接省下這次 API 呼叫。
            if domain_types.get(v) == "bool":
                val = bool(exact_value)
                candidate[v] = val
                fixed_so_far[v] = val
                continue

            bs = bound_by_var.get(v)
            if bs is not None and bs.interval is not None:
                lo, hi = bs.interval
            else:
                lo, hi = domain_bounds.get(v, (0, 9999999))
            merged_context = {**candidate, **fixed_so_far}
            rules = [DomainRule(
                field=v, description=f"{v} in [{lo},{hi}]",
                validator=lambda val, lo=lo, hi=hi: (
                    isinstance(val, (int, float)) and not isinstance(val, bool) and lo <= val <= hi
                ),
            )]
            prompt = self.prompt.build_mask_patch(
                merged_context, [v], domain_types, {}, bound_by_var,
                self.config.func_signature, self.config.domain_context,
            )
            orig_validator = self.sampler.validator
            self.sampler.validator = DomainValidator(rules)
            try:
                partial, _ = self.sampler.sample(prompt, fallback_values={v: exact_value})
                val = partial.get(v, exact_value)
            except LLMSamplingError:
                val = exact_value
            finally:
                self.sampler.validator = orig_validator
            candidate[v] = val
            fixed_so_far[v] = val
        return candidate

    @staticmethod
    def _fill_missing_params(
        case: dict[str, object],
        domain_types: dict[str, str],
        domain_bounds: dict[str, list[int]] | None,
    ) -> dict[str, object]:
        """補全 LLM 輸出中缺少的函數參數。

        LLM 可能只輸出目標決策節點的條件變數（如 D1 只輸出 HC 和 TOTRV），
        缺少其他必要參數會導致函數呼叫時 TypeError，探針完全未執行。
        缺少的變數以域內隨機合法值補全，讓函數可以正常呼叫並執行探針。
        """
        result = dict(case)
        for var_name, var_type in (domain_types or {}).items():
            if var_name in result:
                continue
            if var_type == "bool":
                result[var_name] = IFLOrchestrator._random_bool(var_name, domain_bounds)
            else:
                bounds = (domain_bounds or {}).get(var_name, [0, 130])
                result[var_name] = random.randint(bounds[0], bounds[1])
        return result

    @staticmethod
    def _eval_decision(expr: str, conditions: list, combo: tuple) -> bool | None:
        """以抽象 T/F 值代入條件，靜態求值決策表達式。

        依條件表達式長度由長到短替換（避免短字串誤匹配），
        再用受限 eval 計算布林結果。
        """
        text = expr
        for cond, val in sorted(zip(conditions, combo),
                                 key=lambda x: len(x[0].expression), reverse=True):
            text = text.replace(cond.expression, "True" if val else "False")
        try:
            return bool(eval(text, {"__builtins__": {}}, {}))  # noqa: S307
        except Exception:
            return None

