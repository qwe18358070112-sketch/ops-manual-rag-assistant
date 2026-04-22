from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings
LOW_SIGNAL_TITLES = {
    '目录',
    '操作手册',
    '应急预案',
    '官方手册',
}


def _read_chunk_files() -> list[dict]:
    records: list[dict] = []
    settings = get_settings()
    for path in sorted(settings.chunks_dir.glob('*.json')):
        payload = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(payload, list):
            records.extend(payload)
    return records


@lru_cache(maxsize=1)
def load_manual_sections() -> list[dict]:
    grouped: dict[str, dict] = {}
    for record in _read_chunk_files():
        if str(record.get('source_group', '')) != 'manual':
            continue
        section_id = str(record.get('section_id', '')).strip()
        if not section_id:
            continue
        item = grouped.setdefault(
            section_id,
            {
                'section_id': section_id,
                'doc_id': str(record.get('doc_id', '')),
                'title': str(record.get('title', '')),
                'section_title': str(record.get('section_title', '')),
                'system_name': str(record.get('system_name', '')),
                'source_type': str(record.get('source_type', '')),
                'source_group': str(record.get('source_group', '')),
                'source_file': str(record.get('source_file', '')),
                'page_hints': [],
                'tags': [],
                'content_parts': [],
                'chunk_count': 0,
            },
        )
        item['chunk_count'] += 1
        content = str(record.get('content', '')).strip()
        if content and content not in item['content_parts']:
            item['content_parts'].append(content)
        page_hint = str(record.get('page_hint', '') or '').strip()
        if page_hint and page_hint not in item['page_hints']:
            item['page_hints'].append(page_hint)
        for tag in record.get('tags', []):
            normalized = str(tag).strip()
            if normalized and normalized not in item['tags']:
                item['tags'].append(normalized)

    sections: list[dict] = []
    for item in grouped.values():
        content = '\n'.join(item.pop('content_parts')).strip()
        compact = ' '.join(content.split())
        item['content'] = content
        item['summary'] = compact[:240] + ('...' if len(compact) > 240 else '')
        sections.append(item)

    sections.sort(
        key=lambda entry: (
            Path(str(entry.get('source_file', ''))).name,
            str(entry.get('page_hints', [''])[0] if entry.get('page_hints') else ''),
            str(entry.get('section_id', '')),
        )
    )
    return sections


def _is_low_signal_entry(entry: dict) -> bool:
    section_title = str(entry.get('section_title', '')).strip()
    title = str(entry.get('title', '')).strip()
    if not section_title:
        return True
    if section_title in LOW_SIGNAL_TITLES:
        return True
    if section_title == title and len(section_title) <= 6:
        return True
    return False


def get_manual_detail(section_id: str) -> dict[str, object] | None:
    normalized_section_id = str(section_id).strip()
    if not normalized_section_id:
        return None
    for entry in load_manual_sections():
        if str(entry.get('section_id', '')) == normalized_section_id and not _is_low_signal_entry(entry):
            return entry
    return None
