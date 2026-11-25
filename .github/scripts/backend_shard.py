"""Split the backend test files into CI shards of about equal run time.

    python3 .github/scripts/backend_shard.py SHARD TOTAL       # prints this shard's test files, one per line
    python3 .github/scripts/backend_shard.py --plan TOTAL      # prints each shard's estimated seconds
    python3 .github/scripts/backend_shard.py --durations JUNIT_XML...   # per-file seconds from pytest --junitxml

Run from the repository root. Seconds per test file come from .github/backend-test-durations.json (written by the
--durations mode from a full run); a file not listed yet counts as the median. Files are dealt longest first to the
shard with the least time so far, so the slowest shard finishes close to the others.
"""

from __future__ import annotations

import json
import statistics
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
DURATIONS = ROOT / ".github" / "backend-test-durations.json"


def test_files() -> list[str]:
    found = [*BACKEND.glob("app/**/test_*.py"), *BACKEND.glob("tests/**/test_*.py")]
    return sorted(str(p.relative_to(BACKEND)) for p in found)


def plan(total: int) -> list[tuple[float, list[str]]]:
    known: dict[str, float] = json.loads(DURATIONS.read_text()) if DURATIONS.exists() else {}
    files = test_files()
    default = statistics.median(known.values()) if known else 1.0
    weight = {f: known.get(f, default) for f in files}
    shards: list[tuple[float, list[str]]] = [(0.0, []) for _ in range(total)]
    for f in sorted(files, key=lambda f: (-weight[f], f)):
        i = min(range(total), key=lambda k: (shards[k][0], k))
        seconds, chosen = shards[i]
        shards[i] = (seconds + weight[f], [*chosen, f])
    return [(seconds, sorted(chosen)) for seconds, chosen in shards]


def durations(reports: list[str]) -> dict[str, float]:
    """Seconds per test file, from junit XML written by pytest run in backend/."""
    per_file: dict[str, float] = defaultdict(float)
    for report in reports:
        for case in ET.parse(report).iter("testcase"):
            module = case.get("classname", "")
            # classname is the dotted module path, plus the class name for tests inside a class
            parts = module.split(".")
            while parts and not (BACKEND / Path(*parts).with_suffix(".py")).exists():
                parts.pop()
            if parts:
                per_file[str(Path(*parts).with_suffix(".py"))] += float(case.get("time", 0))
    return {f: round(s, 1) for f, s in sorted(per_file.items())}


def main(argv: list[str]) -> None:
    if argv[:1] == ["--durations"]:
        print(json.dumps(durations(argv[1:]), indent=1, sort_keys=True))
    elif argv[:1] == ["--plan"]:
        for n, (seconds, files) in enumerate(plan(int(argv[1])), start=1):
            print(f"shard {n}: {len(files)} files, about {seconds:.0f} s")
    else:
        shard, total = int(argv[0]), int(argv[1])
        if not 1 <= shard <= total:
            sys.exit(f"shard must be between 1 and {total}")
        print("\n".join(plan(total)[shard - 1][1]))


if __name__ == "__main__":
    main(sys.argv[1:])
