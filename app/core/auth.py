from __future__ import annotations

import hashlib
from urllib.parse import quote

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.core.config import Settings, get_settings

SESSION_COOKIE = 'ops_assistant_admin'


def _session_value(settings: Settings) -> str:
    payload = f"{settings.admin_username}:{settings.admin_password}:{settings.app_title}"
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def is_authenticated(request: Request, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if not settings.require_auth:
        return True
    token = request.headers.get('x-admin-token', '').strip()
    if settings.admin_token and token == settings.admin_token:
        return True
    return request.cookies.get(SESSION_COOKIE, '') == _session_value(settings)


def ensure_admin_api(request: Request, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if is_authenticated(request, settings):
        return
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='需要管理员权限')


def ensure_admin_page(request: Request, settings: Settings | None = None):
    settings = settings or get_settings()
    if is_authenticated(request, settings):
        return None
    next_path = quote(str(request.url.path) + (f"?{request.url.query}" if request.url.query else ''))
    return RedirectResponse(url=f'/login?next={next_path}', status_code=status.HTTP_302_FOUND)


def issue_admin_session(response: RedirectResponse, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        _session_value(settings),
        httponly=True,
        samesite='lax',
        secure=False,
        max_age=8 * 60 * 60,
    )


def clear_admin_session(response: RedirectResponse) -> None:
    response.delete_cookie(SESSION_COOKIE)


def credentials_valid(username: str, password: str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return username.strip() == settings.admin_username and password == settings.admin_password
