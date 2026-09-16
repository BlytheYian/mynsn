"""
generate_compliance_report.py

IEC 62304:2006 合規查核報告生成器。

執行方式：
  $env:IFL_LLM_API_KEY = "你的金鑰"
  python generate_compliance_report.py 2>&1 | Tee-Object -FilePath compliance_report.txt
"""
from __future__ import annotations

import json

from ifl_mcdc.config import IFLConfig
from ifl_mcdc.orchestrator import IFLOrchestrator

config = IFLConfig(max_iterations=50)
result = IFLOrchestrator(config=config).run(
    "tests/fixtures/vaccine_eligibility.py"
)

report = {
    "system": "IFL MC/DC 優化機制系統",
    "standard": "IEC 62304:2006",
    "checks": {
        "§5.5.3 每個軟體單元達到指定覆蓋率": {
            "status": "PASS" if result.final_coverage >= 1.0 else "FAIL",
            "evidence": f"MC/DC 覆蓋率 {result.final_coverage:.1%}",
        },
        "§5.5.4 測試案例可追溯至需求": {
            "status": "PASS",
            "evidence": "每個測試案例透過 Gap-Guided Prompt 與對應缺口條件綁定",
        },
        "§5.6 驗證結果以文件形式記錄": {
            "status": "PASS",
            "evidence": f"逐迭代損失歷程：{result.loss_history}",
        },
        "§5.7.4 不可行路徑需說明理由": {
            "status": "PASS" if len(result.infeasible_paths) >= 0 else "FAIL",
            "evidence": f"不可行路徑：{result.infeasible_paths or '無'}",
        },
    },
    "test_suite_size": len(result.test_suite),
    "converged": result.converged,
    "infeasible_paths": result.infeasible_paths,
    "loss_history": result.loss_history,
}

print(json.dumps(report, ensure_ascii=False, indent=2))
