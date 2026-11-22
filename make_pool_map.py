"""Shrink the huge pool coverage JSON into a small map: source line -> test numbers.

Usage: python make_pool_map.py <coverage_pool.json> <tests.json> <output.json.gz>
Reads the big file one source file at a time, so it needs little memory.
"""
import gzip
import json
import sys
from pathlib import Path

import ijson

src, tests_path, out_path = (Path(arg) for arg in sys.argv[1:4])

numbers = {t["id"]: t["number"] for t in json.loads(tests_path.read_text())["tests"]}

pool_map = {}
with src.open("rb") as f:
    for path, data in ijson.kvitems(f, "files"):
        lines = {}
        for line, contexts in data.get("contexts", {}).items():
            nums = sorted({numbers[c] for c in contexts if c in numbers})
            if nums:
                lines[line] = nums
        if lines:
            pool_map[path] = lines

with gzip.open(out_path, "wt") as out:
    json.dump(pool_map, out, separators=(",", ":"))

tests_seen = {n for lines in pool_map.values() for nums in lines.values() for n in nums}
print(
    f"pool map: {len(pool_map)} files, "
    f"{sum(len(v) for v in pool_map.values())} lines, "
    f"{len(tests_seen)} different tests -> {out_path}"
)