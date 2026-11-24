"""Build every broken-repo scenario into a fresh folder.

  python -m eval.git_scenarios                     builds into eval/git_scenarios/build (git-ignored)
  python -m eval.git_scenarios --out C:/tmp/scn --only push_rejected

Each scenario gets <out>/<id>/ with its repos and a scenario.json answer key.
The folder is deleted and rebuilt every time, so runs always start from the same state.
"""

import argparse
import json
import os
import stat
import shutil
from pathlib import Path

from eval.git_scenarios.scenarios import SCENARIOS, scenario_dict

DEFAULT_OUT = Path(__file__).resolve().parent / "build"  # under eval/: never indexed


def _force_remove(func, path, _exc):
    """Windows: git marks object files read-only, so clear the flag and retry."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--only", help="comma-separated scenario ids")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else None

    out = a.out.resolve()
    if out.exists():
        if not (out / ".git-scenarios").exists():
            raise SystemExit(f"{out} exists and was not made by this tool; refusing to delete it")
        shutil.rmtree(out, onerror=_force_remove)
    out.mkdir(parents=True)
    (out / ".git-scenarios").write_text("built by python -m eval.git_scenarios\n", encoding="utf-8")

    for build in SCENARIOS:
        if only and build.__name__ not in only:
            continue
        root = out / build.__name__
        root.mkdir()
        s = build(root)
        # Paths of this machine are noise for the answer key; keep them relative.
        s.error_output = s.error_output.replace(str(root), "<scenario>").replace(root.as_posix(), "<scenario>")
        (root / "scenario.json").write_text(json.dumps(scenario_dict(s), indent=2), encoding="utf-8")
        first = s.error_output.splitlines()[0] if s.error_output else ""
        print(f"{s.id:22} {s.error_source:8} {s.command:32} {first[:70]}")
    print(f"\nscenarios in {out}")


if __name__ == "__main__":
    main()
