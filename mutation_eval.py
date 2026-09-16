"""Evaluate saved experiment3 suites; never calls an LLM or edits a fixture.

Each suite gets an isolated mutmut project. Compare scores ONLY when bank_id
matches (source, mutmut version, settings and full generated mutant inventory).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def select_cases(cases: list[dict], limit: int | None, seed: int) -> list[dict]:
    unique = {}
    for case in cases:
        clean = {k: v for k, v in case.items() if not k.startswith("__")}
        unique.setdefault(json.dumps(clean, sort_keys=True), clean)
    values = list(unique.values())
    if limit is not None:
        if len(values) < limit:
            raise ValueError(f"Need {limit} unique cases; only {len(values)} available")
        values = random.Random(seed).sample(values, limit)
    if not values:
        raise ValueError("Empty test suite; not scored as a successful mutation experiment")
    return values


def wsl_path(path: Path, distro: str) -> str:
    return subprocess.check_output(
        ["wsl", "-d", distro, "--exec", "wslpath", "-a", str(path.resolve())],
        text=True, encoding="utf-8").strip()


def evaluate_saved(output: Path, fixtures: list[str], kinds: list[str], model: str,
                   runs: int, *, distro="Ubuntu", python_path="python3",
                   toolchain: Path | None = None, case_limit=None, seed=42,
                   workers=2, timeout=3600) -> list[dict]:
    raw = output / "raw_results.jsonl"
    if not raw.exists():
        raise FileNotFoundError(f"No saved generation results: {raw}")
    latest = {}
    for line in raw.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        key = rec["fixture"], rec["method"], rec["run_id"]
        latest[key] = rec
    worker = Path(__file__).with_name("mutation_worker.py").resolve()
    reports = []
    for (fixture, method, run_id), rec in sorted(latest.items()):
        if fixture not in fixtures or method not in [f"{k}_{model}" for k in kinds] or run_id > runs:
            continue
        if rec.get("status") != "completed":
            print(f"[mutation SKIP] {fixture}/{method}/{run_id}: generation incomplete")
            continue
        details = Path(rec["details_path"])
        source = details / "fixture_source.py"
        profile = "full" if case_limit is None else f"n{case_limit}_seed{seed}"
        work = details / "mutation" / profile
        report = dict(fixture=fixture, method=method, run_id=run_id,
                      attempt_id=rec.get("attempt_id"), profile=profile)
        try:
            if not source.exists():
                raise ValueError("Missing fixture_source.py snapshot; cannot verify historical fixture version")
            cfg = read_json(details / "config.json")["fixture"]
            if method.startswith("ifl_"):
                corrected = details / "test_suite_corrected.json"
                if corrected.exists():
                    cases = read_json(corrected)["test_suite"]
                else:
                    cases = read_json(details / "ifl_result.json")["result"]["test_suite"]
            else:
                cases = read_json(details / "llm_candidates.json")["domain_valid_cases"]
            chosen = select_cases(cases, case_limit, seed)
            work.mkdir(parents=True, exist_ok=True)
            request = dict(source=source.read_text(encoding="utf-8"),
                           function=cfg["func_name"], cases=chosen, workers=workers,
                           worker_sha256=hashlib.sha256(worker.read_bytes()).hexdigest())
            request_hash = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
            # No mutant caches are shared across suites. Resume only completed evaluations.
            existing = work / "report.json"
            if existing.exists():
                old = read_json(existing)
                if old.get("request_hash") == request_hash and old.get("status") == "completed":
                    reports.append(old)
                    print(f"[mutation RESUME] {fixture}/{method}/{run_id}")
                    continue
            # A fresh evaluation directory prevents mutmut's source-only cache from
            # accidentally reusing a previous test suite's scores.
            import uuid
            job = work / uuid.uuid4().hex
            job.mkdir()
            (job / "request.json").write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            env = os.environ.copy()
            env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            if os.name == "nt":
                command = ["wsl", "-d", distro, "--exec", "env", "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1"]
                if toolchain:
                    command.append("PYTHONPATH=" + wsl_path(toolchain, distro))
                command += [python_path, wsl_path(worker, distro), wsl_path(job, distro)]
            else:
                if toolchain:
                    env["PYTHONPATH"] = str(toolchain.resolve())
                command = [python_path, str(worker), str(job.resolve())]
            print(f"[mutation RUN] {fixture}/{method}/{run_id} cases={len(chosen)}", flush=True)
            with (job / "worker.log").open("w", encoding="utf-8") as log:
                proc = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                      env=env, timeout=timeout)
            if not (job / "outcome.json").exists():
                raise RuntimeError(f"Worker exited {proc.returncode}; see {job / 'worker.log'}")
            report.update(read_json(job / "outcome.json"))
            report.update(request_hash=request_hash, job_path=str(job), cases=len(chosen))
        except Exception as exc:
            report.update(status="error", error=f"{type(exc).__name__}: {exc}", mutation_score=None)
        work.mkdir(parents=True, exist_ok=True)
        (work / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        reports.append(report)
        print(f"[mutation] {fixture}/{method}/{run_id}: {report['status']} score={report.get('mutation_score')}")
    if not reports:
        raise ValueError("No completed saved runs matched this mutation selection")
    target = output / ("mutation_results.json" if case_limit is None else f"mutation_results_n{case_limit}_seed{seed}.json")
    # Preserve other fixtures/methods from earlier selections; latest attempt wins.
    merged = {(r["fixture"], r["method"], r["run_id"]): r for r in read_json(target)} if target.exists() else {}
    merged.update({(r["fixture"], r["method"], r["run_id"]): r for r in reports})
    target.write_text(json.dumps(list(merged.values()), indent=2, ensure_ascii=False), encoding="utf-8")
    return reports
