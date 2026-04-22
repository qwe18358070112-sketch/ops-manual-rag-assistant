from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from app.api.routes import router as api_router
from app.core.config import get_settings
from app.core.logging import AccessLogMiddleware
from app.services.library import init_library_store
from app.web.routes import router as web_router

settings = get_settings()

app = FastAPI(title="政企运维知识库问答助手", version="0.2.0")
app.add_middleware(AccessLogMiddleware, settings=settings)
app.mount('/static', StaticFiles(directory=str(settings.static_dir)), name='static')
app.include_router(web_router)
app.include_router(api_router)


@app.on_event('startup')
def _startup() -> None:
    init_library_store(settings)


@app.get('/', include_in_schema=False)
def _root_redirect():
    return RedirectResponse('/app', status_code=307)
