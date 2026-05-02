"""MarkdownV2 / HTML renderer for Telegram alerts.

Templates live under ``alerts/templates/telegram/`` as ``<alert_type>.md.j2``.
Jinja's autoescape is *off* — Telegram's MarkdownV2 needs a different escape
set than HTML — so every payload value rendered into a template must go
through the ``esc`` filter (``{{ value | esc }}``). The filter is selected
based on ``parse_mode``.

Telegram caps individual messages at 4096 characters; longer renders are
split on paragraph boundaries (blank line) by ``split_for_telegram``. The
renderer itself returns a single string; chunking happens at the channel
boundary so callers can choose to send a single message or a sequence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

TEMPLATES_DIR = Path(__file__).resolve().parent / "telegram"
MAX_MESSAGE_CHARS = 4096

# Per https://core.telegram.org/bots/api#markdownv2-style: the following
# characters must be escaped with a backslash anywhere they appear as
# literal text (i.e. outside of formatting entities).
_MARKDOWN_V2_SPECIALS = r"_*[]()~`>#+-=|{}.!\\"
_MARKDOWN_V2_TRANSLATE = str.maketrans({c: f"\\{c}" for c in _MARKDOWN_V2_SPECIALS})

_HTML_TRANSLATE = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})


def escape_markdown_v2(value: object) -> str:
    """Escape every MarkdownV2 metacharacter in ``value``."""
    return str(value).translate(_MARKDOWN_V2_TRANSLATE)


def escape_html(value: object) -> str:
    """Escape the three HTML metacharacters Telegram cares about."""
    return str(value).translate(_HTML_TRANSLATE)


def _build_env(parse_mode: str) -> Environment:
    esc = escape_markdown_v2 if parse_mode == "MarkdownV2" else escape_html
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(disabled_extensions=("j2",), default=False),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )
    env.filters["esc"] = esc
    return env


def render(alert_type: str, payload: dict[str, Any], *, parse_mode: str) -> str:
    """Render the named template with ``payload``. Trailing whitespace is stripped."""
    env = _build_env(parse_mode)
    template = env.get_template(f"{alert_type}.md.j2")
    return template.render(**payload).strip()


def split_for_telegram(text: str, *, limit: int | None = None) -> list[str]:
    """Split ``text`` into Telegram-safe chunks on paragraph boundaries.

    Falls back to hard splits if a single paragraph exceeds ``limit``.
    """
    if limit is None:
        limit = MAX_MESSAGE_CHARS
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(paragraph) <= limit:
            current = paragraph
        else:
            for start in range(0, len(paragraph), limit):
                chunks.append(paragraph[start : start + limit])
    if current:
        chunks.append(current)
    return chunks
