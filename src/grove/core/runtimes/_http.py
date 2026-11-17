"""What a REST driver may repeat from a platform's error response.

A server's validation error routinely echoes the request it refused, and the
request is where a driver puts the worker's bearer and a registry password. So
a raw response body never reaches an error message, a log or a client: only the
fields a server writes for a human, else the status line.
"""

from __future__ import annotations

from typing import Any

import httpx


def response_detail(response: httpx.Response) -> str:
    """The platform's own words for a refusal, never the request it may echo.

    Reads ``message``, else every ``detail[*].msg`` (a FastAPI-style validation
    error), else a string ``detail``/``error``; with none of those it is
    ``<status> <reason>``. ``input`` and the raw body are never read.
    """
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        message = body.get("message")
        if isinstance(message, str) and message.strip():
            code = body.get("code")
            return f"{code}: {message.strip()}" if isinstance(code, str) and code else message
        detail = body.get("detail")
        if isinstance(detail, list):
            parts = [
                str(item["msg"]) for item in detail if isinstance(item, dict) and item.get("msg")
            ]
            if parts:
                return "; ".join(parts)
        for key in ("detail", "error"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return f"{response.status_code} {response.reason_phrase}".strip()


__all__ = ["response_detail"]
