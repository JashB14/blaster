import gzip
import json
import re
import sys
from pathlib import Path

DATA = Path("/data")
COVERAGE = Path("/app/coverage_map.json.gz")
POOL_COVERAGE = Path("/app/pool_map.json.gz")
MAX_WORKERS = 8
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def split(tests: list[dict], worker_count: int) -> list[list[int]]:
    workers: list[list[int]] = [[] for _ in range(worker_count)]
    loads = [0.0] * worker_count

    for test in sorted(tests, key=lambda test: test["seconds"], reverse=True):
        lightest = loads.index(min(loads))
        workers[lightest].append(test["number"])
        loads[lightest] += test["seconds"]

    return workers


def changed_lines(patch_text: str) -> dict[str, set[int]]:
    """Return changed file paths and the OLD-file line numbers they touch."""
    changes: dict[str, set[int]] = {}
    old_path = None
    current = None
    old_line = 0
    old_left = new_left = 0

    for line in patch_text.splitlines():
        # Inside a hunk: the header counts say how many lines belong to it.
        if old_left > 0 or new_left > 0:
            tag = line[:1]
            if tag == "-":
                if current:
                    changes[current].add(old_line)
                old_line += 1
                old_left -= 1
            elif tag == "+":
                text = line[1:].strip()
                if current and text and not text.startswith("#"):
                    # An insertion sits between two old lines: select both.
                    changes[current].update({old_line - 1, old_line})
                new_left -= 1
            elif tag == "\\":
                pass
            else:
                old_line += 1
                old_left -= 1
                new_left -= 1
            continue

        if line.startswith("--- "):
            path = line[4:].split("\t")[0]
            old_path = path[2:] if path.startswith("a/") else None
        elif line.startswith("+++ "):
            path = line[4:].split("\t")[0]
            new_path = path[2:] if path.startswith("b/") else None
            current = old_path or new_path
            for p in (old_path, new_path):
                if p:
                    changes.setdefault(p, set())
        elif line.startswith("@@"):
            m = HUNK.match(line)
            if m:
                old_count = 1 if m[2] is None else int(m[2])
                new_count = 1 if m[4] is None else int(m[4])
                # "-5,0" means "insert after line 5", so the next old line is 6.
                old_line = int(m[1]) + (1 if old_count == 0 else 0)
                old_left, new_left = old_count, new_count

    return changes


def main() -> None:
    tests = json.loads((DATA / "tests.json").read_text())["tests"]
    with gzip.open(COVERAGE, "rt") as coverage_file:
        coverage = json.load(coverage_file)
    with gzip.open(POOL_COVERAGE, "rt") as pool_file:
        pool_coverage = json.load(pool_file)

    tests_by_number = {test["number"]: test for test in tests}

    for patch in sorted((DATA / "commits").glob("*.patch")):
        changes = changed_lines(patch.read_text())
        selected_numbers = set()

        for file_path, lines in changes.items():

            # Handle SQL fixture changes.
            if file_path.startswith("tests/fixtures/"):
                fixture_name = Path(file_path).stem

                # Remove fixture-specific suffixes to match related test names.
                normalized_name = fixture_name.removesuffix("_ddl").rstrip("s")

                for test in tests:
                    if normalized_name in test["id"]:
                        selected_numbers.add(test["number"])

                # General optimizer integration test.
                if file_path.startswith("tests/fixtures/optimizer/"):
                    for test in tests:
                        if test["id"].endswith("::test_optimize"):
                            selected_numbers.add(test["number"])

            # Handle Python source changes.
            if file_path.startswith("sqlglot/"):
                line_map = coverage.get(file_path, {})
                pool_line_map = pool_coverage.get(file_path, {})

                file_tests = None
                for line in lines:
                    found = set(line_map.get(str(line), [])) | set(pool_line_map.get(str(line), []))
                    if found:
                        selected_numbers.update(found)
                    elif line >= 1:
                        # No test ever ran this line (import-time code, constants,
                        # blank or comment lines): select every test that touches the file.
                        if file_tests is None:
                            file_tests = {n for nums in line_map.values() for n in nums}
                            file_tests |= {n for nums in pool_line_map.values() for n in nums}
                        selected_numbers.update(file_tests)

        selected = [
            tests_by_number[number]
            for number in sorted(selected_numbers)
            if number in tests_by_number
        ]

        # Minimize worker overhead: each nonempty worker costs 2 seconds.
        best_workers = []
        best_cost = 0.0

        for count in range(1, MAX_WORKERS + 1):
            workers = split(selected, count)
            loads = [
                sum(tests_by_number[number]["seconds"] for number in worker)
                for worker in workers
            ]
            active = sum(bool(worker) for worker in workers)
            cost = max(loads, default=0.0) + 2.0 * active

            if not best_workers or cost < best_cost:
                best_workers = workers
                best_cost = cost

        best_workers = [worker for worker in best_workers if worker]

        print(json.dumps({
            "commit": patch.stem,
            "workers": best_workers,
        }))

        print(
            f"{patch.stem}: selected {len(selected)} / {len(tests)} tests",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()