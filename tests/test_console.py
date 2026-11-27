"""Entry points must not crash when gpt-oss cites as 【1】 and stdout is a cp1252 file (Windows)."""

import io
import subprocess
import sys

from common.console import utf8_console


def test_cp1252_stream_survives_model_citations(monkeypatch):
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    utf8_console()
    print("answer 【1】 → done")
    out.flush()
    assert raw.getvalue().decode("utf-8") == "answer 【1】 → done\n"


def test_entry_point_prints_citations_under_cp1252(tmp_path):
    code = "from common.console import utf8_console; utf8_console(); print('see 【1†L3】')"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       env={"PYTHONIOENCODING": "cp1252", "PATH": "", "PYTHONPATH": "."})
    assert r.returncode == 0, r.stderr
    assert "【1†L3】" in r.stdout.decode("utf-8")


def test_every_cli_calls_it():
    from pathlib import Path
    for f in ("eval/run_knowledge_eval.py", "knowledge/ask.py", "eval/run_git_e2e.py", "eval/average_runs.py"):
        src = Path(f).read_text(encoding="utf-8")
        assert "def main() -> None:\n    utf8_console()" in src, f
