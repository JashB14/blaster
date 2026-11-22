import json
import re
import sys
from pathlib import Path

DATA = Path("/data")
MAPS = Path("/app/maps")
MAX_WORKERS = 8
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# Which tests read which fixture files (found by reading tests/ at the pinned commit).
# Kept as a union with maps/fixtures.json: the tracker cannot see setUpClass work
# credited to every test in a class, or files DuckDB reads natively.
EXTRA_FIXTURE_TESTS = [
    ("optimizer/tpc-h/", ["tests/test_executor.py::", "::test_tpch", "::test_tpcds"]),
    ("optimizer/tpc-ds/", ["tests/test_executor.py::", "::test_tpch", "::test_tpcds"]),
    ("optimizer/annotate_functions.sql", ["::test_annotate_funcs"]),
    ("fixtures/identity.sql", ["tests/test_serde.py::"]),
]


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
                text = line[1:].strip()
                # Removed blank or comment lines cannot change behavior.
                if current and text and not text.startswith("#"):
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

# ---- Syntax guard: paste these definitions into selector.py (after changed_lines) ----
#
# A patch that leaves a Python file unparseable makes every test that imports it
# fail, and line coverage cannot see that. This applies the patch to the pinned
# sqlglot source in memory and parses the result. It only ever ADDS tests, and
# for a patch that still parses it changes nothing.

import ast

SQLGLOT_ROOT = Path("/app/sqlglot")


def parse_patch(patch_text: str) -> dict[str, list[tuple[int, int, list[str]]]]:
    """Return {path: [(old_start, old_count, body_lines), ...]} for every file in the patch."""
    files: dict[str, list[tuple[int, int, list[str]]]] = {}
    old_path = None
    current = None
    body: list[str] = []
    old_left = new_left = 0
    hunk = None

    for line in patch_text.splitlines():
        if old_left > 0 or new_left > 0:
            tag = line[:1]
            if tag == "-":
                old_left -= 1
            elif tag == "+":
                new_left -= 1
            elif tag == "\\":
                continue
            else:
                old_left -= 1
                new_left -= 1
            body.append(line)
            if old_left <= 0 and new_left <= 0 and hunk is not None and current:
                files[current].append((hunk[0], hunk[1], body))
                body = []
            continue

        if line.startswith("--- "):
            path = line[4:].split("\t")[0]
            old_path = path[2:] if path.startswith("a/") else None
        elif line.startswith("+++ "):
            path = line[4:].split("\t")[0]
            new_path = path[2:] if path.startswith("b/") else None
            current = old_path or new_path
            if current:
                files.setdefault(current, [])
        elif line.startswith("@@"):
            m = HUNK.match(line)
            if m:
                old_count = 1 if m[2] is None else int(m[2])
                new_count = 1 if m[4] is None else int(m[4])
                hunk = (int(m[1]), old_count)
                old_left, new_left = old_count, new_count
                body = []
    return files


def apply_hunks(old_lines: list[str], hunks: list[tuple[int, int, list[str]]]) -> list[str]:
    out: list[str] = []
    pos = 0
    for old_start, old_count, body in hunks:
        # "-5,0" means "insert after old line 5", so the next old line (0-based) is index 5.
        start = old_start - 1 if old_count else old_start
        out.extend(old_lines[pos:start])
        pos = start
        for line in body:
            tag, text = line[:1], line[1:]
            if tag == "-":
                pos += 1
            elif tag == "+":
                out.append(text)
            elif tag != "\\":
                out.append(text)
                pos += 1
    out.extend(old_lines[pos:])
    return out


def broken_python_files(patch_text: str) -> list[str]:
    """Paths (sqlglot/ or tests/) of .py files that no longer parse once the patch is applied."""
    broken = []
    for path, hunks in parse_patch(patch_text).items():
        if not path.endswith(".py") or not path.startswith(("sqlglot/", "tests/")):
            continue
        source = SQLGLOT_ROOT / path
        old_lines = source.read_text().splitlines() if source.exists() else []
        new_text = "\n".join(apply_hunks(old_lines, hunks)) + "\n"
        try:
            ast.parse(new_text, filename=path)
        except (SyntaxError, ValueError):
            broken.append(path)
    return broken

# ---- In select_for_patch, add these lines at the very top of the function body: ----
#
#     broken = broken_python_files(patch_text)
#     if broken:
#         print(f"  syntax error after patch in {broken}; selecting every test", file=sys.stderr)
#         return set(tests_by_number)
#
# (If SQLGLOT_ROOT does not exist the guard finds nothing, so it fails safe to today's behavior.
#  The existing try/except in main() still turns any exception into "select every test".)


def load_shard(path: str):
    shard_file = MAPS / "shards" / (path.replace("/", "__") + ".json")
    if not shard_file.exists():
        return None
    return json.loads(shard_file.read_text())


def tests_for_lines(path: str, lines: set[int]) -> set[int]:
    """Map-index set of the tests that can be affected by edits to these old lines."""
    shard = load_shard(path)
    if shard is None:
        print(f"  no map for {path}", file=sys.stderr)
        return set()

    sets, eff, par = shard["sets"], shard["eff"], shard["par"]
    n = len(eff)
    found: set[int] = set()

    for line in sorted(lines):
        if line < 1 or n == 0:
            continue
        i = min(line, n)  # an insertion after the last line maps to the last line
        here = set(sets[eff[i - 1]])

        # Also take the enclosing if/for/try/with headers (they decide whether the line runs).
                # Only when no test ran this line, fall back to the enclosing
        # if/for/try/with headers (they decide whether the line runs).
        if not here:
            cur, parent = i, par[i - 1]
            while 0 < parent < cur:
                here |= set(sets[eff[parent - 1]])
                cur, parent = parent, par[parent - 1]

        if not here:
            print(f"  no tests for {path}:{line}", file=sys.stderr)
        found |= here

    return found


def main() -> None:
    tests = json.loads((DATA / "tests.json").read_text())["tests"]
    tests_by_number = {test["number"]: test for test in tests}

    ids = json.loads((MAPS / "ids.json").read_text())
    fixture_map = json.loads((MAPS / "fixtures.json").read_text())
    number_of_id = {test["id"]: test["number"] for test in tests}
    idx_to_number = [number_of_id.get(test_id) for test_id in ids]
    known_ids = set(ids)
    # Tests the maps have never seen (renumbered or new): add them whenever a change is selected.
    unknown_numbers = {test["number"] for test in tests if test["id"] not in known_ids}

    def select_for_patch(patch_text: str) -> set[int]:
        picked_idx: set[int] = set()
        picked_numbers: set[int] = set()

        for file_path, lines in changed_lines(patch_text).items():

            # Handle fixture changes.
            if file_path.startswith("tests/fixtures/"):
                tracked = fixture_map.get(file_path, [])
                picked_idx.update(tracked)

                # Name heuristic, kept as a union with the tracker's fixture map.
                name = Path(file_path).stem.removesuffix("_ddl").rstrip("s")
                if name:
                    for test in tests:
                        if name in test["id"]:
                            picked_numbers.add(test["number"])

                # General optimizer integration test.
                if file_path.startswith("tests/fixtures/optimizer/"):
                    for test in tests:
                        if test["id"].endswith("::test_optimize"):
                            picked_numbers.add(test["number"])

                # Tests that read this fixture under names the heuristic can't match.
                for needle, id_parts in EXTRA_FIXTURE_TESTS:
                    if needle in file_path:
                        for test in tests:
                            if any(part in test["id"] for part in id_parts):
                                picked_numbers.add(test["number"])

            # Handle Python source changes.
            elif file_path.startswith("sqlglot/") and file_path.endswith(".py"):
                picked_idx |= tests_for_lines(file_path, lines)

        for i in picked_idx:
            number = idx_to_number[i]
            if number is not None:
                picked_numbers.add(number)

        if picked_numbers:
            picked_numbers |= unknown_numbers
        return picked_numbers

    for patch in sorted((DATA / "commits").glob("*.patch")):
        try:
            selected_numbers = select_for_patch(patch.read_text())
        except Exception as exc:  # never crash: fall back to every test
            print(f"{patch.stem}: ERROR {exc!r}; selecting every test", file=sys.stderr)
            selected_numbers = set(tests_by_number)

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