"""URL templatization at capture time (CLAUDE.md Part II, Tier 1).

/api/orders/48219 -> /api/orders/{p0}, literal preserved separately in
url_params. Query parameters are lifted into url_params by name and
removed from the template.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_NUM = re.compile(r"^\d+$")
_HEX = re.compile(r"^[0-9a-fA-F]{16,}$")


def _is_volatile_segment(segment: str) -> bool:
    return bool(_NUM.match(segment) or _UUID.match(segment) or _HEX.match(segment))


def templatize_url(url: str) -> tuple[str, dict[str, str]]:
    """Return (url_template, url_params) with literals preserved."""
    parts = urlsplit(url)
    params: dict[str, str] = {}
    segments: list[str] = []
    counter = 0
    for segment in parts.path.split("/"):
        if segment and _is_volatile_segment(segment):
            key = f"p{counter}"
            counter += 1
            params[key] = segment
            segments.append(f"{{{key}}}")
        else:
            segments.append(segment)
    template = f"{parts.scheme}://{parts.netloc}" if parts.netloc else ""
    template += "/".join(segments)
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        params[key] = value
    return template, params
