from __future__ import annotations

import json
import math
import sqlite3
from functools import lru_cache

from app.core.config import get_settings
from app.retrieval.tokenizer import build_query_text, compute_tfidf_weights, token_counts, tokenize

SEARCH_MODE_SOURCE_GROUPS = {
    "all": [],
    "manual_qa": ["manual"],
    "case_search": ["case"],
}
FOCUS_TERMS = {
    "蓝屏",
    "黑屏",
    "无画面",
    "无信号",
    "信号源",
    "共享",
    "证书",
    "更新",
    "登录",
    "卡顿",
    "巡检",
    "重启",
    "恢复",
    "故障",
    "应急",
    "目录",
    "点位",
    "权限",
    "告警",
    "预案",
    "hdmi",
}
ACTION_HINTS = {
    "步骤",
    "检查",
    "点击",
    "重启",
    "排查",
    "执行",
    "登录",
    "勾选",
    "输入",
    "切换",
    "恢复",
    "先",
    "如仍",
}
PROCEDURE_HINTS = {
    "怎么",
    "如何",
    "步骤",
    "操作",
    "先查",
    "怎么办",
    "如何处理",
}
INCIDENT_HINTS = {
    "案例",
    "报告",
    "记录",
    "遇到",
    "发生",
    "恢复",
    "卡顿",
    "离线",
    "登录不上",
    "报错",
    "异常",
}
SOURCE_TYPE_WEIGHTS = {
    "procedure": {
        "internal_manual": 1.08,
        "emergency_plan": 1.06,
        "official_manual": 1.0,
        "monthly_report": 0.9,
        "weekly_report": 0.86,
    },
    "incident": {
        "monthly_report": 1.08,
        "weekly_report": 1.05,
        "emergency_plan": 1.0,
        "internal_manual": 0.96,
        "official_manual": 0.92,
    },
    "general": {
        "internal_manual": 1.02,
        "emergency_plan": 1.0,
        "official_manual": 1.0,
        "monthly_report": 0.96,
        "weekly_report": 0.95,
    },
}
MIN_RELEVANCE_COVERAGE = {
    "procedure": 0.18,
    "incident": 0.18,
    "general": 0.22,
}
LOW_SIGNAL_QUERY_CHARS = {"的", "了", "和", "与", "及", "或"}


def _db_path():
    return get_settings().indexes_dir / "retrieval.sqlite3"


def _semantic_index_path():
    return get_settings().indexes_dir / "semantic_index.json"


def _build_fts_query(query: str) -> str:
    terms = build_query_text(query).split()
    safe_terms = []
    for term in terms[:12]:
        term = term.replace('"', ' ')
        term = term.strip()
        if term:
            safe_terms.append(f'"{term}"')
    return " OR ".join(safe_terms)


def _build_filters(filters: dict[str, str] | None) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    if not filters:
        return "", params
    search_mode = (filters.get("search_mode") or "all").strip()
    if search_mode in SEARCH_MODE_SOURCE_GROUPS and SEARCH_MODE_SOURCE_GROUPS[search_mode]:
        groups = SEARCH_MODE_SOURCE_GROUPS[search_mode]
        clauses.append("(" + " OR ".join("chunks.source_group = ?" for _ in groups) + ")")
        params.extend(groups)
    if filters.get("system_name"):
        clauses.append("chunks.system_name = ?")
        params.append(filters["system_name"])
    if filters.get("source_type"):
        clauses.append("chunks.source_type = ?")
        params.append(filters["source_type"])
    if filters.get("source_group"):
        clauses.append("chunks.source_group = ?")
        params.append(filters["source_group"])
    if filters.get("tag"):
        clauses.append("instr(chunks.tag_text, ?) > 0")
        params.append(filters["tag"])
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    if date_from:
        clauses.append("chunks.date_end IS NOT NULL AND chunks.date_end >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("chunks.date_start IS NOT NULL AND chunks.date_start <= ?")
        params.append(date_to)
    if not clauses:
        return "", params
    return " AND " + " AND ".join(clauses), params


def _normalize_rank(score: float) -> float:
    return 1.0 / (1.0 + max(score, 0.0))


def _normalize_bm25_scores(rows: list[sqlite3.Row]) -> dict[str, float]:
    if not rows:
        return {}
    raw_scores = {str(row["chunk_id"]): float(row["bm25_score"]) for row in rows}
    best = min(raw_scores.values())
    worst = max(raw_scores.values())
    if math.isclose(best, worst, rel_tol=1e-9, abs_tol=1e-9):
        return {chunk_id: 1.0 for chunk_id in raw_scores}
    return {
        chunk_id: (worst - score) / (worst - best)
        for chunk_id, score in raw_scores.items()
    }


def _make_snippet(content: str, query: str, max_length: int = 220) -> str:
    compact = " ".join(content.split())
    if len(compact) <= max_length:
        return compact
    lower_content = compact.lower()
    for raw_term in build_query_text(query).split():
        position = lower_content.find(raw_term.lower())
        if position >= 0:
            start = max(0, position - 40)
            end = min(len(compact), position + max_length - 40)
            snippet = compact[start:end].strip()
            if start > 0:
                snippet = "..." + snippet
            if end < len(compact):
                snippet = snippet + "..."
            return snippet
    return compact[:max_length].strip() + "..."


def _normalized_text(*parts: str) -> str:
    return " ".join(part.strip().lower() for part in parts if part).strip()


def _coverage_score(text: str, query_terms: list[str]) -> float:
    if not query_terms:
        return 0.0
    text_tokens = set(tokenize(text))
    matched = sum(1 for term in set(query_terms) if term in text_tokens)
    return matched / max(len(set(query_terms)), 1)


def _filter_query_terms(query_terms: list[str]) -> list[str]:
    filtered: list[str] = []
    for term in query_terms:
        if len(term) == 2 and any(char in term for char in LOW_SIGNAL_QUERY_CHARS):
            continue
        filtered.append(term)
    return filtered


def _matched_query_terms(text: str, query_terms: list[str]) -> set[str]:
    if not query_terms:
        return set()
    text_tokens = set(tokenize(text))
    strong_terms = set(_filter_query_terms(query_terms))
    return {term for term in strong_terms if term in text_tokens or term in text}


def _exact_phrase_bonus(query: str, text: str) -> float:
    compact_query = "".join(query.lower().split())
    compact_text = "".join(text.lower().split())
    if compact_query and compact_query in compact_text:
        return 1.0
    return 0.0


def _extract_focus_terms(query: str) -> list[str]:
    compact_query = "".join(query.lower().split())
    return [term for term in FOCUS_TERMS if term in compact_query]


def detect_query_mode(query: str) -> str:
    compact_query = "".join(query.lower().split())
    if any(hint in compact_query for hint in PROCEDURE_HINTS):
        return "procedure"
    if any(hint in compact_query for hint in INCIDENT_HINTS):
        return "incident"
    return "general"


def source_type_weight(source_type: str, query_mode: str) -> float:
    return SOURCE_TYPE_WEIGHTS.get(query_mode, SOURCE_TYPE_WEIGHTS["general"]).get(source_type, 1.0)


def _actionability_score(record: dict) -> float:
    text = _normalized_text(
        str(record.get("section_title", "")),
        str(record.get("snippet", "")),
        str(record.get("content", ""))[:260],
    )
    if not text:
        return 0.0
    hits = sum(1 for hint in ACTION_HINTS if hint in text)
    return hits / len(ACTION_HINTS)


def assess_result_confidence(query: str, results: list[dict], window: int = 5) -> dict[str, object]:
    focus_terms = _extract_focus_terms(query)
    if not results:
        return {
            "level": "low",
            "matched_focus_terms": [],
            "missing_focus_terms": focus_terms,
        }
    top_rows = results[:window]
    merged_text = " ".join(
        _normalized_text(
            str(record.get("section_title", "")),
            str(record.get("snippet", "")),
            str(record.get("content", "")),
        )
        for record in top_rows
    )
    matched_focus_terms = [term for term in focus_terms if term in merged_text]
    missing_focus_terms = [term for term in focus_terms if term not in matched_focus_terms]
    top_score = max(float(record.get("score", 0.0)) for record in top_rows)
    level = "high"
    if missing_focus_terms or top_score < 0.35:
        level = "medium" if matched_focus_terms else "low"
    return {
        "level": level,
        "matched_focus_terms": matched_focus_terms,
        "missing_focus_terms": missing_focus_terms,
    }


def _generic_penalty(record: dict) -> float:
    section_title = str(record.get("section_title", "")).strip()
    title = str(record.get("title", "")).strip()
    snippet = str(record.get("snippet", ""))
    penalty = 1.0
    if section_title and title and section_title == title:
        penalty *= 0.7
    if "目录" in snippet[:60]:
        penalty *= 0.65
    return penalty


def _lexical_rerank(record: dict, query: str, query_terms: list[str], base_score: float) -> float:
    title_text = _normalized_text(record.get("section_title", ""), record.get("title", ""))
    content_text = _normalized_text(record.get("content", ""))
    merged_text = f"{title_text} {content_text}".strip()
    title_overlap = _coverage_score(title_text, query_terms)
    content_overlap = _coverage_score(content_text, query_terms)
    focus_terms = _extract_focus_terms(query)
    focus_score = 0.0
    if focus_terms:
        focus_hits = sum(1 for term in focus_terms if term in merged_text)
        focus_score = focus_hits / len(focus_terms)
    phrase_bonus = max(
        _exact_phrase_bonus(query, title_text),
        _exact_phrase_bonus(query, content_text),
    )
    score = (
        0.45 * base_score
        + 0.20 * title_overlap
        + 0.15 * content_overlap
        + 0.15 * focus_score
        + 0.05 * phrase_bonus
    )
    if focus_terms and focus_score <= 0:
        score *= 0.55
    return score * _generic_penalty(record)


def _passes_relevance_gate(record: dict, query: str, query_terms: list[str], query_mode: str) -> bool:
    unique_terms = set(_filter_query_terms(query_terms))
    if not unique_terms:
        return False
    merged_text = _normalized_text(
        str(record.get("section_title", "")),
        str(record.get("title", "")),
        str(record.get("snippet", "")),
        str(record.get("content", "")),
    )
    if not merged_text:
        return False
    if _exact_phrase_bonus(query, merged_text) > 0:
        return True

    matched_terms = _matched_query_terms(merged_text, query_terms)
    match_count = len(matched_terms)
    coverage = match_count / max(len(unique_terms), 1)
    min_match_count = 1 if len(unique_terms) <= 2 else 2
    min_coverage = MIN_RELEVANCE_COVERAGE.get(query_mode, MIN_RELEVANCE_COVERAGE["general"])

    if match_count < min_match_count:
        return False
    return coverage >= min_coverage


@lru_cache(maxsize=1)
def _load_semantic_index() -> dict:
    semantic_index_path = _semantic_index_path()
    if not semantic_index_path.exists():
        return {"idf_map": {}, "vectors": []}
    return json.loads(semantic_index_path.read_text(encoding="utf-8"))


def keyword_search(query: str, limit: int = 10, filters: dict[str, str] | None = None) -> list[dict]:
    db_path = _db_path()
    if not db_path.exists():
        return []
    fts_query = _build_fts_query(query)
    if not fts_query:
        return []
    query_terms = tokenize(query, drop_stopwords=True)
    filter_sql, filter_params = _build_filters(filters)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"""
        SELECT
            chunks.*,
            bm25(fts_chunks) AS bm25_score
        FROM fts_chunks
        JOIN chunks ON chunks.chunk_id = fts_chunks.chunk_id
        WHERE fts_chunks MATCH ?
        {filter_sql}
        ORDER BY bm25_score ASC
        LIMIT ?
        """,
        [fts_query, *filter_params, limit],
    ).fetchall()
    conn.close()

    normalized_scores = _normalize_bm25_scores(rows)
    results = []
    for row in rows:
        result = dict(row)
        result["snippet"] = _make_snippet(result["content"], query)
        raw_bm25 = float(result.pop("bm25_score", 0.0))
        base_score = normalized_scores.get(result["chunk_id"], _normalize_rank(raw_bm25))
        result["keyword_score"] = _lexical_rerank(result, query, query_terms, base_score)
        results.append(result)
    results.sort(key=lambda item: item["keyword_score"], reverse=True)
    return results


def semantic_search(query: str, limit: int = 10, filters: dict[str, str] | None = None) -> list[dict]:
    db_path = _db_path()
    if not db_path.exists():
        return []
    semantic_index = _load_semantic_index()
    idf_map = semantic_index.get("idf_map", {})
    query_terms = tokenize(query, drop_stopwords=True)
    query_counts = token_counts(query, drop_stopwords=True)
    query_weights, query_norm = compute_tfidf_weights(query_counts, idf_map)
    if not query_weights or query_norm <= 0:
        return []

    filter_sql, filter_params = _build_filters(filters)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"""
        SELECT chunk_id, doc_id, title, content, system_name, source_type, source_group, source_file,
               section_id, section_title, date_start, date_end, tags_json, tag_text, page_hint
        FROM chunks
        WHERE 1 = 1
        {filter_sql}
        """,
        filter_params,
    ).fetchall()
    conn.close()
    allowed = {row["chunk_id"]: dict(row) for row in rows}

    scored: list[tuple[float, dict]] = []
    vector_map = {item["chunk_id"]: item for item in semantic_index.get("vectors", [])}
    for chunk_id, row in allowed.items():
        vector = vector_map.get(chunk_id)
        if not vector:
            continue
        norm = float(vector.get("norm", 0.0))
        if norm <= 0:
            continue
        dot = 0.0
        weights = vector.get("weights", {})
        for token, query_weight in query_weights.items():
            dot += query_weight * float(weights.get(token, 0.0))
        if dot <= 0:
            continue
        score = dot / (query_norm * norm)
        row["snippet"] = _make_snippet(row["content"], query)
        row["semantic_score"] = _lexical_rerank(row, query, query_terms, score)
        scored.append((score, row))

    scored.sort(key=lambda item: item[1]["semantic_score"], reverse=True)
    return [row for _, row in scored[:limit]]


def build_answer_preview(query: str, results: list[dict], max_items: int = 3) -> str:
    if not results:
        return "未找到直接命中的手册内容，请尝试换一种问法，或缩短为关键术语后重试。"
    confidence = assess_result_confidence(query, results)
    candidates = sorted(
        results[: max(max_items * 3, 6)],
        key=lambda record: float(record.get("score", 0.0)) + 0.2 * _actionability_score(record),
        reverse=True,
    )
    lines = []
    if confidence["missing_focus_terms"]:
        missing = "、".join(confidence["missing_focus_terms"])
        lines.append(f"当前结果未直接覆盖“{missing}”对应章节，以下返回最接近的可参考内容。")
    for record in candidates[:max_items]:
        section = str(record.get("section_title", "")).strip() or "未命名章节"
        source = Path(str(record.get("source_file", ""))).name or "未知来源"
        snippet = str(record.get("snippet", "")).strip()
        lines.append(f"优先参考《{source}》中的“{section}”：{snippet}")
    return "\n".join(lines)


def hybrid_search(
    query: str,
    limit: int = 10,
    filters: dict[str, str] | None = None,
    keyword_weight: float = 0.6,
    semantic_weight: float = 0.4,
) -> list[dict]:
    query_mode = detect_query_mode(query)
    candidate_limit = max(limit * 10, 80)
    keyword_results = keyword_search(query, limit=candidate_limit, filters=filters)
    semantic_results = semantic_search(query, limit=candidate_limit, filters=filters)

    merged: dict[str, dict] = {}
    for result in keyword_results:
        merged[result["chunk_id"]] = {
            **result,
            "keyword_score": float(result.get("keyword_score", 0.0)),
            "semantic_score": 0.0,
        }
    for result in semantic_results:
        existing = merged.get(result["chunk_id"])
        if existing is None:
            merged[result["chunk_id"]] = {
                **result,
                "keyword_score": 0.0,
                "semantic_score": float(result.get("semantic_score", 0.0)),
            }
            continue
        existing["semantic_score"] = float(result.get("semantic_score", 0.0))
        if len(result.get("snippet", "")) > len(existing.get("snippet", "")):
            existing["snippet"] = result["snippet"]

    ranked = []
    query_terms = tokenize(query, drop_stopwords=True)
    for record in merged.values():
        combined = keyword_weight * float(record.get("keyword_score", 0.0)) + semantic_weight * float(
            record.get("semantic_score", 0.0)
        )
        combined *= source_type_weight(str(record.get("source_type", "")), query_mode)
        record["query_mode"] = query_mode
        record["score"] = combined
        if not _passes_relevance_gate(record, query, query_terms, query_mode):
            continue
        ranked.append(record)

    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:limit]
