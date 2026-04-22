from __future__ import annotations

import csv
import io
import json
import re
import shutil
import sqlite3
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.ingestion.pipeline import ingest_document, write_json
from app.models.records import SourceDocument, infer_source_group
from app.retrieval.index_builder import build_indexes
from app.services.runtime_cache import clear_runtime_caches

def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')


def _slugify(value: str, fallback: str = 'document') -> str:
    compact = re.sub(r'[^A-Za-z0-9\u4e00-\u9fff_-]+', '-', value.strip())
    compact = re.sub(r'-{2,}', '-', compact).strip('-_')
    return compact or fallback


def _json(value: list[str]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _parse_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item).strip() for item in payload if str(item).strip()]


def _connect(settings: Settings | None = None) -> sqlite3.Connection:
    settings = settings or get_settings()
    settings.library_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.library_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def refresh_document_metrics(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    conn = _connect(settings)
    rows = conn.execute("SELECT doc_id, status, uploaded_at FROM documents").fetchall()
    for row in rows:
        doc_id = str(row['doc_id'])
        extracted_path = settings.extracted_dir / f'{doc_id}.json'
        chunk_path = settings.chunks_dir / f'{doc_id}.json'
        section_count = 0
        chunk_count = 0
        if extracted_path.exists():
            try:
                payload = json.loads(extracted_path.read_text(encoding='utf-8'))
                section_count = len(payload.get('sections', [])) if isinstance(payload, dict) else 0
            except json.JSONDecodeError:
                section_count = 0
        if chunk_path.exists():
            try:
                payload = json.loads(chunk_path.read_text(encoding='utf-8'))
                chunk_count = len(payload) if isinstance(payload, list) else 0
            except json.JSONDecodeError:
                chunk_count = 0
        status = 'indexed' if chunk_count > 0 else str(row['status'])
        indexed_at = _now() if chunk_count > 0 else None
        conn.execute(
            """
            UPDATE documents
            SET section_count = ?, chunk_count = ?, status = ?, indexed_at = COALESCE(indexed_at, ?)
            WHERE doc_id = ?
            """,
            (section_count, chunk_count, status, indexed_at, doc_id),
        )
    conn.commit()
    conn.close()


def init_library_store(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    conn = _connect(settings)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_group TEXT NOT NULL,
            system_name TEXT NOT NULL,
            file_type TEXT NOT NULL,
            file_path TEXT NOT NULL,
            original_filename TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            notes TEXT NOT NULL DEFAULT '',
            date_start TEXT,
            date_end TEXT,
            status TEXT NOT NULL DEFAULT 'uploaded',
            origin TEXT NOT NULL DEFAULT 'uploaded',
            section_count INTEGER NOT NULL DEFAULT 0,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            file_size INTEGER NOT NULL DEFAULT 0,
            uploaded_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            indexed_at TEXT,
            last_error TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.commit()
    conn.close()
    seed_library_from_manifest(settings)
    refresh_document_metrics(settings)
    sync_ingestion_summary(settings)


def seed_library_from_manifest(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    if not settings.manifest_path.exists():
        return 0
    payload = json.loads(settings.manifest_path.read_text(encoding='utf-8'))
    if not isinstance(payload, list):
        return 0
    conn = _connect(settings)
    inserted = 0
    now = _now()
    for item in payload:
        if not isinstance(item, dict):
            continue
        doc = SourceDocument.from_dict(item)
        path = Path(doc.file_path)
        exists = conn.execute('SELECT doc_id, status FROM documents WHERE doc_id = ?', (doc.doc_id,)).fetchone()
        status = 'indexed' if (settings.chunks_dir / f'{doc.doc_id}.json').exists() else 'uploaded'
        if exists:
            conn.execute(
                """
                UPDATE documents SET
                    title = ?, source_type = ?, source_group = ?, system_name = ?, file_type = ?,
                    file_path = ?, original_filename = ?, tags_json = ?, notes = ?, date_start = ?, date_end = ?,
                    status = CASE WHEN status = 'error' THEN status ELSE ? END,
                    file_size = ?, updated_at = ?
                WHERE doc_id = ?
                """,
                (
                    doc.title,
                    doc.source_type,
                    doc.source_group,
                    doc.system_name,
                    doc.file_type,
                    doc.file_path,
                    path.name,
                    _json(doc.tags),
                    doc.notes,
                    doc.date_start,
                    doc.date_end,
                    status,
                    path.stat().st_size if path.exists() else 0,
                    now,
                    doc.doc_id,
                ),
            )
            continue
        conn.execute(
            """
            INSERT INTO documents (
                doc_id, title, source_type, source_group, system_name, file_type, file_path,
                original_filename, tags_json, notes, date_start, date_end, status, origin,
                file_size, uploaded_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc.doc_id,
                doc.title,
                doc.source_type,
                doc.source_group,
                doc.system_name,
                doc.file_type,
                doc.file_path,
                path.name,
                _json(doc.tags),
                doc.notes,
                doc.date_start,
                doc.date_end,
                status,
                'seed',
                path.stat().st_size if path.exists() else 0,
                now,
                now,
            ),
        )
        inserted += 1
    conn.commit()
    conn.close()
    return inserted


def _row_to_document(row: sqlite3.Row) -> dict[str, Any]:
    return {
        'doc_id': str(row['doc_id']),
        'title': str(row['title']),
        'source_type': str(row['source_type']),
        'source_group': str(row['source_group']),
        'system_name': str(row['system_name']),
        'file_type': str(row['file_type']),
        'file_path': str(row['file_path']),
        'original_filename': str(row['original_filename']),
        'tags': _parse_json_list(row['tags_json']),
        'notes': str(row['notes']),
        'date_start': str(row['date_start']) if row['date_start'] else None,
        'date_end': str(row['date_end']) if row['date_end'] else None,
        'status': str(row['status']),
        'origin': str(row['origin']),
        'section_count': int(row['section_count']),
        'chunk_count': int(row['chunk_count']),
        'file_size': int(row['file_size']),
        'uploaded_at': str(row['uploaded_at']),
        'updated_at': str(row['updated_at']),
        'indexed_at': str(row['indexed_at']) if row['indexed_at'] else None,
        'last_error': str(row['last_error']),
    }


def list_documents(
    *,
    query: str = '',
    source_group: str = '',
    source_type: str = '',
    system_name: str = '',
    status: str = '',
    limit: int = 200,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    conn = _connect(settings)
    clauses = ['1 = 1']
    params: list[Any] = []
    if query.strip():
        clauses.append('(title LIKE ? OR notes LIKE ? OR original_filename LIKE ?)')
        wildcard = f"%{query.strip()}%"
        params.extend([wildcard, wildcard, wildcard])
    if source_group:
        clauses.append('source_group = ?')
        params.append(source_group)
    if source_type:
        clauses.append('source_type = ?')
        params.append(source_type)
    if system_name:
        clauses.append('system_name = ?')
        params.append(system_name)
    if status:
        clauses.append('status = ?')
        params.append(status)
    rows = conn.execute(
        f"SELECT * FROM documents WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC, id DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    conn.close()
    return [_row_to_document(row) for row in rows]


def get_document(doc_id: str, settings: Settings | None = None) -> dict[str, Any] | None:
    settings = settings or get_settings()
    conn = _connect(settings)
    row = conn.execute('SELECT * FROM documents WHERE doc_id = ?', (doc_id,)).fetchone()
    conn.close()
    return _row_to_document(row) if row is not None else None


def build_source_document(payload: dict[str, Any]) -> SourceDocument:
    return SourceDocument(
        doc_id=str(payload['doc_id']),
        title=str(payload['title']),
        source_type=str(payload['source_type']),
        source_group=str(payload.get('source_group') or infer_source_group(str(payload['source_type']))),
        system_name=str(payload['system_name']),
        file_type=str(payload['file_type']).lower(),
        file_path=str(payload['file_path']),
        date_start=payload.get('date_start'),
        date_end=payload.get('date_end'),
        tags=[str(item).strip() for item in payload.get('tags', []) if str(item).strip()],
        notes=str(payload.get('notes', '')),
    )


def save_uploaded_document(
    *,
    filename: str,
    content: bytes,
    title: str,
    source_type: str,
    system_name: str,
    tags: list[str],
    notes: str = '',
    date_start: str | None = None,
    date_end: str | None = None,
    index_now: bool = True,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    ext = Path(filename).suffix.lower().lstrip('.')
    if ext not in {'docx', 'pdf', 'xlsx', 'txt'}:
        raise ValueError('仅支持 docx/pdf/xlsx/txt 文件')
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(title or Path(filename).stem, 'upload')
    doc_id = f"ops-upload-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    storage_path = settings.uploads_dir / f"{doc_id}-{slug}.{ext}"
    storage_path.write_bytes(content)
    now = _now()
    payload = {
        'doc_id': doc_id,
        'title': title.strip() or Path(filename).stem,
        'source_type': source_type.strip(),
        'source_group': infer_source_group(source_type),
        'system_name': system_name.strip() or '自定义资料库',
        'file_type': ext,
        'file_path': str(storage_path),
        'tags': [item for item in tags if item],
        'notes': notes.strip(),
        'date_start': date_start or None,
        'date_end': date_end or None,
    }
    conn = _connect(settings)
    conn.execute(
        """
        INSERT INTO documents (
            doc_id, title, source_type, source_group, system_name, file_type, file_path,
            original_filename, tags_json, notes, date_start, date_end, status, origin,
            file_size, uploaded_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload['doc_id'], payload['title'], payload['source_type'], payload['source_group'], payload['system_name'], payload['file_type'], payload['file_path'],
            filename, _json(payload['tags']), payload['notes'], payload['date_start'], payload['date_end'], 'uploaded', 'upload',
            len(content), now, now,
        ),
    )
    conn.commit()
    conn.close()
    if index_now:
        reindex_document(payload['doc_id'], settings=settings)
    return get_document(payload['doc_id'], settings=settings) or payload


def _update_document_status(
    doc_id: str,
    *,
    status: str,
    section_count: int = 0,
    chunk_count: int = 0,
    last_error: str = '',
    indexed_at: str | None = None,
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    conn = _connect(settings)
    conn.execute(
        """
        UPDATE documents
        SET status = ?, section_count = ?, chunk_count = ?, last_error = ?, indexed_at = ?, updated_at = ?
        WHERE doc_id = ?
        """,
        (status, section_count, chunk_count, last_error[:800], indexed_at, _now(), doc_id),
    )
    conn.commit()
    conn.close()


def reindex_document(doc_id: str, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    document = get_document(doc_id, settings=settings)
    if document is None:
        raise FileNotFoundError(f'未找到文档：{doc_id}')
    try:
        result = ingest_document(settings.project_root, build_source_document(document))
        build_indexes(settings.project_root)
        clear_runtime_caches()
        _update_document_status(
            doc_id,
            status='indexed',
            section_count=int(result['sections']),
            chunk_count=int(result['chunks']),
            indexed_at=_now(),
            settings=settings,
        )
        sync_ingestion_summary(settings)
    except Exception as error:  # noqa: BLE001
        _update_document_status(doc_id, status='error', last_error=str(error), settings=settings)
        raise
    return get_document(doc_id, settings=settings) or document


def rebuild_all_documents(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    documents = list_documents(limit=5000, settings=settings)
    shutil.rmtree(settings.extracted_dir, ignore_errors=True)
    shutil.rmtree(settings.chunks_dir, ignore_errors=True)
    settings.extracted_dir.mkdir(parents=True, exist_ok=True)
    settings.chunks_dir.mkdir(parents=True, exist_ok=True)

    success = 0
    errors: list[dict[str, str]] = []
    for document in documents:
        try:
            result = ingest_document(settings.project_root, build_source_document(document))
            _update_document_status(
                document['doc_id'],
                status='indexed',
                section_count=int(result['sections']),
                chunk_count=int(result['chunks']),
                indexed_at=_now(),
                settings=settings,
            )
            success += 1
        except Exception as error:  # noqa: BLE001
            _update_document_status(document['doc_id'], status='error', last_error=str(error), settings=settings)
            errors.append({'doc_id': document['doc_id'], 'error': str(error)})
    build_indexes(settings.project_root)
    clear_runtime_caches()
    sync_ingestion_summary(settings)
    return {
        'document_count': len(documents),
        'indexed_count': success,
        'error_count': len(errors),
        'errors': errors,
    }


def sync_ingestion_summary(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    documents = list_documents(limit=5000, settings=settings)
    summary_docs: list[dict[str, Any]] = []
    section_count = 0
    chunk_count = 0
    for document in documents:
        section_count += int(document['section_count'])
        chunk_count += int(document['chunk_count'])
        summary_docs.append(
            {
                'doc_id': document['doc_id'],
                'title': document['title'],
                'source_type': document['source_type'],
                'source_group': document['source_group'],
                'system_name': document['system_name'],
                'file_type': document['file_type'],
                'file_path': document['file_path'],
                'date_start': document['date_start'],
                'date_end': document['date_end'],
                'tags': document['tags'],
                'sections': document['section_count'],
                'chunks': document['chunk_count'],
                'status': document['status'],
                'origin': document['origin'],
            }
        )
    summary = {
        'document_count': len(summary_docs),
        'section_count': section_count,
        'chunk_count': chunk_count,
        'documents': summary_docs,
    }
    write_json(settings.indexes_dir / 'ingestion_summary.json', summary)
    return summary


def build_library_analysis(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    documents = list_documents(limit=5000, settings=settings)
    status_counts = Counter(document['status'] for document in documents)
    source_type_counts = Counter(document['source_type'] for document in documents)
    source_group_counts = Counter(document['source_group'] for document in documents)
    system_counts = Counter(document['system_name'] for document in documents)
    file_type_counts = Counter(document['file_type'] for document in documents)
    tag_counts = Counter(tag for document in documents for tag in document['tags'])
    timeline_counts = Counter((document['date_start'] or '')[:7] for document in documents if document['date_start'])
    indexed_docs = [document for document in documents if document['status'] == 'indexed']
    return {
        'totals': {
            'documents': len(documents),
            'indexed': len(indexed_docs),
            'pending': status_counts.get('uploaded', 0),
            'errors': status_counts.get('error', 0),
            'manuals': source_group_counts.get('manual', 0),
            'cases': source_group_counts.get('case', 0),
        },
        'status_counts': dict(status_counts),
        'source_type_counts': dict(source_type_counts),
        'source_group_counts': dict(source_group_counts),
        'system_counts': dict(system_counts),
        'file_type_counts': dict(file_type_counts),
        'tag_counts': dict(tag_counts.most_common(12)),
        'timeline_counts': dict(sorted(timeline_counts.items())),
        'recent_documents': documents[:8],
    }


def export_documents(fmt: str = 'csv', *, settings: Settings | None = None) -> tuple[str, str, bytes]:
    settings = settings or get_settings()
    documents = list_documents(limit=5000, settings=settings)
    if fmt == 'json':
        payload = json.dumps(documents, ensure_ascii=False, indent=2).encode('utf-8')
        return 'library-documents.json', 'application/json; charset=utf-8', payload
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'doc_id', 'title', 'source_type', 'source_group', 'system_name', 'file_type', 'original_filename',
        'status', 'origin', 'section_count', 'chunk_count', 'date_start', 'date_end', 'tags', 'uploaded_at', 'updated_at', 'indexed_at', 'notes'
    ])
    for document in documents:
        writer.writerow([
            document['doc_id'], document['title'], document['source_type'], document['source_group'], document['system_name'], document['file_type'], document['original_filename'],
            document['status'], document['origin'], document['section_count'], document['chunk_count'], document['date_start'] or '', document['date_end'] or '',
            ' / '.join(document['tags']), document['uploaded_at'], document['updated_at'], document['indexed_at'] or '', document['notes'],
        ])
    return 'library-documents.csv', 'text/csv; charset=utf-8', output.getvalue().encode('utf-8-sig')


def export_analysis(fmt: str = 'json', *, settings: Settings | None = None) -> tuple[str, str, bytes]:
    analysis = build_library_analysis(settings)
    if fmt == 'csv':
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['category', 'name', 'count'])
        for category in ['status_counts', 'source_type_counts', 'source_group_counts', 'system_counts', 'file_type_counts', 'tag_counts', 'timeline_counts']:
            for name, count in analysis.get(category, {}).items():
                writer.writerow([category, name, count])
        return 'library-analysis.csv', 'text/csv; charset=utf-8', output.getvalue().encode('utf-8-sig')
    return 'library-analysis.json', 'application/json; charset=utf-8', json.dumps(analysis, ensure_ascii=False, indent=2).encode('utf-8')
