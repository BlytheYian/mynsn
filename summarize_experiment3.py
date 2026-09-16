"""Aggregate all results/experiment3/full/<model>/ data into one markdown report."""
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

MODELS = ["gemma", "gpt", "claude"]
BASE = Path("results/experiment3/full")


def load_raw(model):
    path = BASE / model / "raw_results.jsonl"
    if not path.exists():
        return []
    dec = json.JSONDecoder()
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        idx = 0
        try:
            while idx < len(line):
                obj, end = dec.raw_decode(line, idx)
                records.append(obj)
                idx = end
                while idx < len(line) and line[idx] in " \t":
                    idx += 1
        except Exception:
            continue
    # latest attempt per (fixture, method, run_id) wins
    latest = {}
    for r in records:
        key = (r.get("fixture"), r.get("method"), r.get("run_id"))
        latest[key] = r
    return list(latest.values())


def load_mutation(model):
    path = BASE / model / "mutation_results.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    latest = {}
    for r in data:
        key = (r.get("fixture"), r.get("method"), r.get("run_id"))
        latest[key] = r
    return list(latest.values())


def mean(vals):
    vals = [v for v in vals if v is not None]
    return round(st.mean(vals), 4) if vals else None


def pct(n, d):
    return round(100 * n / d, 1) if d else None


lines = []
lines.append("# Experiment 3 — Full Results Summary")
lines.append("")
lines.append(f"Generated from `results/experiment3/full/` across models: {', '.join(MODELS)}")
lines.append("")

all_raw = {m: load_raw(m) for m in MODELS}
all_mut = {m: load_mutation(m) for m in MODELS}

# Build mutation score lookup: (fixture, method, run_id) -> mutation_score
mut_lookup = {}
for m in MODELS:
    for r in all_mut[m]:
        key = (m, r.get("fixture"), r.get("method"), r.get("run_id"))
        mut_lookup[key] = r

for model in MODELS:
    records = all_raw[model]
    if not records:
        lines.append(f"## {model}")
        lines.append("(no data)")
        lines.append("")
        continue

    lines.append(f"## Model: {model}")
    lines.append("")

    by_fixture_method = defaultdict(list)
    for r in records:
        by_fixture_method[(r.get("fixture"), r.get("method"))].append(r)

    fixtures = sorted({k[0] for k in by_fixture_method})

    for fixture in fixtures:
        lines.append(f"### {fixture}")
        lines.append("")
        lines.append(
            "| method | runs | converged | mean coverage | mean iter | mean tokens | "
            "mean time(s) | mask patches (total/llm%) | mean mutation score |"
        )
        lines.append(
            "|---|---|---|---|---|---|---|---|---|"
        )
        methods = sorted(k[1] for k in by_fixture_method if k[0] == fixture)
        for method in methods:
            recs = by_fixture_method[(fixture, method)]
            completed = [r for r in recs if r.get("status") == "completed"]
            n = len(recs)
            n_conv = sum(1 for r in completed if r.get("converged"))
            cov = mean([r.get("coverage") for r in completed])
            iters = mean([r.get("iterations") for r in completed])
            tokens = mean([
                r.get("known_tokens") if r.get("known_tokens") is not None else r.get("tokens")
                for r in completed
            ])
            time_s = mean([r.get("time_sec") for r in completed])

            mask_total = [r.get("mask_patches_total") for r in completed if r.get("mask_patches_total") is not None]
            mask_llm = [r.get("mask_patches_llm") for r in completed if r.get("mask_patches_llm") is not None]
            if mask_total:
                total_sum = sum(mask_total)
                llm_sum = sum(mask_llm) if mask_llm else 0
                mask_str = f"{mean(mask_total)} / {pct(llm_sum, total_sum)}%" if total_sum else "0 / -"
            else:
                mask_str = "-"

            mut_scores = []
            for r in recs:
                key = (model, fixture, method, r.get("run_id"))
                mrec = mut_lookup.get(key)
                if mrec and mrec.get("mutation_score") is not None:
                    mut_scores.append(mrec["mutation_score"])
            mut_str = mean(mut_scores)

            lines.append(
                f"| {method} | {n} | {pct(n_conv, len(completed))}% "
                f"({n_conv}/{len(completed)}) | {cov} | {iters} | {tokens} | {time_s} | "
                f"{mask_str} | {mut_str} |"
            )
        lines.append("")

report = "\n".join(lines)
out_path = Path("results/experiment3/SUMMARY.md")
out_path.write_text(report, encoding="utf-8")
print(f"wrote {out_path} ({len(report)} chars)")
