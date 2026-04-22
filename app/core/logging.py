from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.config import Settings, get_settings

_LOG_LOCK = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False)
    with _LOG_LOCK:
        with path.open('a', encoding='utf-8') as handle:
            handle.write(line + '\n')


class AccessLogMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings | None = None):
        super().__init__(app)
        self.settings = settings or get_settings()

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        payload = {
            'ts': _utc_now(),
            'method': request.method,
            'path': request.url.path,
            'query': request.url.query,
            'status_code': response.status_code,
            'duration_ms': duration_ms,
            'client': request.client.host if request.client else '',
            'user_agent': request.headers.get('user-agent', ''),
            'has_admin_cookie': bool(request.cookies.get('ops_assistant_admin')),
            'has_admin_token': bool(request.headers.get('x-admin-token')),
        }
        _append_jsonl(self.settings.access_log_path, payload)
        return response


def load_recent_access_logs(limit: int = 20, settings: Settings | None = None) -> list[dict[str, object]]:
    settings = settings or get_settings()
    if not settings.access_log_path.exists():
        return []
    lines = settings.access_log_path.read_text(encoding='utf-8').splitlines()
    entries: list[dict[str, object]] = []
    for line in lines[-limit:]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return list(reversed(entries))
