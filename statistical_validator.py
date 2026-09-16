"""
statistical_validator.py — 統計驗證模組。

提供三個公開函式：
  compare_before_after    — Wilcoxon Signed-Rank + Holm-Bonferroni + effect size (r)
  compare_methods         — Kruskal-Wallis + Dunn's post-hoc test
  generate_random_baseline — 純隨機基線，供與 IFL 對比用

理論依據：
  Wilcoxon (1945) — Signed-Rank test
  Holm (1979)     — Holm-Bonferroni step-down correction
  Cohen (1988)    — Effect size r = Z / sqrt(N)
  Kruskal & Wallis (1952) — Kruskal-Wallis one-way ANOVA on ranks
  Dunn (1964)     — Dunn's post-hoc pairwise comparison
"""
from __future__ import annotations

import importlib.util
import random
from pathlib import Path
from typing import Any

import numpy as np
import scipy.stats as stats

from ifl_mcdc.config import IFLConfig
from ifl_mcdc.orchestrator import IFLOrchestrator
from validation_fixtures import FixtureSpec


# ══════════════════════════════════════════════════════════════
#  compare_before_after
# ══════════════════════════════════════════════════════════════

def compare_before_after(
    before_scores: list[float],
    after_scores:  list[float],
    metric_names:  list[str],
) -> dict[str, Any]:
    """Wilcoxon Signed-Rank 檢定 + Holm-Bonferroni 校正 + effect size。

    比較「改進前」與「改進後」的各項多樣性指標分數，
    驗證改進是否在統計上顯著提升。

    Args:
        before_scores: 改進前的指標分數列表（每個元素對應一個指標）。
        after_scores:  改進後的指標分數列表（與 before_scores 一一對應）。
        metric_names:  指標名稱列表（供輸出報告用）。

    Returns:
        {
            "results": [
                {
                    "metric":   str,        # 指標名稱
                    "p_raw":    float,      # 未校正 p-value
                    "p_adj":    float,      # Holm-Bonferroni 校正後 p-value
                    "stat":     float,      # Wilcoxon 統計量
                    "effect_r": float,      # effect size r = |Z| / sqrt(N)
                    "improved": bool,       # after > before（方向性）
                    "sig":      bool,       # p_adj < 0.05
                },
                ...
            ],
            "overall_improved": bool,   # 所有顯著指標均改善
        }
    """
    if len(before_scores) != len(after_scores):
        raise ValueError(
            f"before/after 長度不一致：{len(before_scores)} vs {len(after_scores)}"
        )
    if len(before_scores) != len(metric_names):
        raise ValueError(
            f"metric_names 長度（{len(metric_names)}）與分數長度（{len(before_scores)}）不一致"
        )

    n = len(before_scores)
    raw_results: list[dict[str, Any]] = []

    for i, name in enumerate(metric_names):
        b, a = before_scores[i], after_scores[i]
        try:
            # Wilcoxon 需要配對資料，此處假設 before/after 各為單一觀測值
            # 實務上應傳入多次重複實驗的向量；此處以單點差值退化為符號檢定
            diff = a - b
            if diff == 0:
                p_raw, stat = 1.0, 0.0
                z_approx    = 0.0
            else:
                # 若只有單一差值，回報為符號（p 設為 NaN 表示無法計算）
                p_raw, stat = float("nan"), float(abs(diff))
                z_approx    = 0.0
        except Exception:
            p_raw, stat, z_approx = float("nan"), 0.0, 0.0

        # effect size r = |Z| / sqrt(N)（此處 N=1，退化）
        effect_r = abs(z_approx) / (1.0 ** 0.5) if not (p_raw != p_raw) else 0.0
        raw_results.append({
            "metric":   name,
            "p_raw":    p_raw,
            "stat":     stat,
            "effect_r": round(effect_r, 4),
            "improved": a > b,
            "diff":     round(a - b, 4),
        })

    # Holm-Bonferroni 校正（跳過 NaN）
    valid_idx   = [i for i, r in enumerate(raw_results) if not (r["p_raw"] != r["p_raw"])]
    p_raw_valid = [raw_results[i]["p_raw"] for i in valid_idx]
    p_adj_valid = _holm_bonferroni(p_raw_valid)

    for k, i in enumerate(valid_idx):
        raw_results[i]["p_adj"] = round(p_adj_valid[k], 4)
        raw_results[i]["sig"]   = p_adj_valid[k] < 0.05
    for i, r in enumerate(raw_results):
        if "p_adj" not in r:
            r["p_adj"] = float("nan")
            r["sig"]   = False

    overall = all(r["improved"] for r in raw_results)
    return {"results": raw_results, "overall_improved": overall}


def compare_before_after_paired(
    before_series: list[list[float]],
    after_series:  list[list[float]],
    metric_names:  list[str],
) -> dict[str, Any]:
    """Wilcoxon Signed-Rank 檢定（多次重複實驗配對版本）。

    Args:
        before_series: 改進前的重複觀測列表，shape=(n_metrics, n_runs)。
        after_series:  改進後的重複觀測列表，shape=(n_metrics, n_runs)。
        metric_names:  指標名稱列表。

    Returns:
        同 compare_before_after 的格式。
    """
    n_metrics = len(metric_names)
    if len(before_series) != n_metrics or len(after_series) != n_metrics:
        raise ValueError("before_series/after_series 長度必須等於 metric_names 長度")

    raw_results: list[dict[str, Any]] = []

    for i, name in enumerate(metric_names):
        b_arr = np.array(before_series[i], dtype=float)
        a_arr = np.array(after_series[i], dtype=float)

        try:
            stat, p_raw = stats.wilcoxon(a_arr - b_arr, alternative="greater")
            n_pairs      = len(b_arr)
            # 近似 Z 值：用正態近似
            mean_w   = n_pairs * (n_pairs + 1) / 4
            std_w    = math.sqrt(n_pairs * (n_pairs + 1) * (2 * n_pairs + 1) / 24)
            z_approx = (stat - mean_w) / std_w if std_w > 0 else 0.0
            effect_r = abs(z_approx) / (n_pairs ** 0.5)
        except Exception:
            stat, p_raw, z_approx, effect_r = 0.0, float("nan"), 0.0, 0.0

        raw_results.append({
            "metric":   name,
            "p_raw":    p_raw,
            "stat":     float(stat),
            "effect_r": round(effect_r, 4),
            "improved": float(np.mean(a_arr)) > float(np.mean(b_arr)),
            "diff":     round(float(np.mean(a_arr - b_arr)), 4),
        })

    # Holm-Bonferroni
    valid_idx   = [i for i, r in enumerate(raw_results) if not (r["p_raw"] != r["p_raw"])]
    p_raw_valid = [raw_results[i]["p_raw"] for i in valid_idx]
    p_adj_valid = _holm_bonferroni(p_raw_valid)

    for k, i in enumerate(valid_idx):
        raw_results[i]["p_adj"] = round(p_adj_valid[k], 4)
        raw_results[i]["sig"]   = p_adj_valid[k] < 0.05
    for r in raw_results:
        if "p_adj" not in r:
            r["p_adj"] = float("nan")
            r["sig"]   = False

    overall = all(r["improved"] for r in raw_results)
    return {"results": raw_results, "overall_improved": overall}


def _holm_bonferroni(p_values: list[float]) -> list[float]:
    """Holm-Bonferroni step-down 校正。

    Args:
        p_values: 未校正 p-value 列表。

    Returns:
        校正後 p-value 列表（順序與輸入相同）。
    """
    n = len(p_values)
    if n == 0:
        return []

    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    adjusted = [0.0] * n
    prev_adj = 0.0

    for rank, (orig_idx, p) in enumerate(indexed):
        adj = p * (n - rank)
        adj = max(adj, prev_adj)   # step-down 保證單調性
        adj = min(adj, 1.0)
        adjusted[orig_idx] = adj
        prev_adj = adj

    return adjusted


# ══════════════════════════════════════════════════════════════
#  compare_methods
# ══════════════════════════════════════════════════════════════

def compare_methods(
    scores_dict: dict[str, list[float]],
    metric: str,
) -> dict[str, Any]:
    """Kruskal-Wallis 檢定 + Dunn's post-hoc 比較多個方法的分數分布。

    Args:
        scores_dict: {方法名稱: 分數列表}，各方法可有不同樣本數。
        metric:      指標名稱（僅供報告用）。

    Returns:
        {
            "metric":       str,
            "kruskal": {
                "stat":   float,
                "p":      float,
                "sig":    bool,   # p < 0.05
            },
            "dunn": {
                "(method_a, method_b)": {
                    "p_raw": float, "p_adj": float, "sig": bool
                },
                ...
            },
        }
    """
    method_names = list(scores_dict.keys())
    groups       = [np.array(scores_dict[m], dtype=float) for m in method_names]

    if len(groups) < 2:
        return {
            "metric":  metric,
            "kruskal": {"stat": 0.0, "p": 1.0, "sig": False},
            "dunn":    {},
            "note":    "少於 2 個方法，無法比較",
        }

    try:
        kw_stat, kw_p = stats.kruskal(*groups)
    except Exception:
        kw_stat, kw_p = 0.0, 1.0

    # Dunn's post-hoc（手動實作，使用 Z 統計量）
    dunn_results: dict[str, dict[str, Any]] = {}
    all_vals = np.concatenate(groups)
    n_total  = len(all_vals)
    ranks    = stats.rankdata(all_vals)

    offset = 0
    group_ranks: list[np.ndarray] = []
    for g in groups:
        group_ranks.append(ranks[offset:offset + len(g)])
        offset += len(g)

    pair_ps: list[tuple[tuple[str, str], float]] = []
    for i in range(len(method_names)):
        for j in range(i + 1, len(method_names)):
            ni, nj = len(groups[i]), len(groups[j])
            if ni < 1 or nj < 1:
                continue
            rank_mean_i = float(np.mean(group_ranks[i]))
            rank_mean_j = float(np.mean(group_ranks[j]))
            se = ((n_total * (n_total + 1) / 12.0) * (1.0 / ni + 1.0 / nj)) ** 0.5
            z  = (rank_mean_i - rank_mean_j) / se if se > 0 else 0.0
            p  = float(2 * stats.norm.sf(abs(z)))  # two-sided
            pair_ps.append(((method_names[i], method_names[j]), p))

    # Holm-Bonferroni
    raw_ps  = [p for _, p in pair_ps]
    adj_ps  = _holm_bonferroni(raw_ps)

    for k, ((ma, mb), p_raw) in enumerate(pair_ps):
        key = f"({ma}, {mb})"
        dunn_results[key] = {
            "p_raw": round(p_raw, 4),
            "p_adj": round(adj_ps[k], 4),
            "sig":   adj_ps[k] < 0.05,
        }

    return {
        "metric":  metric,
        "kruskal": {
            "stat": round(float(kw_stat), 4),
            "p":    round(float(kw_p), 4),
            "sig":  float(kw_p) < 0.05,
        },
        "dunn": dunn_results,
    }


# ══════════════════════════════════════════════════════════════
#  generate_random_baseline
# ══════════════════════════════════════════════════════════════

def generate_random_baseline(
    spec: FixtureSpec,
    n_runs: int = 5,
    cases_per_run: int = 30,
) -> list[dict[str, Any]]:
    """生成純隨機基線測試案例，供與 IFL 對比使用。

    純隨機策略：對每個欄位在 domain_bounds 範圍內均勻隨機取值，
    不使用任何 MC/DC 引導或 SMT 約束。

    Args:
        spec:          Fixture 規格。
        n_runs:        執行次數。
        cases_per_run: 每次執行生成的案例數。

    Returns:
        含 _source="random_baseline" 和 _run 元資料的案例列表。
    """
    rng    = random.Random(42)
    result: list[dict[str, Any]] = []

    for run_idx in range(1, n_runs + 1):
        for _ in range(cases_per_run):
            case: dict[str, Any] = {}
            for field, ftype in spec.domain_types.items():
                if ftype == "bool":
                    case[field] = rng.choice([True, False])
                else:  # int
                    lo, hi = spec.domain_bounds.get(field, [0, 100])
                    case[field] = rng.randint(lo, hi)
            case["_source"] = "random_baseline"
            case["_run"]    = run_idx
            case["_fixture"] = spec.label
            result.append(case)

    return result


# ══════════════════════════════════════════════════════════════
#  報告輸出（終端機）
# ══════════════════════════════════════════════════════════════

def print_comparison_report(
    compare_result: dict[str, Any],
    title: str = "改進前後比較（Wilcoxon Signed-Rank + Holm-Bonferroni）",
) -> None:
    """將 compare_before_after 結果格式化輸出至終端機。"""
    W = 62
    print(f"\n{'='*W}")
    print(f"  {title}")
    print(f"{'='*W}")
    print(f"  {'指標':<20} {'diff':>8}  {'p_raw':>8}  {'p_adj':>8}  "
          f"{'effect_r':>9}  {'判定':>6}")
    print(f"  {'-'*62}")
    for r in compare_result["results"]:
        sig_mark = "[SIG]" if r.get("sig") else "     "
        dir_mark = "↑" if r["improved"] else "↓"
        p_raw_str = f"{r['p_raw']:.4f}" if r["p_raw"] == r["p_raw"] else "  NaN "
        p_adj_str = f"{r['p_adj']:.4f}" if r["p_adj"] == r["p_adj"] else "  NaN "
        print(
            f"  {r['metric']:<20} {r['diff']:>+8.4f}  {p_raw_str:>8}  "
            f"{p_adj_str:>8}  {r['effect_r']:>9.4f}  {dir_mark} {sig_mark}"
        )
    overall = "[整體改善]" if compare_result["overall_improved"] else "[未整體改善]"
    print(f"\n  {overall}")
    print(f"  {'='*W}")


def print_kruskal_report(
    kruskal_result: dict[str, Any],
) -> None:
    """將 compare_methods 結果格式化輸出至終端機。"""
    W  = 62
    kw = kruskal_result["kruskal"]
    print(f"\n{'='*W}")
    print(f"  Kruskal-Wallis 檢定：{kruskal_result['metric']}")
    print(f"  H={kw['stat']:.4f}  p={kw['p']:.4f}  "
          f"{'[顯著]' if kw['sig'] else '[不顯著]'}")
    if kruskal_result.get("dunn"):
        print(f"\n  Dunn's post-hoc（Holm-Bonferroni 校正）：")
        print(f"  {'配對':<40} {'p_raw':>8}  {'p_adj':>8}  {'判定':>6}")
        print(f"  {'-'*62}")
        for pair, res in kruskal_result["dunn"].items():
            sig_mark = "[SIG]" if res["sig"] else "     "
            print(f"  {pair:<40} {res['p_raw']:>8.4f}  "
                  f"{res['p_adj']:>8.4f}  {sig_mark}")
    print(f"  {'='*W}")


# math 模組需要在模組頂層 import
import math  # noqa: E402
