"""Console output that never crashes on Windows.

When output is redirected to a file on Windows, Python writes it as cp1252, which cannot hold
characters the models use (gpt-oss cites as 【1】). Printing one then raises UnicodeEncodeError
and kills a long eval run. Every command-line entry point calls utf8_console() first.
"""

import sys


def utf8_console() -> None:
    """Write stdout and stderr as UTF-8; replace anything that still cannot be written."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
