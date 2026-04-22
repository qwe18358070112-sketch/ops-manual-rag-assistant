from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache

from app.core.config import get_settings
GENERIC_TOPIC_TAGS = {
    "案例库",
    "周报",
    "月报",
    "手册",
    "预案",
    "官方手册",
    "历史案例",
    "手册问答",
    "综治中心",
    "视频平台",
    "视联网",
    "平安风险智控",
    "政企驻场运维",
}

LOW_SIGNAL_TITLES = {
    "工作周报",
    "周报",
    "月报",
    "运维报告",
}


def _read_chunk_files() -> list[dict]:
    records: list[dict] = []
    settings = get_settings()
    for path in sorted(settings.chunks_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            records.extend(payload)
    return records


@lru_cache(maxsize=1)
def load_chunk_records() -> list[dict]:
    return _read_chunk_files()


@lru_cache(maxsize=1)
def load_case_sections() -> list[dict]:
    grouped: dict[str, dict] = {}
    for record in load_chunk_records():
        if str(record.get("source_group", "")) != "case":
            continue
        section_id = str(record.get("section_id", ""))
        item = grouped.setdefault(
            section_id,
            {
                "section_id": section_id,
                "doc_id": str(record.get("doc_id", "")),
                "title": str(record.get("title", "")),
                "section_title": str(record.get("section_title", "")),
                "system_name": str(record.get("system_name", "")),
                "source_type": str(record.get("source_type", "")),
                "source_group": str(record.get("source_group", "")),
                "source_file": str(record.get("source_file", "")),
                "date_start": str(record.get("date_start", "") or ""),
                "date_end": str(record.get("date_end", "") or ""),
                "page_hint": str(record.get("page_hint", "") or ""),
                "tags": [],
                "content_parts": [],
                "chunk_count": 0,
            },
        )
        item["chunk_count"] += 1
        content = str(record.get("content", "")).strip()
        if content and content not in item["content_parts"]:
            item["content_parts"].append(content)
        for tag in record.get("tags", []):
            normalized = str(tag).strip()
            if normalized and normalized not in item["tags"]:
                item["tags"].append(normalized)

    sections: list[dict] = []
    for item in grouped.values():
        content = "\n".join(item.pop("content_parts")).strip()
        compact = " ".join(content.split())
        item["content"] = content
        item["summary"] = compact[:220] + ("..." if len(compact) > 220 else "")
        sections.append(item)

    sections.sort(
        key=lambda entry: (
            entry.get("date_end", ""),
            entry.get("date_start", ""),
            entry.get("section_id", ""),
        ),
        reverse=True,
    )
    return sections


def _matches_filters(entry: dict, filters: dict[str, str] | None) -> bool:
    if not filters:
        return True
    system_name = str(filters.get("system_name", "") or "").strip()
    source_type = str(filters.get("source_type", "") or "").strip()
    tag = str(filters.get("tag", "") or "").strip()
    date_from = str(filters.get("date_from", "") or "").strip()
    date_to = str(filters.get("date_to", "") or "").strip()

    if system_name and entry.get("system_name") != system_name:
        return False
    if source_type and entry.get("source_type") != source_type:
        return False
    if tag and tag not in entry.get("tags", []):
        return False
    if date_from and not entry.get("date_end"):
        return False
    if date_to and not entry.get("date_start"):
        return False
    if date_from and str(entry.get("date_end", "")) < date_from:
        return False
    if date_to and str(entry.get("date_start", "")) > date_to:
        return False
    return True


def _is_low_signal_entry(entry: dict) -> bool:
    section_title = str(entry.get("section_title", "")).strip()
    summary = str(entry.get("summary", "")).strip()
    source_title = str(entry.get("title", "")).strip()
    if not section_title:
        return True
    if section_title in LOW_SIGNAL_TITLES:
        return True
    if section_title == source_title:
        return True
    if section_title.isdigit():
        return True
    if summary in LOW_SIGNAL_TITLES:
        return True
    return False


def build_case_timeline(filters: dict[str, str] | None = None, limit: int = 40) -> list[dict]:
    results = [
        entry
        for entry in load_case_sections()
        if _matches_filters(entry, filters) and not _is_low_signal_entry(entry)
    ]
    return results[:limit]


def build_topic_view(filters: dict[str, str] | None = None, limit_topics: int = 12, sample_size: int = 3) -> list[dict]:
    entries = [
        entry
        for entry in load_case_sections()
        if _matches_filters(entry, filters) and not _is_low_signal_entry(entry)
    ]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        tags = [tag for tag in entry.get("tags", []) if tag not in GENERIC_TOPIC_TAGS]
        if not tags:
            continue
        for tag in tags:
            grouped[tag].append(entry)

    topics: list[dict] = []
    for tag, rows in grouped.items():
        sorted_rows = sorted(
            rows,
            key=lambda entry: (
                entry.get("date_end", ""),
                entry.get("date_start", ""),
                entry.get("section_id", ""),
            ),
            reverse=True,
        )
        topics.append(
            {
                "tag": tag,
                "count": len(sorted_rows),
                "latest_date": sorted_rows[0].get("date_end") or sorted_rows[0].get("date_start") or "",
                "samples": [
                    {
                        "section_id": row["section_id"],
                        "section_title": row["section_title"],
                        "system_name": row["system_name"],
                        "source_type": row["source_type"],
                        "source_file": row["source_file"],
                        "date_start": row["date_start"],
                        "date_end": row["date_end"],
                        "summary": row["summary"],
                    }
                    for row in sorted_rows[:sample_size]
                ],
            }
        )

    topics.sort(key=lambda item: (item["count"], item["latest_date"], item["tag"]), reverse=True)
    return topics[:limit_topics]


def build_topic_detail(tag: str, filters: dict[str, str] | None = None, limit: int = 100) -> dict[str, object]:
    normalized_tag = str(tag).strip()
    topic_filters = dict(filters or {})
    topic_filters["tag"] = normalized_tag
    entries = build_case_timeline(filters=topic_filters, limit=limit)
    latest_date = ""
    if entries:
        latest_date = entries[0].get("date_end") or entries[0].get("date_start") or ""
    return {
        "tag": normalized_tag,
        "count": len(entries),
        "latest_date": latest_date,
        "filters": topic_filters,
        "entries": entries,
    }


def get_case_detail(section_id: str) -> dict[str, object] | None:
    normalized_section_id = str(section_id).strip()
    if not normalized_section_id:
        return None
    for entry in load_case_sections():
        if str(entry.get("section_id", "")) == normalized_section_id and not _is_low_signal_entry(entry):
            return entry
    return None
