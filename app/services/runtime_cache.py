from __future__ import annotations

from app.retrieval import service as retrieval_service
from app.services import case_views, manual_views


def clear_runtime_caches() -> None:
    try:
        retrieval_service._load_semantic_index.cache_clear()
    except AttributeError:
        pass
    try:
        case_views.load_chunk_records.cache_clear()
        case_views.load_case_sections.cache_clear()
    except AttributeError:
        pass
    try:
        manual_views.load_manual_sections.cache_clear()
    except AttributeError:
        pass
