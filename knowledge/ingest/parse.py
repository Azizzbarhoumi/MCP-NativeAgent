"""Generic Markdown cleanup shared by all sources (nothing source-specific here)."""

import re

FRONT_MATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.S)
# Only real HTML layout tags. A generic <...> pattern would also delete placeholders like
# jobs.<job_id>.steps or <your-token>, which carry meaning in CI docs.
HTML_TAG = re.compile(r"</?(?:div|span|a|abbr|br|p|img|details|summary|sup|sub|kbd|em|strong|b|i|code|pre|table|thead|tbody|tr|td|th|ul|ol|li|section|figure|figcaption|picture|source|iframe|video)\b[^>\n]*>", re.I)
FENCE = re.compile(r"^(```|~~~)")


def strip_front_matter(text: str) -> str:
    return FRONT_MATTER.sub("", text, count=1)


def shorten_long_code(text: str, max_lines: int) -> str:
    """Replace fenced code blocks longer than max_lines with a one-line marker.
    Short snippets stay: they hold option names and error messages we want to match exactly."""
    out, block, in_code = [], [], False
    for line in text.splitlines():
        if FENCE.match(line.strip()):
            if not in_code:
                in_code, block = True, [line]
            else:
                block.append(line)
                body = len(block) - 2
                out.extend(block if body <= max_lines else
                           [block[0], f"[{body}-line code block omitted]", line])
                in_code = False
            continue
        (block if in_code else out).append(line)
    if in_code:  # unclosed fence: keep as is
        out.extend(block)
    return "\n".join(out)


def clean(text: str, max_code_lines: int) -> str:
    text = strip_front_matter(text)
    text = HTML_TAG.sub("", text)
    text = shorten_long_code(text, max_code_lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
