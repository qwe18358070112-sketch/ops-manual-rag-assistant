from __future__ import annotations

import json
import math
import os
import sqlite3
from collections import Counter
from pathlib import Path

from app.retrieval.tokenizer import build_query_text, compute_tfidf_weights, token_counts


def _load_chunk_records(chunks_dir: Path) -> list[dict]:
    records: list[dict] = []
    for path in sorted(chunks_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            records.extend(payload)
    return records


def _resolve_data_dir(project_root: Path) -> Path:
    return Path(os.getenv("OPS_ASSISTANT_DATA_DIR") or (project_root / "data")).resolve()


def build_keyword_index(database_path: Path, chunk_records: list[dict]) -> dict[str, int]:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if database_path.exists():
        database_path.unlink()

    conn = sqlite3.connect(database_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            search_text TEXT NOT NULL,
            system_name TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_group TEXT NOT NULL,
            source_file TEXT NOT NULL,
            section_id TEXT NOT NULL,
            section_title TEXT NOT NULL,
            date_start TEXT,
            date_end TEXT,
            tags_json TEXT NOT NULL,
            tag_text TEXT NOT NULL,
            page_hint TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE fts_chunks USING fts5(
            chunk_id UNINDEXED,
            title,
            content,
            search_text,
            system_name,
            source_type,
            source_group,
            section_title,
            tag_text,
            tokenize = 'unicode61'
        )
        """
    )

    chunk_rows = []
    fts_rows = []
    for record in chunk_records:
        search_text = build_query_text(" ".join([
            record.get("title", ""),
            record.get("section_title", ""),
            record.get("content", ""),
            record.get("system_name", ""),
            record.get("source_type", ""),
            record.get("source_group", ""),
            " ".join(record.get("tags", [])),
        ]))
        tags = [str(item).strip() for item in record.get("tags", []) if str(item).strip()]
        tag_text = " ".join(tags)
        chunk_rows.append(
            (
                record["chunk_id"],
                record["doc_id"],
                record["title"],
                record["content"],
                search_text,
                record["system_name"],
                record["source_type"],
                record.get("source_group", ""),
                record["source_file"],
                record["section_id"],
                record["section_title"],
                record.get("date_start"),
                record.get("date_end"),
                json.dumps(tags, ensure_ascii=False),
                tag_text,
                record.get("page_hint"),
            )
        )
        fts_rows.append(
            (
                record["chunk_id"],
                record["title"],
                record["content"],
                search_text,
                record["system_name"],
                record["source_type"],
                record.get("source_group", ""),
                record["section_title"],
                tag_text,
            )
        )

    conn.executemany(
        """
        INSERT INTO chunks (
            chunk_id, doc_id, title, content, search_text, system_name, source_type,
            source_group, source_file, section_id, section_title,
            date_start, date_end, tags_json, tag_text, page_hint
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        chunk_rows,
    )
    conn.executemany(
        """
        INSERT INTO fts_chunks (
            chunk_id, title, content, search_text, system_name, source_type, source_group, section_title, tag_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        fts_rows,
    )
    conn.commit()
    conn.close()
    return {"chunk_count": len(chunk_rows)}


def build_semantic_index(index_path: Path, chunk_records: list[dict]) -> dict[str, int]:
    doc_freq: Counter[str] = Counter()
    record_counts: list[tuple[str, Counter[str]]] = []

    for record in chunk_records:
        counts = token_counts(" ".join([
            record.get("title", ""),
            record.get("section_title", ""),
            record.get("content", ""),
            record.get("system_name", ""),
            record.get("source_type", ""),
        ]))
        record_counts.append((record["chunk_id"], counts))
        doc_freq.update(counts.keys())

    document_count = len(record_counts) or 1
    idf_map = {
        token: math.log((1 + document_count) / (1 + frequency)) + 1.0
        for token, frequency in doc_freq.items()
    }

    vectors = []
    for chunk_id, counts in record_counts:
        weights, norm = compute_tfidf_weights(counts, idf_map)
        vectors.append(
            {
                "chunk_id": chunk_id,
                "weights": weights,
                "norm": norm,
            }
        )

    payload = {
        "document_count": document_count,
        "idf_map": idf_map,
        "vectors": vectors,
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return {"vector_count": len(vectors), "term_count": len(idf_map)}


def build_indexes(project_root: Path) -> dict[str, object]:
    data_dir = _resolve_data_dir(project_root)
    chunks_dir = data_dir / "chunks"
    chunk_records = _load_chunk_records(chunks_dir)
    db_summary = build_keyword_index(data_dir / "indexes" / "retrieval.sqlite3", chunk_records)
    semantic_summary = build_semantic_index(data_dir / "indexes" / "semantic_index.json", chunk_records)
    summary = {
        "chunk_count": len(chunk_records),
        "keyword_index": db_summary,
        "semantic_index": semantic_summary,
    }
    (data_dir / "indexes" / "retrieval_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary
