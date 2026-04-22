from __future__ import annotations

import json
import os
from pathlib import Path

from app.ingestion.chunker import chunk_document
from app.ingestion.extractors import extract_document
from app.models.records import SourceDocument


def load_manifest(manifest_path: Path) -> list[SourceDocument]:
    payload = json.loads(manifest_path.read_text(encoding='utf-8'))
    return [SourceDocument.from_dict(item) for item in payload]


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def _resolve_data_dir(project_root: Path) -> Path:
    return Path(os.getenv('OPS_ASSISTANT_DATA_DIR') or (project_root / 'data')).resolve()


def ingest_document(project_root: Path, document: SourceDocument) -> dict[str, object]:
    data_dir = _resolve_data_dir(project_root)
    extracted_dir = data_dir / 'extracted'
    chunks_dir = data_dir / 'chunks'
    extracted = extract_document(document)
    chunks = chunk_document(extracted)
    write_json(extracted_dir / f'{document.doc_id}.json', extracted.to_dict())
    write_json(chunks_dir / f'{document.doc_id}.json', [chunk.to_dict() for chunk in chunks])
    return {
        'doc_id': document.doc_id,
        'title': document.title,
        'source_type': document.source_type,
        'source_group': document.source_group,
        'system_name': document.system_name,
        'file_type': document.file_type,
        'file_path': document.file_path,
        'date_start': document.date_start,
        'date_end': document.date_end,
        'tags': document.tags,
        'sections': len(extracted.sections),
        'chunks': len(chunks),
    }


def ingest_documents(project_root: Path, manifest_path: Path, limit: int | None = None) -> dict[str, object]:
    documents = load_manifest(manifest_path)
    if limit is not None:
        documents = documents[:limit]

    summary_docs: list[dict[str, object]] = []
    total_sections = 0
    total_chunks = 0
    for document in documents:
        result = ingest_document(project_root, document)
        total_sections += int(result['sections'])
        total_chunks += int(result['chunks'])
        summary_docs.append(result)

    summary = {
        'document_count': len(summary_docs),
        'section_count': total_sections,
        'chunk_count': total_chunks,
        'documents': summary_docs,
    }
    write_json(_resolve_data_dir(project_root) / 'indexes' / 'ingestion_summary.json', summary)
    return summary
