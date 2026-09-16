"""
diversity_reporter.py — 多樣性指標 D1~D8 計算與報告模組。

D1  唯一性率       unique/total ≥ 0.70
D2  函式輸出平衡   True% ∈ [25%, 75%]
D3  條件激活覆蓋   每個條件 True% ∈ [5%, 95%]
D4  整數邊界偏向   boundary_ratio ≤ 0.70
D5  Shannon Entropy H_n ≥ 0.72  (Shannon 1948)
D6  Bin Coverage   bins_covered/n_bins ≥ 0.80  (Sturges' Rule)
D7  Bootstrap KS   p > 0.05 PASS  (Kolmogorov-Smirnov)
D8  Wasserstein    W ≤ 0.15  (Wasserstein 1969)
"""
from __future__ import annotations

import ast as _ast
import json
import math
import statistics
from typing import Any

import numpy as np
from scipy.stats import kstest, wasserstein_distance

from ifl_mcdc.layer1.ast_parser import ASTParser
from validation_fixtures import FixtureSpec

# ── 閾值 ─────────────────────────────────────────────────────────
THRESH_D1_UNIQUENESS: float = 0.70
THRESH_D2_OUTPUT_LO:  float = 0.25
THRESH_D2_OUTPUT_HI:  float = 0.75
THRESH_D3_COND_LO:    float = 0.05
THRESH_D3_COND_HI:    float = 0.95
THRESH_D4_BOUNDARY:   float = 0.70
THRESH_D5_ENTROPY:    float = 0.72   # normalized Shannon entropy (Shannon 1948)
THRESH_D6_BIN_COV:    float = 0.80   # Sturges' Rule bin coverage
THRESH_D7_KS_P:       float = 0.05   # Bootstrap KS p-value threshold
THRESH_D8_WASS:       float = 0.15   # Wasserstein distance (Wasserstein 1969)

MIN_SAMPLES_FOR_STATS: int = 10   # 統計指標 D5~D8 的最低樣本數要求

_PASS = "PASS"
_FAIL = "FAIL"
_W    = 62   # 報告輸出寬度


def _mark(ok: bool) -> str:
    return f"[{_PASS}]" if ok else f"[{_FAIL}]"


# ══════════════════════════════════════════════════════════════
#  D1  唯一性率
# ══════════════════════════════════════════════════════════════

def compute_d1(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """D1: 唯一性率 = 不重複案例 / 總案例數。閾值：≥ 0.70。"""
    total  = len(cases)
    frozen = [json.dumps(c, sort_keys=True, default=str) for c in cases]
    unique = len(set(frozen))
    dups   = total - unique
    rate   = unique / total if total > 0 else 0.0
    return {
        "total":     total,
        "unique":    unique,
        "dups":      dups,
        "rate":      rate,
        "pass":      rate >= THRESH_D1_UNIQUENESS,
        "threshold": THRESH_D1_UNIQUENESS,
    }


# ══════════════════════════════════════════════════════════════
#  D2  函式輸出平衡
# ══════════════════════════════════════════════════════════════

def compute_d2(outputs: list[bool]) -> dict[str, Any]:
    """D2: 函式輸出 True% ∈ [25%, 75%]。

    MC/DC 以 True/False 配對方式生成，故輸出比例天然趨向平衡。
    閾值：[25%, 75%]。
    """
    if not outputs:
        return {"total": 0, "true_n": 0, "true_p": 0.0, "pass": False, "note": "無輸出"}
    true_n = sum(outputs)
    true_p = true_n / len(outputs)
    return {
        "total":     len(outputs),
        "true_n":    true_n,
        "true_p":    true_p,
        "pass":      THRESH_D2_OUTPUT_LO <= true_p <= THRESH_D2_OUTPUT_HI,
        "threshold": [THRESH_D2_OUTPUT_LO, THRESH_D2_OUTPUT_HI],
    }


# ══════════════════════════════════════════════════════════════
#  輔助：條件激活計數（D3 前置步驟）
# ══════════════════════════════════════════════════════════════

def compute_condition_activation(
    cases: list[dict[str, Any]],
    spec: FixtureSpec,
) -> dict[str, dict[str, int]]:
    """輔助：計算每個原子條件的 True/False 觸發次數。

    Returns:
        {condition_expression: {"true": n, "false": m}}
    """
    parser = ASTParser()
    nodes  = parser.parse_file(spec.path)
    dn     = nodes[0]

    activation: dict[str, dict[str, int]] = {}
    for cond in dn.condition_set.conditions:
        activation[cond.expression] = {"true": 0, "false": 0}

    for case in cases:
        for cond in dn.condition_set.conditions:
            try:
                raw = bool(eval(cond.expression, {"__builtins__": {}}, dict(case)))  # noqa: S307
                val = (not raw) if cond.negated else raw
            except Exception:
                continue
            activation[cond.expression]["true" if val else "false"] += 1

    return activation


# ══════════════════════════════════════════════════════════════
#  D3  條件激活覆蓋
# ══════════════════════════════════════════════════════════════

def compute_d3(activation: dict[str, dict[str, int]]) -> dict[str, Any]:
    """D3: 每個原子條件的 True% ∈ [5%, 95%]。

    閾值比 MC/DC 最低要求（>0 & <100%）更嚴，確保雙向激活分布不偏向。
    """
    cond_results: dict[str, dict[str, Any]] = {}
    all_pass = True

    for expr, counts in activation.items():
        t = counts["true"]
        f = counts["false"]
        n = t + f
        true_p = t / n if n > 0 else 0.0
        ok = THRESH_D3_COND_LO <= true_p <= THRESH_D3_COND_HI
        all_pass = all_pass and ok
        cond_results[expr] = {
            "true_n":  t,
            "false_n": f,
            "true_p":  true_p,
            "pass":    ok,
        }

    return {
        "conditions": cond_results,
        "all_pass":   all_pass,
        "threshold":  [THRESH_D3_COND_LO, THRESH_D3_COND_HI],
    }


# ══════════════════════════════════════════════════════════════
#  D4  整數邊界偏向
# ══════════════════════════════════════════════════════════════

def compute_d4(
    cases: list[dict[str, Any]],
    domain_types: dict[str, str],
    domain_bounds: dict[str, list[int]],
    spec: FixtureSpec,
) -> dict[str, Any]:
    """D4: 整數欄位中恰落在 MC/DC 邊界臨界值（±1）的比例。

    比例過高代表 Z3 總是回傳最小解，測試集缺乏空間多樣性。
    閾值：≤ 0.70。
    """
    parser = ASTParser()
    nodes  = parser.parse_file(spec.path)
    dn     = nodes[0]

    thresholds: dict[str, set[int]] = {}
    for cond in dn.condition_set.conditions:
        try:
            tree = _ast.parse(cond.expression, mode="eval")
            body = tree.body
            if isinstance(body, _ast.Compare) and isinstance(body.left, _ast.Name):
                var = body.left.id
                if domain_types.get(var) == "int":
                    for comp in body.comparators:
                        if isinstance(comp, _ast.Constant) and isinstance(comp.value, int):
                            thresholds.setdefault(var, set())
                            thresholds[var].add(comp.value)
                            thresholds[var].add(comp.value - 1)
        except Exception:
            continue

    if not thresholds:
        return {"boundary_ratio": 0.0, "pass": True, "detail": {}, "threshold": THRESH_D4_BOUNDARY,
                "note": "無整數比較條件（或無法解析）"}

    detail: dict[str, dict[str, Any]] = {}
    total_vals    = 0
    boundary_vals = 0

    for f, t in domain_types.items():
        if t != "int" or f not in thresholds:
            continue
        vals = [int(float(c[f])) for c in cases if f in c and not isinstance(c[f], bool)]
        if not vals:
            continue
        bdry_set    = thresholds[f]
        on_boundary = sum(1 for v in vals if v in bdry_set)
        total_vals    += len(vals)
        boundary_vals += on_boundary
        detail[f] = {
            "n":          len(vals),
            "boundary_n": on_boundary,
            "boundary_p": on_boundary / len(vals),
            "thresholds": sorted(bdry_set),
        }

    ratio = boundary_vals / total_vals if total_vals > 0 else 0.0
    return {
        "boundary_ratio": ratio,
        "boundary_n":     boundary_vals,
        "total_n":        total_vals,
        "pass":           ratio <= THRESH_D4_BOUNDARY,
        "threshold":      THRESH_D4_BOUNDARY,
        "detail":         detail,
    }


# ══════════════════════════════════════════════════════════════
#  D5  Shannon Entropy（Shannon 1948）
# ══════════════════════════════════════════════════════════════

def compute_d5(
    cases: list[dict[str, Any]],
    domain_types: dict[str, str],
    domain_bounds: dict[str, list[int]],
) -> dict[str, Any]:
    """D5: 正規化 Shannon Entropy。

    每個 int 欄位離散化為 n_bins = ceil(sqrt(n)) 個 bin（Sturges' Rule），
    計算 H_n = H / log2(n_bins)。各欄位 H_n 取平均值。

    理論依據：Shannon, C.E. (1948). A Mathematical Theory of Communication.
    閾值：avg_H_n ≥ 0.72（最大值=1.0，越大表示分布越均勻）。
    n < MIN_SAMPLES_FOR_STATS 時輸出警告並跳過。
    """
    n = len(cases)
    if n < MIN_SAMPLES_FOR_STATS:
        return {
            "skip": True, "pass": True,
            "note": f"樣本數={n} < {MIN_SAMPLES_FOR_STATS}，略過 D5",
        }

    int_fields = [f for f, t in domain_types.items() if t == "int"]
    if not int_fields:
        return {"skip": True, "pass": True, "note": "無整數欄位，略過 D5"}

    field_results: dict[str, dict[str, Any]] = {}
    entropies: list[float] = []

    for f in int_fields:
        vals = [int(float(c[f])) for c in cases if f in c and not isinstance(c[f], bool)]
        if len(vals) < 2:
            continue
        lo, hi       = domain_bounds.get(f, [min(vals), max(vals) + 1])
        domain_range = hi - lo if hi > lo else 1
        n_bins       = max(2, math.ceil(math.sqrt(len(vals))))
        bin_width    = domain_range / n_bins

        counts = [0] * n_bins
        for v in vals:
            idx = min(int((v - lo) / bin_width), n_bins - 1)
            counts[idx] += 1

        total = len(vals)
        H = sum(-c / total * math.log2(c / total) for c in counts if c > 0)
        H_max = math.log2(n_bins)
        H_n   = H / H_max if H_max > 0 else 0.0

        entropies.append(H_n)
        field_results[f] = {
            "n_bins": n_bins,
            "H":      round(H, 4),
            "H_n":    round(H_n, 4),
            "H_max":  round(H_max, 4),
        }

    if not entropies:
        return {"skip": True, "pass": True, "note": "無有效整數欄位，略過 D5"}

    avg_H_n = statistics.mean(entropies)
    return {
        "avg_H_n":   round(avg_H_n, 4),
        "fields":    field_results,
        "pass":      avg_H_n >= THRESH_D5_ENTROPY,
        "threshold": THRESH_D5_ENTROPY,
        "skip":      False,
    }


# ══════════════════════════════════════════════════════════════
#  D6  Bin Coverage（Sturges' Rule）
# ══════════════════════════════════════════════════════════════

def compute_d6(
    cases: list[dict[str, Any]],
    domain_types: dict[str, str],
    domain_bounds: dict[str, list[int]],
) -> dict[str, Any]:
    """D6: Bin Coverage — 每個 bin 至少包含一個樣本的比例。

    n_bins = ceil(sqrt(n))（Sturges' Rule）。
    閾值：avg_coverage ≥ 80%。
    n < MIN_SAMPLES_FOR_STATS 時輸出警告並跳過。
    """
    n = len(cases)
    if n < MIN_SAMPLES_FOR_STATS:
        return {
            "skip": True, "pass": True,
            "note": f"樣本數={n} < {MIN_SAMPLES_FOR_STATS}，略過 D6",
        }

    int_fields = [f for f, t in domain_types.items() if t == "int"]
    if not int_fields:
        return {"skip": True, "pass": True, "note": "無整數欄位，略過 D6"}

    field_results: dict[str, dict[str, Any]] = {}
    coverages: list[float] = []

    for f in int_fields:
        vals = [int(float(c[f])) for c in cases if f in c and not isinstance(c[f], bool)]
        if len(vals) < 2:
            continue
        lo, hi       = domain_bounds.get(f, [min(vals), max(vals) + 1])
        domain_range = hi - lo if hi > lo else 1
        n_bins       = max(2, math.ceil(math.sqrt(len(vals))))
        bin_width    = domain_range / n_bins

        bins_hit: set[int] = set()
        for v in vals:
            idx = min(int((v - lo) / bin_width), n_bins - 1)
            bins_hit.add(idx)

        coverage = len(bins_hit) / n_bins
        coverages.append(coverage)
        field_results[f] = {
            "n_bins":   n_bins,
            "bins_hit": len(bins_hit),
            "coverage": round(coverage, 4),
        }

    if not coverages:
        return {"skip": True, "pass": True, "note": "無有效整數欄位，略過 D6"}

    avg_coverage = statistics.mean(coverages)
    return {
        "avg_coverage": round(avg_coverage, 4),
        "fields":       field_results,
        "pass":         avg_coverage >= THRESH_D6_BIN_COV,
        "threshold":    THRESH_D6_BIN_COV,
        "skip":         False,
    }


# ══════════════════════════════════════════════════════════════
#  D7  Bootstrap KS 檢定（Kolmogorov–Smirnov）
# ══════════════════════════════════════════════════════════════

def compute_d7(
    cases: list[dict[str, Any]],
    domain_types: dict[str, str],
    domain_bounds: dict[str, list[int]],
    n_bootstrap: int = 1000,
) -> dict[str, Any]:
    """D7: Bootstrap KS 檢定 — H0: 樣本來自均勻分布。

    1. 計算觀測 KS 統計量 D_obs（vs Uniform(0,1)）
    2. 從 Uniform(0,1) 抽 n_bootstrap 組同大小樣本，各計算 D_boot
    3. p_value = Pr(D_boot ≥ D_obs)
    4. p > 0.05 → 無法拒絕均勻假設 → PASS

    閾值：p > 0.05（雙側，H0=均勻分布）。
    n < MIN_SAMPLES_FOR_STATS 時輸出警告並跳過。
    """
    n = len(cases)
    if n < MIN_SAMPLES_FOR_STATS:
        return {
            "skip": True, "pass": True,
            "note": f"樣本數={n} < {MIN_SAMPLES_FOR_STATS}，略過 D7",
        }

    int_fields = [f for f, t in domain_types.items() if t == "int"]
    if not int_fields:
        return {"skip": True, "pass": True, "note": "無整數欄位，略過 D7"}

    rng = np.random.default_rng(42)
    field_results: dict[str, dict[str, Any]] = {}

    for f in int_fields:
        vals = [int(float(c[f])) for c in cases if f in c and not isinstance(c[f], bool)]
        if len(vals) < MIN_SAMPLES_FOR_STATS:
            continue
        lo, hi       = domain_bounds.get(f, [min(vals), max(vals) + 1])
        domain_range = hi - lo if hi > lo else 1

        norm_vals = np.clip(
            np.array([(v - lo) / domain_range for v in vals], dtype=float),
            0.0, 1.0,
        )

        d_obs, _ = kstest(norm_vals, "uniform")

        m          = len(norm_vals)
        boot_stats = np.array([
            kstest(rng.uniform(0, 1, m), "uniform")[0]
            for _ in range(n_bootstrap)
        ])
        p_value = float(np.mean(boot_stats >= d_obs))

        field_results[f] = {
            "d_obs":       round(float(d_obs), 4),
            "p_value":     round(p_value, 4),
            "pass":        p_value > THRESH_D7_KS_P,
            "n_bootstrap": n_bootstrap,
        }

    if not field_results:
        return {"skip": True, "pass": True, "note": "無有效整數欄位，略過 D7"}

    all_pass = all(r["pass"] for r in field_results.values())
    return {
        "fields":      field_results,
        "all_pass":    all_pass,
        "pass":        all_pass,
        "threshold":   THRESH_D7_KS_P,
        "n_bootstrap": n_bootstrap,
        "skip":        False,
    }


# ══════════════════════════════════════════════════════════════
#  D8  Wasserstein 距離（Wasserstein 1969）
# ══════════════════════════════════════════════════════════════

def compute_d8(
    cases: list[dict[str, Any]],
    domain_types: dict[str, str],
    domain_bounds: dict[str, list[int]],
) -> dict[str, Any]:
    """D8: Wasserstein W1 距離 — 觀測分布 vs 均勻分布。

    使用 scipy.stats.wasserstein_distance，參考樣本為 linspace(0,1,1000)。
    理論依據：Wasserstein, L. (1969).
    閾值：max_W ≤ 0.15（值域 [0,1]，越小越接近均勻分布）。
    n < MIN_SAMPLES_FOR_STATS 時輸出警告並跳過。
    """
    n = len(cases)
    if n < MIN_SAMPLES_FOR_STATS:
        return {
            "skip": True, "pass": True,
            "note": f"樣本數={n} < {MIN_SAMPLES_FOR_STATS}，略過 D8",
        }

    int_fields = [f for f, t in domain_types.items() if t == "int"]
    if not int_fields:
        return {"skip": True, "pass": True, "note": "無整數欄位，略過 D8"}

    uniform_ref  = np.linspace(0.0, 1.0, 1000)
    field_results: dict[str, dict[str, Any]] = {}
    w_values: list[float] = []

    for f in int_fields:
        vals = [int(float(c[f])) for c in cases if f in c and not isinstance(c[f], bool)]
        if len(vals) < MIN_SAMPLES_FOR_STATS:
            continue
        lo, hi       = domain_bounds.get(f, [min(vals), max(vals) + 1])
        domain_range = hi - lo if hi > lo else 1

        norm_vals = np.clip(
            np.array([(v - lo) / domain_range for v in vals], dtype=float),
            0.0, 1.0,
        )

        W = float(wasserstein_distance(norm_vals, uniform_ref))
        w_values.append(W)
        field_results[f] = {
            "W":    round(W, 4),
            "pass": W <= THRESH_D8_WASS,
        }

    if not w_values:
        return {"skip": True, "pass": True, "note": "無有效整數欄位，略過 D8"}

    max_W    = max(w_values)
    all_pass = all(r["pass"] for r in field_results.values())
    return {
        "max_W":     round(max_W, 4),
        "fields":    field_results,
        "all_pass":  all_pass,
        "pass":      all_pass,
        "threshold": THRESH_D8_WASS,
        "skip":      False,
    }


# ══════════════════════════════════════════════════════════════
#  結構性偏向分析
# ══════════════════════════════════════════════════════════════

def analyze_structural_bias(spec: FixtureSpec) -> dict[str, Any]:
    """解析 Fixture AST，找出 AND 鏈中的必要條件及其理論偏向下界。

    在 AND 邏輯結構中，MC/DC unique-cause 測試需其餘條件保持 True，
    因此 True 側案例中 AND 鏈條件的 True% 有結構性下界：

        P_lower = (2k-2) / (2k)    （k = AND 鏈條件數）

    此偏向屬 MC/DC 結構性限制，非測試系統缺陷。
    """
    parser = ASTParser()
    nodes  = parser.parse_file(spec.path)
    dn     = nodes[0]
    k      = len(dn.condition_set.conditions)

    if k <= 1:
        return {
            "and_chain_k": k, "p_lower": 0.0,
            "and_conditions": [], "note": "單一條件，無結構性偏向",
        }

    p_lower = (2 * k - 2) / (2 * k)

    # 嘗試解析頂層 AND 鏈（找第一個 if 語句）
    and_conditions: list[str] = []
    try:
        with open(spec.path, encoding="utf-8") as fh:
            src = fh.read()
        tree = _ast.parse(src)
        for node in _ast.walk(tree):
            if isinstance(node, _ast.If):
                cond = node.test
                if isinstance(cond, _ast.BoolOp) and isinstance(cond.op, _ast.And):
                    and_conditions = [_ast.unparse(v) for v in cond.values]
                break
    except Exception:
        pass

    return {
        "and_chain_k":    k,
        "p_lower":        round(p_lower, 4),
        "and_conditions": and_conditions,
        "note": (
            f"AND 鏈 k={k}，理論 True% 下界={p_lower:.1%}（MC/DC 結構性限制，非系統缺陷）"
            if and_conditions else
            f"k={k}，未偵測到純 AND 鏈（可能含 OR 或巢狀結構）"
        ),
    }


# ══════════════════════════════════════════════════════════════
#  終端機報告輸出
# ══════════════════════════════════════════════════════════════

def print_report(
    spec: FixtureSpec,
    n_runs: int,
    cases: list[dict[str, Any]],
    outputs: list[bool],
    coverages: list[bool],
    iters: list[int],
    d1: dict[str, Any],
    d2: dict[str, Any],
    d3: dict[str, Any],
    d4: dict[str, Any],
    d5: dict[str, Any],
    d6: dict[str, Any],
    d7: dict[str, Any],
    d8: dict[str, Any],
    bias: dict[str, Any],
) -> None:
    """將 D1~D8 全部結果輸出至終端機。"""
    W          = _W
    conv_rate  = sum(coverages) / len(coverages) if coverages else 0.0
    avg_iter   = statistics.mean(iters) if iters else 0.0

    print(f"\n{'='*W}")
    print(f"  多樣性報告：{spec.label}")
    print(f"  N_RUNS={n_runs}，總案例={d1['total']}，"
          f"收斂率={conv_rate:.0%}，平均迭代={avg_iter:.1f}")
    print(f"{'='*W}")

    # ── D1 ────────────────────────────────────────────────────
    print(f"\n  D1 唯一性率              {_mark(d1['pass'])}")
    print(f"     total={d1['total']}  unique={d1['unique']}  dups={d1['dups']}")
    print(f"     唯一率={d1['rate']:.1%}  （閾值 ≥ {THRESH_D1_UNIQUENESS:.0%}）")

    # ── D2 ────────────────────────────────────────────────────
    print(f"\n  D2 函式輸出平衡          {_mark(d2['pass'])}")
    print(f"     True={d2['true_n']}/{d2['total']}={d2['true_p']:.1%}  "
          f"（閾值 [{THRESH_D2_OUTPUT_LO:.0%}, {THRESH_D2_OUTPUT_HI:.0%}]）")

    # ── D3 ────────────────────────────────────────────────────
    print(f"\n  D3 條件激活覆蓋          {_mark(d3['all_pass'])}")
    print(f"     閾值：每個條件 True% ∈ [{THRESH_D3_COND_LO:.0%}, {THRESH_D3_COND_HI:.0%}]")
    print(f"     {'條件表達式':<36} {'True':>6} {'False':>6} {'True%':>7}  {'判定':>6}")
    print(f"     {'-'*62}")
    for expr, info in d3["conditions"].items():
        short = (expr[:33] + "...") if len(expr) > 36 else expr
        print(
            f"     {short:<36} {info['true_n']:>6} {info['false_n']:>6}"
            f" {info['true_p']:>6.1%}  {_mark(info['pass'])}"
        )

    # ── D4 ────────────────────────────────────────────────────
    print(f"\n  D4 整數邊界偏向          {_mark(d4['pass'])}")
    if d4.get("detail"):
        print(f"     整體邊界率={d4['boundary_ratio']:.1%}  "
              f"（閾值 ≤ {THRESH_D4_BOUNDARY:.0%}）")
        for f, info in d4["detail"].items():
            print(f"     {f}: boundary={info['boundary_n']}/{info['n']}="
                  f"{info['boundary_p']:.1%}  閾值={info['thresholds']}")
    else:
        print(f"     {d4.get('note', '無整數比較條件')}")

    # ── D5 ────────────────────────────────────────────────────
    if d5.get("skip"):
        print(f"\n  D5 Shannon Entropy       [SKIP] {d5.get('note', '')}")
    else:
        print(f"\n  D5 Shannon Entropy       {_mark(d5['pass'])}")
        print(f"     avg_H_n={d5['avg_H_n']:.4f}  （閾值 ≥ {THRESH_D5_ENTROPY:.2f}，"
              f"最大=1.0；理論依據：Shannon 1948）")
        for f, info in d5.get("fields", {}).items():
            print(f"     {f}: H={info['H']:.3f}  H_n={info['H_n']:.4f}  "
                  f"n_bins={info['n_bins']}")

    # ── D6 ────────────────────────────────────────────────────
    if d6.get("skip"):
        print(f"\n  D6 Bin Coverage          [SKIP] {d6.get('note', '')}")
    else:
        print(f"\n  D6 Bin Coverage          {_mark(d6['pass'])}")
        print(f"     avg_coverage={d6['avg_coverage']:.1%}  "
              f"（閾值 ≥ {THRESH_D6_BIN_COV:.0%}；n_bins=ceil(sqrt(n))）")
        for f, info in d6.get("fields", {}).items():
            print(f"     {f}: {info['bins_hit']}/{info['n_bins']} bins "
                  f"= {info['coverage']:.1%}")

    # ── D7 ────────────────────────────────────────────────────
    if d7.get("skip"):
        print(f"\n  D7 Bootstrap KS          [SKIP] {d7.get('note', '')}")
    else:
        print(f"\n  D7 Bootstrap KS 檢定     {_mark(d7['pass'])}")
        print(f"     n_bootstrap={d7['n_bootstrap']}，H0=均勻分布，"
              f"p > {THRESH_D7_KS_P} 為 PASS")
        for f, info in d7.get("fields", {}).items():
            print(f"     {f}: D_obs={info['d_obs']:.4f}  "
                  f"p={info['p_value']:.4f}  {_mark(info['pass'])}")

    # ── D8 ────────────────────────────────────────────────────
    if d8.get("skip"):
        print(f"\n  D8 Wasserstein 距離      [SKIP] {d8.get('note', '')}")
    else:
        print(f"\n  D8 Wasserstein 距離      {_mark(d8['pass'])}")
        print(f"     max_W={d8['max_W']:.4f}  （閾值 ≤ {THRESH_D8_WASS:.2f}；"
              f"理論依據：Wasserstein 1969）")
        for f, info in d8.get("fields", {}).items():
            print(f"     {f}: W={info['W']:.4f}  {_mark(info['pass'])}")

    # ── 結構性偏向說明 ────────────────────────────────────────
    if bias.get("and_conditions"):
        print(f"\n  [結構性偏向分析]")
        print(f"     {bias['note']}")
        conds_str = ", ".join(bias["and_conditions"][:5])
        if len(bias["and_conditions"]) > 5:
            conds_str += f"...（共 {len(bias['and_conditions'])} 個）"
        print(f"     AND 鏈條件：{conds_str}")
        print(f"     ⇒ 這些條件的 True% 偏高屬 MC/DC 結構性限制，非測試系統缺陷")

    # ── 綜合判定 ──────────────────────────────────────────────
    all_pass = (
        d1["pass"]
        and d2["pass"]
        and d3["all_pass"]
        and d4["pass"]
        and d5.get("pass", True)
        and d6.get("pass", True)
        and d7.get("pass", True)
        and d8.get("pass", True)
    )
    print(f"\n  {'='*W}")
    verdict = _PASS if all_pass else _FAIL
    print(f"  綜合多樣性判定：[{verdict}]")
    print(f"  {'='*W}")


# ══════════════════════════════════════════════════════════════
#  run_experiments.py 專用簡化介面（D1~D3）
#
#  與上方 D1~D8 系統指標相互獨立，供三組對照實驗使用。
#  函式簽名設計為不依賴 FixtureSpec，直接接收欄位清單。
# ══════════════════════════════════════════════════════════════

_MIN_SAMPLES_D23: int = 5   # D2/D3 的最低樣本數；不足時略過並標注


def compute_d1_uniqueness(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """D1: 唯一性率 = 不重複案例 / 總案例數。

    排除 __test_id、__source 等內部欄位後以 JSON hash 判斷重複。
    閾值：uniqueness_rate ≥ 0.70。

    Returns:
        {
          "total": 38,
          "unique": 33,
          "duplicates": 5,
          "uniqueness_rate": 0.868,
          "pass": true
        }
    """
    clean  = [{k: v for k, v in c.items() if not k.startswith("__")} for c in cases]
    total  = len(clean)
    if total == 0:
        return {"total": 0, "unique": 0, "duplicates": 0, "uniqueness_rate": 0.0, "pass": False}
    frozen = [json.dumps(c, sort_keys=True, default=str) for c in clean]
    unique = len(set(frozen))
    rate   = unique / total
    return {
        "total":          total,
        "unique":         unique,
        "duplicates":     total - unique,
        "uniqueness_rate": round(rate, 4),
        "pass":           rate >= THRESH_D1_UNIQUENESS,
    }


def compute_d2_entropy(
    cases: list[dict[str, Any]],
    bool_fields: list[str],
) -> dict[str, Any]:
    """D2: 每個布林欄位的 Shannon Entropy。

    理論依據：Shannon (1948).
    p = True 案例比例，clip 至 (1e-10, 1-1e-10)
    H = -p*log2(p) - (1-p)*log2(1-p)
    閾值：H ≥ 0.72
    structural_bias = True% > 75% 且 H < 0.72

    案例數 < 5 時略過並標注「樣本不足」。

    Returns:
        {
          "has_diabetes": {
            "true_rate": 0.881,
            "entropy": 0.506,
            "pass": false,
            "structural_bias": true
          },
          ...
        }
        或 {"skip": true, "note": "..."} 當樣本不足
    """
    n = len(cases)
    if n < _MIN_SAMPLES_D23:
        return {
            "skip": True,
            "note": f"樣本數={n} < {_MIN_SAMPLES_D23}，略過 D2（Shannon Entropy）",
        }

    result: dict[str, Any] = {}
    for f in bool_fields:
        vals = [c[f] for c in cases if f in c and isinstance(c[f], bool)]
        if not vals:
            continue
        p       = sum(1 for v in vals if v) / len(vals)
        p_clip  = max(1e-10, min(1.0 - 1e-10, p))
        H       = -p_clip * math.log2(p_clip) - (1.0 - p_clip) * math.log2(1.0 - p_clip)
        result[f] = {
            "true_rate":       round(p, 4),
            "entropy":         round(H, 4),
            "pass":            H >= THRESH_D5_ENTROPY,   # 複用 0.72 閾值
            "structural_bias": p > 0.75 and H < THRESH_D5_ENTROPY,
        }
    return result


def compute_d3_wasserstein(
    cases: list[dict[str, Any]],
    int_fields: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    """D3: 每個整數欄位與均勻分布的 Wasserstein 距離。

    理論依據：Wasserstein (1969).
    norm = (v - lo) / (hi - lo)
    W = wasserstein_distance(norm_values, linspace(0,1,1000))
    閾值：W ≤ 0.15

    Args:
        int_fields: {欄位名: (domain_min, domain_max)}

    案例數 < 5 時略過並標注「樣本不足」。

    Returns:
        {
          "credit_score": {
            "wasserstein": 0.089,
            "pass": true
          },
          ...
        }
        或 {"skip": true, "note": "..."} 當樣本不足
    """
    n = len(cases)
    if n < _MIN_SAMPLES_D23:
        return {
            "skip": True,
            "note": f"樣本數={n} < {_MIN_SAMPLES_D23}，略過 D3（Wasserstein）",
        }

    uniform_ref = np.linspace(0.0, 1.0, 1000)
    result: dict[str, Any] = {}

    for f, (lo, hi) in int_fields.items():
        vals = [int(float(c[f])) for c in cases if f in c and not isinstance(c[f], bool)]
        if not vals:
            continue
        domain_range = hi - lo if hi > lo else 1
        norm_vals = np.clip(
            np.array([(v - lo) / domain_range for v in vals], dtype=float),
            0.0, 1.0,
        )
        W = float(wasserstein_distance(norm_vals, uniform_ref))
        result[f] = {
            "wasserstein": round(W, 4),
            "pass":        W <= THRESH_D8_WASS,   # 複用 0.15 閾值
        }
    return result


def compute_diversity_metrics(
    cases: list[dict[str, Any]],
    bool_fields: list[str],
    int_fields: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    """同時計算 D1~D3，回傳整合結果。

    Args:
        cases:       測試案例列表（可含 __source / __test_id 等內部欄位）。
        bool_fields: 布林型欄位名稱列表。
        int_fields:  {欄位名: (domain_min, domain_max)} 整數欄位邊界。

    Returns:
        {"D1": {...}, "D2": {...}, "D3": {...}}
    """
    return {
        "D1": compute_d1_uniqueness(cases),
        "D2": compute_d2_entropy(cases, bool_fields),
        "D3": compute_d3_wasserstein(cases, int_fields),
    }
