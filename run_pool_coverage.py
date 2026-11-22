"""Second coverage pass: trace code that runs inside worker processes."""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(os.environ.get("REPO", "/app/sqlglot"))
TESTS_JSON = Path(os.environ.get("TESTS_JSON", "/app/tests.json"))
OUT = Path(os.environ.get("OUT", "/app/coverage_pool.json"))
RC = Path(os.environ.get("RC", "/app/pool.coveragerc"))
JOBS = int(os.environ.get("JOBS", "4"))
ONLY = os.environ.get("ONLY")  # comma-separated test ids, for quick experiments
POOL_FILES = ("tests/test_optimizer.py", "tests/test_executor.py")

RC.write_text("[run]\nsource = sqlglot\nparallel = True\ncontext = ${TESTID}\n")

tests = json.loads(TESTS_JSON.read_text())["tests"]
ids = [t["id"] for t in tests if t["file"] in POOL_FILES]
if ONLY:
    ids = ONLY.split(",")


def run_one(test_id: str) -> None:
    env = dict(os.environ, TESTID=test_id, COVERAGE_PROCESS_START=str(RC), SKIP_INTEGRATION="0")
    subprocess.run(
        [sys.executable, "-m", "pytest", test_id, "-q", "-p", "no:cacheprovider"],
        cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


with ThreadPoolExecutor(max_workers=JOBS) as pool:
    list(pool.map(run_one, ids))

subprocess.run([sys.executable, "-m", "coverage", "combine", "--rcfile", str(RC)], cwd=REPO, check=True)
subprocess.run(
    [sys.executable, "-m", "coverage", "json", "--rcfile", str(RC), "--show-contexts", "-o", str(OUT)],
    cwd=REPO, check=True,
)
print(f"pool coverage done for {len(ids)} tests -> {OUT}", file=sys.stderr)

# Optional quick check: CHECK="sqlglot/serde.py:53,sqlglot/parsers/bigquery.py:500"
if os.environ.get("CHECK"):
    files = json.loads(OUT.read_text())["files"]
    for item in os.environ["CHECK"].split(","):
        path, line = item.rsplit(":", 1)
        contexts = files.get(path, {}).get("contexts", {}).get(line, [])
        print(f"{item} -> {sorted(c.split('::')[-1] for c in contexts)}", file=sys.stderr)