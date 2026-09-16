"""
C 語言 IFL 主控迴圈。

繼承 IFLOrchestrator，僅覆寫語言相關的兩個方法：
  _prepare()   — 使用 CLibclangASTParser + CProbeInjector + CExecutor.compile_runtime()
  _run_test()  — 使用 CRuntime.run_one() 取代 Python 的 module.func()

其餘流程（SMT 合成、LLM 採樣、AcceptanceGate、IFL 迭代迴圈）完全繼承，不重複。
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from ifl_mcdc.config import IFLConfig
from ifl_mcdc.layer1.c_ast_parser import CLibclangASTParser
from ifl_mcdc.layer1.c_executor import CExecutor, CRuntime
from ifl_mcdc.layer1.c_probe_injector import CProbeInjector
from ifl_mcdc.layer3.llm_sampler import LLMBackend
from ifl_mcdc.models.decision_node import DecisionNode
from ifl_mcdc.models.probe_record import ProbeLog
from ifl_mcdc.orchestrator import IFLOrchestrator


class CIFLOrchestrator(IFLOrchestrator):
    """C 語言版 IFLOrchestrator。

    使用方式與 IFLOrchestrator 相同，但 run() 接受 .c 檔路徑：

        config = IFLConfig(
            func_name        = 'check_vaccine_eligibility',
            func_signature   = 'check_vaccine_eligibility(age, high_risk, ...)',
            domain_types     = {'age': 'int', 'high_risk': 'bool', ...},
            domain_bounds    = {'age': [0, 130], 'days_since_last': [0, 365]},
            max_iterations   = 20,
            llm_backend      = ...,
        )
        orchestrator = CIFLOrchestrator(config, func_name='check_vaccine_eligibility')
        result = orchestrator.run('tests/fixtures/vaccine_eligibility.c')
    """

    def __init__(
        self,
        config: IFLConfig,
        func_name: str,
        backend: LLMBackend | None = None,
        gcc_flags: list[str] | None = None,
    ) -> None:
        """
        Args:
            config:     與 IFLOrchestrator 相同的設定物件。
            func_name:  C 檔中被測函數的名稱（libclang 需要明確指定）。
            backend:    LLM 後端；None 則從 config.llm_backend 取得。
            gcc_flags:  gcc 編譯旗標；預設 ['-std=c11']。
        """
        super().__init__(config, backend)
        self._c_func_name = func_name
        self._gcc_flags = gcc_flags or ['-std=c11']
        self._c_work_dir: Path | None = None   # 由 _prepare 建立，run 結束後清除

    # ──────────────────────────────────────────────────────────
    # 覆寫：語言相關的初始化
    # ──────────────────────────────────────────────────────────

    def _prepare(
        self, source_path: str | Path
    ) -> tuple[list[DecisionNode], CRuntime, ProbeLog]:
        """C 版初始化：解析 → 插樁 → 編譯，回傳 (decision_nodes, CRuntime, ProbeLog)。

        work_dir 在整個 session 期間保留（供後續 run_one 使用），
        run() 結束後由 _cleanup() 清除。
        """
        filepath = Path(source_path)

        # Step 1: C AST 解析
        parser = CLibclangASTParser()
        decision_nodes = parser.parse_file(filepath, self._c_func_name)
        if not decision_nodes:
            raise ValueError(f"在 {filepath} 中找不到任何決策節點（函數：{self._c_func_name}）")

        # Step 2: 探針注入
        injector = CProbeInjector()
        instrumented_src = injector.inject(filepath, decision_nodes)

        # Step 3: 編譯一次，取得 CRuntime
        self._c_work_dir = Path(tempfile.mkdtemp(prefix='ifl_c_orch_'))
        executor = CExecutor()
        runtime = executor.compile_runtime(
            original_filepath=filepath,
            instrumented_src=instrumented_src,
            func_name=self._c_func_name,
            work_dir=self._c_work_dir,
            gcc_flags=self._gcc_flags,
        )

        log = ProbeLog()
        return decision_nodes, runtime, log

    # ──────────────────────────────────────────────────────────
    # 覆寫：單次測試執行
    # ──────────────────────────────────────────────────────────

    def _run_test(
        self,
        runtime: CRuntime,
        test_case: dict[str, Any],
        log: ProbeLog,
    ) -> str:
        """C 版：呼叫 CRuntime.run_one()，將新 ProbeRecord 追加到 log，回傳 test_id。

        介面與 Python 版 _run_test(module, test_case, log) 完全對齊，
        差別只在 runtime 為 CRuntime 而非 ModuleType。
        """
        test_id, records = runtime.run_one(test_case)
        for r in records:
            log.append(r)
        return test_id

    # ──────────────────────────────────────────────────────────
    # 覆寫：run() 加上 cleanup
    # ──────────────────────────────────────────────────────────

    def run(self, source_path: str | Path):  # type: ignore[override]
        """執行 C 版 IFL 主流程，結束後清除暫存目錄。"""
        try:
            return super().run(source_path)
        finally:
            self._cleanup()

    def _cleanup(self) -> None:
        if self._c_work_dir and self._c_work_dir.exists():
            shutil.rmtree(self._c_work_dir, ignore_errors=True)
            self._c_work_dir = None
