"""Standalone Linux/WSL worker. Requires mutmut 3.x and pytest, no LLM SDKs."""
import collections
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys


def score_statuses(statuses):
    counts = dict(collections.Counter(statuses.values()))
    unresolved = sum(n for s, n in counts.items() if s not in {"killed", "survived", "no tests"})
    total = len(statuses)
    # Conservative denominator: ALL generated mutants. No automatic equivalent
    # exclusion, no timeout-as-kill, and no silently discarded tool errors.
    return dict(total_mutants=total, counts=counts, unresolved=unresolved,
                killed=counts.get("killed", 0),
                mutation_score=counts.get("killed", 0)/total if total and not unresolved else None,
                killed_fraction_lower_bound=counts.get("killed", 0)/total if total else None,
                status="completed" if total and not unresolved else "incomplete")


def execute(job):
    os.chdir(job)
    request = json.loads(Path("request.json").read_text(encoding="utf-8"))
    version = importlib.metadata.version("mutmut")
    if version.split(".")[0] != "3":
        raise RuntimeError(f"Unsupported mutmut {version}; install tested mutmut 3.x")
    Path("src").mkdir()
    Path("tests").mkdir()
    Path("src/target.py").write_text(request["source"], encoding="utf-8")
    # Freeze the original oracle in a separate process before any mutation.
    oracle = """import json, sys
sys.path.insert(0, 'src')
import target
r=json.load(open('request.json', encoding='utf-8'))
f=getattr(target,r['function'])
expected=[f(**case) for case in r['cases']]
if any(type(v) not in (bool,int,float,str,type(None)) for v in expected):
    raise TypeError('Only scalar fixture return values are supported')
json.dump(expected,open('expected.json','w',encoding='utf-8'),allow_nan=False)
"""
    result = subprocess.run([sys.executable, "-c", oracle], capture_output=True, text=True, timeout=60)
    Path("oracle.log").write_text(result.stdout+result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError("Original fixture failed for saved cases; see oracle.log")
    expected = json.loads(Path("expected.json").read_text())
    tests = ["from target import " + request["function"] + " as subject", ""]
    for i, (case, value) in enumerate(zip(request["cases"], expected)):
        tests += [f"def test_case_{i:05d}():", f"    actual = subject(**{case!r})",
                  f"    assert type(actual) is {type(value).__name__ if value is not None else 'type(None)'}",
                  f"    assert actual == {value!r}", ""]
    Path("tests/test_generated.py").write_text("\n".join(tests), encoding="utf-8")
    config = '''[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
[tool.mutmut]
source_paths = ["src/"]
mutate_only_covered_lines = false
max_stack_depth = -1
'''
    Path("pyproject.toml").write_text(config)
    with Path("baseline.log").open("w") as log:
        baseline = subprocess.run([sys.executable,"-m","pytest","-q"], stdout=log, stderr=subprocess.STDOUT, timeout=60)
    if baseline.returncode:
        raise RuntimeError("Original pytest baseline failed; see baseline.log")
    # Import cli rather than python -m mutmut (avoids double-loading trampoline state).
    launcher = "from mutmut.__main__ import cli; cli()"
    with Path("mutmut.log").open("w") as log:
        run = subprocess.run([sys.executable,"-c",launcher,"run","--max-children",str(request["workers"])],
                             stdout=log,stderr=subprocess.STDOUT)
    if run.returncode:
        raise RuntimeError(f"mutmut exited {run.returncode}; see mutmut.log")
    # Read the exact per-mutant inventory via the same installed mutmut version.
    export = """import json
from mutmut.__main__ import collect_source_file_mutation_data, status_by_exit_code
mutants, _ = collect_source_file_mutation_data(mutant_names=())
json.dump({name:code for _,name,code in mutants},open('exit_codes.json','w'))
json.dump({name:('runner internal error' if code == 3 else status_by_exit_code[code]) for _,name,code in mutants},open('statuses.json','w'))
"""
    result = subprocess.run([sys.executable,"-c",export], capture_output=True,text=True,timeout=60)
    Path("export.log").write_text(result.stdout+result.stderr)
    if result.returncode:
        raise RuntimeError("Unsupported mutmut result API; see export.log")
    statuses = json.loads(Path("statuses.json").read_text())
    bank = dict(source_sha256=hashlib.sha256(request["source"].encode()).hexdigest(),
                mutmut_version=version, config=config, mutant_ids=sorted(statuses))
    bank_id = hashlib.sha256(json.dumps(bank,sort_keys=True).encode()).hexdigest()
    Path("mutant_bank.json").write_text(json.dumps(bank,indent=2))
    return dict(**score_statuses(statuses), bank_id=bank_id, mutmut_version=version,
                mutant_statuses=statuses, baseline_passed=True,
                oracle="original fixture output; scalar value and exact type",
                denominator_policy="all generated mutants; no equivalent exclusions; unresolved => score null")


if __name__ == "__main__":
    job = Path(sys.argv[1]).resolve()
    try:
        report = execute(job)
    except Exception as exc:
        report = dict(status="error",error=f"{type(exc).__name__}: {exc}",mutation_score=None)
    (job/"outcome.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k != 'mutant_statuses'}))
    sys.exit(0 if report["status"] == "completed" else 1)
