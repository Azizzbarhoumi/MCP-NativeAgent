"""Manual steps: things the USER does by hand because no git command does them (editing a
file to remove conflict markers, fixing a typo, moving a token into an environment variable).

The tool never edits code. In the end-to-end benchmark a simulated user performs a manual
step only when the assistant's plan asks for it AND names one of the files below; the edit is
the obvious correct one, so a run is not failed for a step a real user would do in 10 seconds.
This favours the assistant a little and is reported as such.
"""

from pathlib import Path


def _w(work: Path, name: str, text: str) -> None:
    (work / name).write_text(text, encoding="utf-8", newline="\n")


MANUAL: dict[str, dict] = {
    "merge_conflict": {"files": ["app.py"],
                       "apply": lambda w: _w(w, "app.py", 'GREETING = "hello from feature"\n\nprint(GREETING)\n')},
    "rebase_conflict": {"files": ["app.py"],
                        "apply": lambda w: _w(w, "app.py", 'GREETING = "hello from feature"\n\nprint(GREETING)\n')},
    "ho_merge_conflict_two_files": {"files": ["app.py", "settings.py"],
                                    "apply": lambda w: (_w(w, "app.py", 'GREETING = "hello from redesign"\n\nprint(GREETING)\n'),
                                                        _w(w, "settings.py", "DEBUG = False  # production\n"))},
    "ci_test_failure": {"files": ["calc.py"], "apply": lambda w: _w(w, "calc.py", "def add(a, b):\n    return a + b\n")},
    "ci_conflict_markers": {"files": ["app.py"],
                            "apply": lambda w: _w(w, "app.py", 'GREETING = "hello from redesign"\n\nprint(GREETING)\n')},
    "push_protection_secret": {"files": ["deploy.py"], "apply": lambda w: _w(
        w, "deploy.py", 'import os\n\nimport requests\n\nTOKEN = os.environ["DEPLOY_TOKEN"]\n\n'
                        'def deploy():\n    return requests.post("https://example.invalid", headers={"Authorization": TOKEN})\n')},
    "ho_bad_commit_unpushed_feature": {"files": ["search.py"],
                                       "apply": lambda w: _w(w, "search.py", "def search(items, x):\n    return x in items\n")},
}
