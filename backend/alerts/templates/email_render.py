"""Plain-text renderer for the email (SMTP) channel.

Templates live under ``alerts/templates/email/`` as ``<alert_type>.txt.j2``.
The first non-blank line is treated as the email subject; the remainder
becomes the body. No escaping is needed — SMTP delivers raw text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from alerts.templates.telegram_render import TEMPLATES_ROOT

TEMPLATES_DIR = TEMPLATES_ROOT / "email"


@dataclass(frozen=True, slots=True)
class EmailMessage:
    subject: str
    body: str


def _build_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=False,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )


def render(
    alert_type: str,
    payload: dict[str, Any],
    *,
    web_base_url: str = "",
) -> EmailMessage:
    """Render an email subject + body for ``alert_type``.

    The template's first non-blank line becomes the subject; everything that
    follows (after the next blank line, if present) is the body. ``web_base_url``
    is exposed to templates so they can include "Open in app" deep links.
    """
    env = _build_env()
    env.globals["web_base_url"] = web_base_url.rstrip("/")
    template = env.get_template(f"{alert_type}.txt.j2")
    rendered = template.render(**payload).strip()

    if not rendered:
        return EmailMessage(subject="(empty alert)", body="")

    lines = rendered.splitlines()
    subject = lines[0].strip()
    # Drop the subject line + the (optional) blank separator that follows it.
    if len(lines) > 1 and not lines[1].strip():
        body = "\n".join(lines[2:]).strip()
    else:
        body = "\n".join(lines[1:]).strip()
    return EmailMessage(subject=subject or "(empty alert)", body=body)
