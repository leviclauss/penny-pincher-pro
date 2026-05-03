"""ntfy.sh renderer.

Templates live under ``alerts/templates/ntfy/`` as ``<alert_type>.j2`` and
emit ``Title: <text>`` on the first line followed by the body. ntfy keeps
the title short (it shows in the phone notification banner) so templates
should keep it under ~80 chars.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from alerts.templates.telegram_render import TEMPLATES_ROOT

TEMPLATES_DIR = TEMPLATES_ROOT / "ntfy"


@dataclass(frozen=True, slots=True)
class NtfyMessage:
    title: str
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
) -> NtfyMessage:
    """Render a ntfy notification (title + body) for ``alert_type``.

    Templates start with ``Title: <text>``; the rest of the file is the body.
    A missing or unparseable header falls back to the alert type as the title
    so the alert still surfaces on the phone.
    """
    env = _build_env()
    env.globals["web_base_url"] = web_base_url.rstrip("/")
    template = env.get_template(f"{alert_type}.j2")
    rendered = template.render(**payload).strip()

    if not rendered:
        return NtfyMessage(title=alert_type, body="")

    first, _, rest = rendered.partition("\n")
    if first.lower().startswith("title:"):
        title = first.split(":", 1)[1].strip() or alert_type
        body = rest.lstrip("\n").rstrip()
    else:
        title = alert_type
        body = rendered
    return NtfyMessage(title=title or alert_type, body=body)
