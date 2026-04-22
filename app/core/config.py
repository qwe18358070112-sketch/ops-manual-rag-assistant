from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    data_dir: Path
    uploads_dir: Path
    extracted_dir: Path
    chunks_dir: Path
    indexes_dir: Path
    logs_dir: Path
    templates_dir: Path
    static_dir: Path
    manifest_path: Path
    library_db_path: Path
    access_log_path: Path
    env_name: str
    app_host: str
    app_port: int
    require_auth: bool
    admin_username: str
    admin_password: str
    admin_token: str
    app_title: str = "政企运维知识库问答助手"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    project_root = Path(os.getenv('OPS_ASSISTANT_PROJECT_ROOT') or Path(__file__).resolve().parents[2]).resolve()
    data_dir = Path(os.getenv('OPS_ASSISTANT_DATA_DIR') or (project_root / 'data')).resolve()
    return Settings(
        project_root=project_root,
        data_dir=data_dir,
        uploads_dir=data_dir / 'raw' / 'uploads',
        extracted_dir=data_dir / 'extracted',
        chunks_dir=data_dir / 'chunks',
        indexes_dir=data_dir / 'indexes',
        logs_dir=data_dir / 'logs',
        templates_dir=project_root / 'app' / 'templates',
        static_dir=project_root / 'app' / 'static',
        manifest_path=data_dir / 'manifests' / 'seed_documents.json',
        library_db_path=data_dir / 'library' / 'metadata.sqlite3',
        access_log_path=data_dir / 'logs' / 'access.log',
        env_name=os.getenv('OPS_ASSISTANT_ENV', 'local').strip() or 'local',
        app_host=os.getenv('OPS_ASSISTANT_HOST', '0.0.0.0').strip() or '0.0.0.0',
        app_port=int(os.getenv('OPS_ASSISTANT_PORT', '8000').strip() or '8000'),
        require_auth=_to_bool(os.getenv('OPS_ASSISTANT_REQUIRE_AUTH'), default=False),
        admin_username=os.getenv('OPS_ASSISTANT_ADMIN_USERNAME', 'admin').strip() or 'admin',
        admin_password=os.getenv('OPS_ASSISTANT_ADMIN_PASSWORD', 'admin123').strip() or 'admin123',
        admin_token=os.getenv('OPS_ASSISTANT_ADMIN_TOKEN', '').strip(),
    )
