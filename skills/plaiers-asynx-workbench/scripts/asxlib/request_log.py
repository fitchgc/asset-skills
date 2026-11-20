from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Mapping

MAX_LOG_BYTES = 5 * 1024 * 1024
MAX_FIELD_BYTES = 64 * 1024
_lock = threading.Lock()


def log_path() -> Path:
    override = os.environ.get("ASYNX_LOG_PATH")
    if override:
        return Path(override).expanduser()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Asynx" / "logs" / "failed-requests.jsonl"
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return state_home / "asx" / "logs" / "failed-requests.jsonl"


def _headers(headers: Mapping[str, Any] | Any | None) -> dict[str, str]:
    if not headers:
        return {}
    out: dict[str, str] = {}
    for key, value in dict(headers.items()).items():
        out[str(key)] = str(value)
    return out


def _text(data: bytes | str | None) -> dict[str, Any] | None:
    if data is None:
        return None
    raw = data.encode("utf-8") if isinstance(data, str) else data
    head = raw[:MAX_FIELD_BYTES]
    text = head.decode("utf-8", errors="replace")
    if b"\x00" in head[:1024] or text.count("\ufffd") > 8:
        return {"binary": True, "bytes": len(raw)}
    return {"text": text, "bytes": len(raw), "truncated": len(raw) > MAX_FIELD_BYTES}


def log_failed_request(
    *,
    method: str,
    url: str,
    request_headers: Mapping[str, Any] | None = None,
    request_body: bytes | str | None = None,
    status: int | None = None,
    response_headers: Mapping[str, Any] | Any | None = None,
    response_body: bytes | str | None = None,
    error: str | None = None,
    attempt: int | None = None,
) -> None:
    """Append one failed request as a JSON line. Never raises."""
    try:
        entry = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "method": method,
            "url": url,
            "attempt": attempt,
            "status": status,
            "error": error,
            "request_headers": _headers(request_headers),
            "request_body": _text(request_body),
            "response_headers": _headers(response_headers),
            "response_body": _text(response_body),
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        path = log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            path = Path(tempfile.gettempdir()) / "asx" / "logs" / path.name
            path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            try:
                if path.stat().st_size > MAX_LOG_BYTES:
                    path.replace(path.with_suffix(path.suffix + ".1"))
            except OSError:
                pass
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception:
        pass
