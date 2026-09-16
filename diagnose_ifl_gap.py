"""
診斷腳本：追踪 IFL 何故无法改進覆蓋率
"""
import json
from pathlib import Path
from ifl_mcdc.config import IFLConfig
from ifl_mcdc.orchestrator import IFLOrchestrator

def diagnose_ifl_single_run():
    """運行單次 IFL 並詳細分析覆蓋狀況"""
    
    fixture_cfg = {
        "name": "loan_approval",
        "path": "tests/fixtures/loan_approval.py",
        "func_name": "check_loan_approval",
        "func_signature": "check_loan_approval(credit_score, annual_income, loan_amount, employed, has_collateral, bankruptcy_history)",
        "domain_context": "銀行貸款審核系統",
        "domain_types": {
            "credit_score": "int", "annual_income": "int",
            "loan_amount": "int", "employed": "bool",
            "has_collateral": "bool", "bankruptcy_history": "bool",
        },
        "domain_bounds": {
            "credit_score": [300, 850],
            "annual_income": [0, 2000000],
            "loan_amount": [10000, 10000000],
        },
    }
    
    print("\n" + "="*70)
    print("【IFL 覆蓋率診斷】loan_approval")
    print("="*70)
    
    config = IFLConfig(
        max_iterations=30,
        func_name=fixture_cfg["func_name"],
        func_signature=fixture_cfg["func_signature"],
        domain_context=fixture_cfg["domain_context"],
        domain_types=fixture_cfg["domain_types"],
        domain_bounds=fixture_cfg["domain_bounds"],
    )
    
    orch = IFLOrchestrator(config=config)
    result = orch.run(fixture_cfg["path"])
    
    # ════════════════════════════════
    # 第一部分：初始覆蓋
    # ════════════════════════════════
    print("\n【第一部分】初始測試生成覆蓋情況")
    print("-" * 70)
    
    # 統計初始有多少條件對被覆蓋
    initial_covered = 0
    for detail in result.iteration_details[:3]:  # 初始3個測試
        for gr in detail.get("gate_results", []):
            if gr.get("accepted"):
                initial_covered += 1
    
    total_pairs = len([c for c in fixture_cfg["domain_types"] if fixture_cfg["domain_types"][c] == "bool"]) * 2
    initial_coverage_pairs = 0
    
    print(f"\n初始覆蓋率：{result.final_coverage:.1%}")
    print(f"總測試數：{len(result.test_suite)}")
    print(f"迭代次數：{result.iteration_count} (目標30次)")
    
    # ════════════════════════════════
    # 第二部分：迭代進度分析
    # ════════════════════════════════
    print("\n【第二部分】30次迭代的進度分析")
    print("-" * 70)
    
    iteration_stats = []
    for i, detail in enumerate(result.iteration_details):
        iteration = detail["iteration"]
        gap_id = detail["gap"]
        accepted = sum(1 for gr in detail["gate_results"] if gr["accepted"])
        total_gate = len(detail["gate_results"])
        
        # 檢查是否有LLM或SMT錯誤
        has_error = "error" in detail
        
        iteration_stats.append({
            "iter": iteration,
            "gap": gap_id,
            "accepted": accepted,
            "total": total_gate,
            "pass_rate": accepted / total_gate if total_gate > 0 else 0,
            "has_error": has_error,
        })
    
    # 顯示前10次
    print(f"\n前10次迭代詳情（Gate 通過率）：")
    print(f"  {'迭代':>3} {'缺口':12} {'Gate結果':>12} {'狀態':<15}")
    print(f"  {'-'*50}")
    for stat in iteration_stats[:10]:
        status = "✓" if stat["pass_rate"] > 0 else "✗"
        if stat["has_error"]:
            status += " [ERROR]"
        result_str = f"{stat['accepted']}/{stat['total']}"
        print(f"  {stat['iter']:3d} {stat['gap']:12} {result_str:>12} {status:<15}")
    
    # ════════════════════════════════
    # 第三部分：缺口卡住分析
    # ════════════════════════════════
    print("\n【第三部分】缺口卡住分析")
    print("-" * 70)
    
    gap_attempt_count = {}
    gap_accepted_count = {}
    gap_errors = {}
    
    for detail in result.iteration_details:
        gap_id = detail["gap"]
        if gap_id not in gap_attempt_count:
            gap_attempt_count[gap_id] = 0
            gap_accepted_count[gap_id] = 0
            gap_errors[gap_id] = []
        
        gap_attempt_count[gap_id] += 1
        
        # 計算該缺口是否被接受
        accepted = sum(1 for gr in detail["gate_results"] if gr["accepted"])
        if accepted > 0:
            gap_accepted_count[gap_id] += 1
        
        # 記錄錯誤
        if "error" in detail:
            gap_errors[gap_id].append(detail["error"])
    
    print(f"\n在30次迭代中，各缺口的嘗試/成功比率：")
    print(f"  {'缺口':12} {'嘗試次數':>8} {'成功次數':>8} {'成功率':>8} {'備註':<30}")
    print(f"  {'-'*70}")
    for gap_id in sorted(gap_attempt_count.keys(), key=lambda x: -gap_attempt_count[x]):
        attempts = gap_attempt_count[gap_id]
        successes = gap_accepted_count[gap_id]
        success_rate = successes / attempts if attempts > 0 else 0
        
        error_note = ""
        if gap_id in gap_errors and gap_errors[gap_id]:
            error_note = f"[{len(gap_errors[gap_id])} 個LLM/SMT錯誤]"
        elif successes == 0:
            error_note = "[全部被Gate拒絕]"
        
        print(f"  {gap_id:12} {attempts:>8} {successes:>8} {success_rate:>7.1%} {error_note:<30}")
    
    # ════════════════════════════════
    # 第四部分：Token 消耗分析
    # ════════════════════════════════
    print("\n【第四部分】Token 消耗分析")
    print("-" * 70)
    
    total_tokens = result.total_tokens
    expected_tokens = 30 * 1300  # 粗略估算：30次迭代 × 1300 tokens/迭代
    actual_ratio = total_tokens / expected_tokens if expected_tokens > 0 else 0
    
    print(f"\n實際 Token 消耗：{total_tokens}")
    print(f"預期 Token 消耗：≈{expected_tokens} (30 iter × 1300/iter)")
    print(f"實際 / 預期比率：{actual_ratio:.1%} ← {'✓ 合理' if 0.5 < actual_ratio < 1.0 else '✗ 異常低！'}")
    
    if actual_ratio < 0.5:
        print(f"\n⚠️  WARNING: 實際 Token 遠低於預期")
        print(f"   可能原因：")
        print(f"   1. 迭代過早終止 (不足30次)")
        print(f"   2. 大量Gate拒絕導致LLM不被調用")
        print(f"   3. LLM返回非常短的回應")
    
    # ════════════════════════════════
    # 第五部分：失敗分析
    # ════════════════════════════════
    print("\n【第五部分】失敗類型統計")
    print("-" * 70)
    
    failure_log = result.failure_log
    llm_fails = [f for f in failure_log if "LLM" in f]
    smt_fails = [f for f in failure_log if "SMT" in f]
    gate_rejects = sum(
        1 for detail in result.iteration_details 
        for gr in detail["gate_results"] 
        if not gr["accepted"]
    )
    
    print(f"\nLLM 生成失敗：{len(llm_fails)} 次")
    print(f"SMT 求解失敗：{len(smt_fails)} 次")
    print(f"Gate 拒絕：{gate_rejects} 次 (總共 {len(result.iteration_details) * 2} 個候選)")
    print(f"Gate 拒絕率：{gate_rejects / (len(result.iteration_details) * 2) * 100:.1f}%")
    
    # ════════════════════════════════
    # 第六部分：建議
    # ════════════════════════════════
    print("\n【診斷結論】")
    print("-" * 70)
    
    # 分析覆蓋率沒有改進的原因
    high_gate_reject = gate_rejects / (len(result.iteration_details) * 2) > 0.8
    high_error_rate = (len(llm_fails) + len(smt_fails)) / len(result.iteration_details) > 0.5
    low_token_usage = actual_ratio < 0.5
    stuck_on_gaps = len(gap_attempt_count) <= 3
    
    print(f"\n❌ 覆蓋率無改進的根本原因：\n")
    
    if high_gate_reject:
        print(f"   1. ⚠️  【嚴重】Gate 拒絕率過高 ({gate_rejects / (len(result.iteration_details) * 2) * 100:.1f}%)")
        print(f"      → 含义：LLM和SMT生成的測試都無法改進MC/DC損失")
        print(f"      → 原因可能：")
        print(f"         • 難缺口的補集無解（Z3求解失敗）")
        print(f"         • LLM生成的測試無法觸發該補集")
        print(f"         • AcceptanceGate損失計算有誤")
    
    if stuck_on_gaps:
        print(f"\n   2. ⚠️  【嚴重】迭代卡在極少數缺口 ({len(gap_attempt_count)} 個缺口)")
        print(f"      → 含义：無法均勻覆蓋所有缺口")
        print(f"      → 原因可能：")
        print(f"         • GapAnalyzer 選擇策略有誤（總是選同一個缺口）")
        print(f"         • 某些缺口無法通過LLM解決（需要人工或其他方法）")
    
    if high_error_rate:
        print(f"\n   3. ⚠️  LLM/SMT 錯誤率高 ({(len(llm_fails) + len(smt_fails)) / len(result.iteration_details) * 100:.1f}%)")
        print(f"      → 可能導致大量迭代白費")
    
    if low_token_usage:
        print(f"\n   4. ⚠️  Token 消耗極低 ({total_tokens} << {expected_tokens})")
        print(f"      → 可能表示系統無法正常執行30次迭代")
    
    # 提出改進建議
    print(f"\nO 改進建議：\n")
    print(f"   • 檢查 AcceptanceGate 的損失判斷邏輯")
    print(f"   • 檢查 GapAnalyzer 的缺口選擇策略（是否有多樣化）")
    print(f"   • 檢查 SMTSynthesizer 對難缺口補集的求解能力")
    print(f"   • 啟用 error_history，讓LLM知道前面失敗的原因")
    print(f"   • 考慮鬆綁 coverage_ratio 要求，允許部分改進被接受")
    
    print("\n" + "="*70)


if __name__ == "__main__":
    diagnose_ifl_single_run()
