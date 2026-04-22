from __future__ import annotations

import re
from pathlib import Path
import json

from app.retrieval.service import assess_result_confidence, detect_query_mode
from app.retrieval.tokenizer import tokenize
from app.services.llm_rewriter import rewrite_answer_with_llm

ACTION_KEYWORDS = {
    "检查",
    "点击",
    "重启",
    "处理",
    "恢复",
    "切换",
    "登录",
    "勾选",
    "插拔",
    "上传",
    "开启",
    "关闭",
    "巡检",
    "排查",
    "修复",
}
ANSWER_SOURCE_BONUS = {
    "procedure": {
        "internal_manual": 0.12,
        "emergency_plan": 0.1,
        "official_manual": 0.02,
        "monthly_report": 0.04,
        "weekly_report": 0.03,
    },
    "incident": {
        "weekly_report": 0.12,
        "monthly_report": 0.1,
        "emergency_plan": 0.06,
        "internal_manual": 0.05,
        "official_manual": 0.02,
    },
    "general": {
        "internal_manual": 0.08,
        "emergency_plan": 0.07,
        "official_manual": 0.04,
        "monthly_report": 0.05,
        "weekly_report": 0.05,
    },
}


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _clean_answer_line(text: str) -> str:
    cleaned = _normalize_text(text)
    cleaned = re.sub(r"^[（(]?\d+[）)\.\、:\-\s]*", "", cleaned)
    cleaned = re.sub(r"^(步骤|处理办法|处理建议|建议处理|注意事项|注意|说明)[:：]\s*", "", cleaned)
    return cleaned.strip("；;，, ")


def _split_content_lines(record: dict) -> list[str]:
    raw_content = str(record.get("content", "")).strip()
    if not raw_content:
        snippet = str(record.get("snippet", "")).strip()
        return [snippet] if snippet else []

    lines: list[str] = []
    for block in raw_content.splitlines():
        block = _normalize_text(block)
        if not block:
            continue
        parts = [part.strip() for part in re.split(r"\s*\|\s*", block) if part.strip()]
        lines.extend(parts)
    return lines or [str(record.get("snippet", "")).strip()]


def _line_query_overlap(line: str, query_terms: list[str]) -> float:
    if not query_terms:
        return 0.0
    line_tokens = set(tokenize(line))
    hits = sum(1 for term in set(query_terms) if term in line_tokens or term in line)
    return hits / max(len(set(query_terms)), 1)


def _action_strength(line: str) -> float:
    compact = line.lower()
    hits = sum(1 for keyword in ACTION_KEYWORDS if keyword in compact)
    return hits / len(ACTION_KEYWORDS)


def _is_low_value_line(line: str) -> bool:
    compact = _clean_answer_line(line)
    if not compact or compact in {"无", "正常", "一切正常"}:
        return True
    if compact in {"工作周报", "月报", "运维报告"}:
        return True
    if "概述" in compact or "项目采购 操作手册" in compact:
        return True
    if compact.startswith("目的：") or compact.startswith("图 ") or re.match(r"^图\s*\d+", compact):
        return True
    return len(compact) < 6


def _derive_evidence_candidates(query: str, results: list[dict], query_mode: str, max_candidates: int = 8) -> list[dict]:
    query_terms = tokenize(query, drop_stopwords=True)
    candidates: list[dict] = []
    for record in results[:max_candidates]:
        record_score = float(record.get("score", 0.0))
        for line in _split_content_lines(record):
            if _is_low_value_line(line):
                continue
            overlap = _line_query_overlap(line, query_terms)
            action = _action_strength(line)
            if query_mode == "procedure" and action < 0.05 and overlap < 0.25:
                continue
            source_bonus = ANSWER_SOURCE_BONUS.get(query_mode, ANSWER_SOURCE_BONUS["general"]).get(
                str(record.get("source_type", "")),
                0.0,
            )
            line_score = 0.55 * record_score + 0.30 * overlap + 0.15 * action + source_bonus
            candidates.append(
                {
                    "chunk_id": str(record.get("chunk_id", "")),
                    "section_id": str(record.get("section_id", "")),
                    "section_title": str(record.get("section_title", "")).strip() or "未命名章节",
                    "source_file": str(record.get("source_file", "")),
                    "source_type": str(record.get("source_type", "")),
                    "source_group": str(record.get("source_group", "")),
                    "system_name": str(record.get("system_name", "")),
                    "page_hint": str(record.get("page_hint", "") or ""),
                    "date_start": str(record.get("date_start", "") or ""),
                    "date_end": str(record.get("date_end", "") or ""),
                    "tags_json": str(record.get("tags_json", "") or "[]"),
                    "text": _normalize_text(line),
                    "overlap": overlap,
                    "action": action,
                    "source_bonus": source_bonus,
                    "score": line_score,
                }
            )
    candidates.sort(key=lambda item: item["score"], reverse=True)
    return candidates


def _build_citation(chunk: dict, citation_id: str) -> dict[str, str]:
    try:
        tags = json.loads(str(chunk.get("tags_json", "[]") or "[]"))
    except json.JSONDecodeError:
        tags = []
    return {
        "id": citation_id,
        "doc_id": str(chunk.get("doc_id", "")),
        "chunk_id": chunk["chunk_id"],
        "section_id": chunk["section_id"],
        "section_title": chunk["section_title"],
        "source_name": Path(chunk["source_file"]).name or "未知来源",
        "source_file": chunk["source_file"],
        "source_type": chunk["source_type"],
        "source_group": str(chunk.get("source_group", "") or ""),
        "system_name": chunk["system_name"],
        "page_hint": chunk["page_hint"],
        "date_start": str(chunk.get("date_start", "") or ""),
        "date_end": str(chunk.get("date_end", "") or ""),
        "tags": [str(tag) for tag in tags if str(tag).strip()],
    }


def _build_direct_answer(query: str, query_mode: str, steps: list[dict[str, object]], citations: list[dict[str, str]]) -> str:
    if not steps:
        return "当前资料没有形成可直接执行的处理结论，建议先查看原始手册。"
    primary_step = _clean_answer_line(str(steps[0]["text"]))
    primary_source = citations[0] if citations else {}
    source_name = str(primary_source.get("source_name", "") or "当前资料")
    section_title = str(primary_source.get("section_title", "") or "相关章节")
    if query_mode == "incident":
        return (
            f"针对“{query}”，优先结合《{source_name}》中的“{section_title}”处理。"
            f"当前建议先{primary_step.rstrip('。')}"
            "，再根据现场现象继续排查。"
        )
    if query_mode == "procedure":
        return (
            f"针对“{query}”，当前资料已经给出可直接执行的处理路径。"
            f"优先参考《{source_name}》中的“{section_title}”，先{primary_step.rstrip('。')}。"
        )
    return (
        f"针对“{query}”，知识库已定位到最相关资料。"
        f"优先参考《{source_name}》中的“{section_title}”，核心处理动作是：{primary_step.rstrip('。')}。"
    )


def _build_follow_up_tips(
    query_mode: str,
    confidence: dict[str, object],
    results: list[dict],
) -> list[str]:
    tips: list[str] = []
    case_rows = [row for row in results if str(row.get("source_group", "")) == "case"]
    manual_rows = [row for row in results if str(row.get("source_group", "")) == "manual"]
    if query_mode == "procedure" and case_rows:
        case_title = str(case_rows[0].get("section_title", "")).strip() or "相关案例"
        tips.append(f"如果按手册处理后仍未恢复，可继续参考历史案例“{case_title}”核对现场差异。")
    if query_mode == "incident" and manual_rows:
        manual_title = str(manual_rows[0].get("section_title", "")).strip() or "相关手册章节"
        tips.append(f"如果现场现象与历史案例不完全一致，建议回到手册章节“{manual_title}”逐项核对。")
    missing_terms = confidence.get("missing_focus_terms", [])
    if isinstance(missing_terms, list) and missing_terms:
        missing = "、".join(str(item) for item in missing_terms if str(item).strip())
        tips.append(f"当前结果未完整覆盖“{missing}”相关章节，执行前建议打开原始手册再次确认。")
    return tips


def generate_cited_answer(
    query: str,
    results: list[dict],
    max_steps: int = 3,
    use_model_rewrite: bool | None = None,
) -> dict[str, object]:
    confidence = assess_result_confidence(query, results)
    query_mode = detect_query_mode(query)
    if not results:
        text = "当前知识库没有检索到可直接引用的内容，建议改用更短的关键词重新检索。"
        return {
            "query_mode": query_mode,
            "confidence": confidence,
            "rule_text": text,
            "text": text,
            "steps": [],
            "citations": [],
            "rewrite": {
                "enabled": False,
                "applied": False,
                "provider": "openai_compatible",
                "model": "",
                "endpoint": "",
                "error": "当前没有可用于重写的检索结果",
            },
        }

    evidence_pool = _derive_evidence_candidates(query, results, query_mode=query_mode)
    selected: list[dict] = []
    seen_texts: set[str] = set()
    for candidate in evidence_pool:
        dedupe_key = re.sub(r"\s+", "", candidate["text"].lower())
        if dedupe_key in seen_texts:
            continue
        selected.append(candidate)
        seen_texts.add(dedupe_key)
        if len(selected) >= max_steps:
            break

    citation_map: dict[str, str] = {}
    citations: list[dict[str, str]] = []
    steps: list[dict[str, object]] = []
    for item in selected:
        if item["chunk_id"] not in citation_map:
            citation_id = f"[{len(citations) + 1}]"
            citation_map[item["chunk_id"]] = citation_id
            citations.append(_build_citation(item, citation_id))
        else:
            citation_id = citation_map[item["chunk_id"]]

        cleaned_text = _clean_answer_line(item["text"])
        steps.append(
            {
                "text": cleaned_text,
                "citation_ids": [citation_id],
                "section_title": item["section_title"],
            }
        )

    direct_answer = _build_direct_answer(query, query_mode, steps, citations)
    follow_up_tips = _build_follow_up_tips(query_mode, confidence, results)

    intro = "结论："
    lines = [intro, direct_answer]
    if steps:
        lines.extend(["", "建议步骤："])
    for index, step in enumerate(steps, start=1):
        citation_suffix = " ".join(step["citation_ids"])
        lines.append(f"{index}. {step['text']} {citation_suffix}".strip())
    if follow_up_tips:
        lines.extend(["", "补充建议："])
        for item in follow_up_tips:
            lines.append(f"- {item}")
    if citations:
        lines.extend(["", "参考来源："])
        for citation in citations:
            lines.append(f"{citation['id']} {citation['source_name']} / {citation['section_title']}")

    rule_text = "\n".join(lines)
    rewrite_result = rewrite_answer_with_llm(
        query=query,
        query_mode=query_mode,
        draft_text=rule_text,
        confidence=confidence,
        steps=steps,
        citations=citations,
        force=use_model_rewrite,
    )
    text = str(rewrite_result["text"]).strip() or rule_text
    return {
        "query_mode": query_mode,
        "confidence": confidence,
        "direct_answer": direct_answer,
        "rule_text": rule_text,
        "text": text,
        "steps": steps,
        "follow_up_tips": follow_up_tips,
        "citations": citations,
        "rewrite": rewrite_result["metadata"],
    }
