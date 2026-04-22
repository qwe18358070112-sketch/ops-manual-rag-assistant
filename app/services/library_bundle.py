from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from app.core.config import get_settings
from app.services.library import init_library_store, list_documents, rebuild_all_documents


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def export_bundle(output_path: Path) -> Path:
    settings = get_settings()
    docs = list_documents(limit=5000, settings=settings)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="ops-assistant-bundle-") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        files_dir = temp_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        bundled_docs: list[dict] = []

        for doc in docs:
            source_path = Path(str(doc["file_path"]))
            if not source_path.exists():
                continue
            bundle_name = f"{doc['doc_id']}-{Path(str(doc['original_filename']) or source_path.name).name}"
            target = files_dir / bundle_name
            shutil.copy2(source_path, target)
            bundled_docs.append(
                {
                    "doc_id": doc["doc_id"],
                    "title": doc["title"],
                    "source_type": doc["source_type"],
                    "source_group": doc["source_group"],
                    "system_name": doc["system_name"],
                    "file_type": doc["file_type"],
                    "original_filename": doc["original_filename"],
                    "tags": doc["tags"],
                    "notes": doc["notes"],
                    "date_start": doc["date_start"],
                    "date_end": doc["date_end"],
                    "origin": doc["origin"],
                    "bundle_file": f"files/{bundle_name}",
                }
            )

        manifest = {
            "app_title": settings.app_title,
            "document_count": len(bundled_docs),
            "documents": bundled_docs,
        }
        (temp_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in temp_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(temp_dir))
    return output_path


def import_bundle(bundle_path: Path, replace_existing: bool = False) -> dict[str, int]:
    settings = get_settings()
    init_library_store(settings)

    with tempfile.TemporaryDirectory(prefix="ops-assistant-import-") as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        with zipfile.ZipFile(bundle_path, "r") as zf:
            zf.extractall(temp_dir)

        manifest = json.loads((temp_dir / "manifest.json").read_text(encoding="utf-8"))
        docs = manifest.get("documents", [])
        if not isinstance(docs, list):
            raise ValueError("迁移包 manifest.json 格式不正确")

        conn = _connect(settings.library_db_path)
        if replace_existing:
            rows = conn.execute("SELECT file_path FROM documents WHERE origin = 'upload' OR origin = 'bundle_import'").fetchall()
            for row in rows:
                path = Path(str(row["file_path"]))
                if path.exists() and settings.uploads_dir in path.parents:
                    path.unlink()
            conn.execute("DELETE FROM documents")
            conn.commit()

        imported = 0
        skipped_existing = 0
        for item in docs:
            if not isinstance(item, dict):
                continue
            bundle_file = temp_dir / str(item.get("bundle_file", "")).strip()
            if not bundle_file.exists():
                continue
            doc_id = str(item["doc_id"])
            existing = conn.execute("SELECT doc_id FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
            if existing is not None and not replace_existing:
                skipped_existing += 1
                continue
            original_filename = str(item.get("original_filename", "") or bundle_file.name)
            storage_name = f"{doc_id}-{Path(original_filename).name}"
            target_path = settings.uploads_dir / storage_name
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundle_file, target_path)
            conn.execute(
                """
                INSERT OR REPLACE INTO documents (
                    doc_id, title, source_type, source_group, system_name, file_type, file_path,
                    original_filename, tags_json, notes, date_start, date_end, status, origin,
                    file_size, uploaded_at, updated_at, indexed_at, last_error, section_count, chunk_count
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), NULL, '', 0, 0
                )
                """,
                (
                    doc_id,
                    str(item.get("title", "") or Path(original_filename).stem),
                    str(item.get("source_type", "internal_manual")),
                    str(item.get("source_group", "")),
                    str(item.get("system_name", "政企运维知识库")),
                    str(item.get("file_type", Path(original_filename).suffix.lstrip("."))),
                    str(target_path),
                    original_filename,
                    json.dumps(item.get("tags", []), ensure_ascii=False),
                    str(item.get("notes", "")),
                    str(item.get("date_start", "") or ""),
                    str(item.get("date_end", "") or ""),
                    "uploaded",
                    "bundle_import",
                    target_path.stat().st_size,
                ),
            )
            imported += 1
        conn.commit()
        conn.close()

    summary = rebuild_all_documents(settings)
    summary["imported"] = imported
    summary["skipped_existing"] = skipped_existing
    return summary
