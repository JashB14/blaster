
import json
from pathlib import Path

coverage = json.loads(Path("/app/coverage.json").read_text())

tests = json.loads(Path("/app/tests.json").read_text())["tests"]
test_numbers = {test["id"]: test["number"] for test in tests}

coverage_map = {}

for file_path, file_data in coverage["files"].items():
    line_map = {}

    for line, contexts in file_data.get("contexts", {}).items():
        numbers = {
            test_numbers[context.removesuffix("|run")]
            for context in contexts
            if context.endswith("|run")
            and context.removesuffix("|run") in test_numbers
        }

        if numbers:
            line_map[line] = sorted(numbers)

    if line_map:
        coverage_map[file_path] = line_map

output_path = Path("/app/coverage_map.json")
output_path.write_text(json.dumps(coverage_map, separators=(",", ":")))

print(
    f"Created compact coverage map: {len(coverage_map)} files, "
    f"{sum(len(lines) for lines in coverage_map.values())} covered lines"
)
