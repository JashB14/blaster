"""Evaluation only (never copied into the Docker image): where does the score go?

Usage: python3 gap_report.py sample output.jsonl
Compares each commit's cost with the cheapest possible cost for exactly its failing tests.
"""
import json
import sys
from collections import Counter
from pathlib import Path

data_dir, output = Path(sys.argv[1]), Path(sys.argv[2])
tests = json.loads((data_dir / "tests.json").read_text())["tests"]
answers = json.loads((data_dir / "answers.json").read_text())["commits"]
ms = {t["number"]: round(t["seconds"] * 1000) for t in tests}


def cost(workers):
    workers = [w for w in workers if w]
    if not workers:
        return 0
    return max(sum(ms[n] for n in w) for w in workers) + 2000 * len(workers)


def ideal_cost(numbers):
    """Cheapest split of exactly these tests (longest-first packing, 1 to 8 workers)."""
    if not numbers:
        return 0
    best = None
    for k in range(1, min(8, len(numbers)) + 1):
        workers, loads = [[] for _ in range(k)], [0] * k
        for n in sorted(numbers, key=lambda n: -ms[n]):
            i = loads.index(min(loads))
            workers[i].append(n)
            loads[i] += ms[n]
        best = cost(workers) if best is None else min(best, cost(workers))
    return best


rows = []
for line in output.read_text().splitlines():
    entry = json.loads(line)
    commit = entry["commit"]
    selected = {n for worker in entry["workers"] for n in worker}
    failing = set(answers[commit]["failing"])
    mine = cost(entry["workers"]) + (300000 if failing - selected else 0)
    ideal = ideal_cost(failing)
    rows.append((mine - ideal, commit, answers[commit]["kind"], selected, failing, mine, ideal))

rows.sort(reverse=True)
print(f"{'commit':7} {'kind':8} {'picked':>6} {'fail':>4} {'cost':>8} {'ideal':>7} {'excess':>8}")
for excess, commit, kind, selected, failing, mine, ideal in rows:
    flag = "  MISS" if failing - selected else ""
    print(f"{commit:7} {kind:8} {len(selected):6} {len(failing):4} {mine/1000:7.1f}s {ideal/1000:6.1f}s {excess/1000:7.1f}s{flag}")

total_mine = sum(r[5] for r in rows) / 1000
total_ideal = sum(r[6] for r in rows) / 1000
print(f"\nTOTAL cost {total_mine:.1f}s | ideal {total_ideal:.1f}s | excess {total_mine - total_ideal:.1f}s")

print("\nExtra tests (picked but not failing) for the 5 most expensive commits, by test file:")
for excess, commit, kind, selected, failing, mine, ideal in rows[:5]:
    extra = Counter(tests[n]["file"] for n in selected - failing)
    print(f"  {commit} ({excess/1000:.1f}s excess):", dict(extra.most_common(4)) or "none")