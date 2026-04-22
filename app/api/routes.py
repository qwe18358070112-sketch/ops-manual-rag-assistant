from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import get_settings
from app.retrieval.service import hybrid_search
from app.services.answering import generate_cited_answer
from app.services.case_views import build_case_timeline, build_topic_detail, build_topic_view, get_case_detail, load_chunk_records
from app.services.manual_views import get_manual_detail

router = APIRouter()


def read_json(path: Path) -> object:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def build_filter_options() -> dict[str, list[str]]:
    settings = get_settings()
    manifest = read_json(settings.manifest_path)
    chunk_records = load_chunk_records()
    if not isinstance(manifest, list):
        return {
            "system_names": [],
            "source_types": [],
            "source_groups": [],
            "tags": [],
        }
    system_names = sorted(
        {
            str(item.get("system_name", "")).strip()
            for item in [*manifest, *chunk_records]
            if str(item.get("system_name", "")).strip()
        }
    )
    source_types = sorted(
        {
            str(item.get("source_type", "")).strip()
            for item in [*manifest, *chunk_records]
            if str(item.get("source_type", "")).strip()
        }
    )
    source_groups = sorted(
        {
            str(item.get("source_group", "")).strip()
            for item in [*manifest, *chunk_records]
            if str(item.get("source_group", "")).strip()
        }
    )
    tags = sorted(
        {
            str(tag).strip()
            for item in chunk_records
            for tag in item.get("tags", [])
            if str(tag).strip()
        }
    )
    return {
        "system_names": system_names,
        "source_types": source_types,
        "source_groups": source_groups,
        "tags": tags,
    }


def format_date_range(date_start: str | None, date_end: str | None) -> str:
    values = [value for value in [str(date_start or "").strip(), str(date_end or "").strip()] if value]
    return " ~ ".join(values) if values else "-"


def derive_case_search_query(entry: dict) -> str:
    section_title = str(entry.get("section_title", "")).strip()
    summary = str(entry.get("summary", "")).strip()
    if section_title and section_title not in {"工作周报", "周报", "月报", "运维报告"} and not section_title.isdigit():
        return section_title
    if summary:
        return summary[:40]
    return str(entry.get("title", "")).strip() or "历史案例"


def build_case_search_url(entry: dict) -> str:
    specific_tags = [
        str(tag).strip()
        for tag in entry.get("tags", [])
        if str(tag).strip() and str(tag).strip() not in {"案例库", "周报", "月报", "历史案例", "政企驻场运维"}
    ]
    params = {
        "q": derive_case_search_query(entry),
        "search_mode": "case_search",
        "system_name": str(entry.get("system_name", "") or ""),
        "source_type": str(entry.get("source_type", "") or ""),
        "tag": specific_tags[0] if specific_tags else "",
        "date_from": str(entry.get("date_start", "") or ""),
        "date_to": str(entry.get("date_end", "") or ""),
    }
    return "/search-page?" + urlencode({key: value for key, value in params.items() if value})


def build_case_detail_url(section_id: str) -> str:
    return "/case-detail-page?" + urlencode({"section_id": section_id})


def build_topic_detail_url(
    tag: str,
    *,
    system_name: str | None = None,
    source_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int | None = None,
) -> str:
    params = {
        "tag": tag,
        "system_name": system_name or "",
        "source_type": source_type or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
        "limit": str(limit or 100),
    }
    return "/topic-detail-page?" + urlencode({key: value for key, value in params.items() if value})


def build_topic_search_url(
    tag: str,
    *,
    system_name: str | None = None,
    source_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    params = {
        "q": f"{tag}怎么处理",
        "search_mode": "case_search",
        "tag": tag,
        "system_name": system_name or "",
        "source_type": source_type or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
    }
    return "/search-page?" + urlencode({key: value for key, value in params.items() if value})


def build_manual_detail_url(section_id: str) -> str:
    return "/manual-detail-page?" + urlencode({"section_id": section_id})


def build_manual_original_url(section_id: str) -> str:
    return "/app/manuals/" + quote(section_id) + "/original"


def enrich_answer_citations(answer: dict[str, object]) -> dict[str, object]:
    citations = answer.get("citations", [])
    if not isinstance(citations, list):
        return answer
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        source_type = str(citation.get("source_type", "") or "")
        source_group = str(citation.get("source_group", "") or "")
        section_id = str(citation.get("section_id", "") or "")
        if source_type in {"weekly_report", "monthly_report"} and section_id:
            citation["detail_url"] = build_case_detail_url(section_id)
        elif source_group == "manual" and section_id:
            citation["detail_url"] = build_manual_detail_url(section_id)
            citation["original_url"] = build_manual_original_url(section_id)
    return answer


def _render_select_options(options: list[str], selected: str | None, placeholder: str) -> str:
    rows = [f'<option value="">{html.escape(placeholder)}</option>']
    for option in options:
        is_selected = " selected" if selected == option else ""
        rows.append(f'<option value="{html.escape(option)}"{is_selected}>{html.escape(option)}</option>')
    return "".join(rows)


def render_search_form(
    *,
    query: str = "",
    rewrite: bool = False,
    search_mode: str = "all",
    system_name: str | None = None,
    source_type: str | None = None,
    tag: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    filter_options = build_filter_options()
    mode_options = [
        ("all", "全部"),
        ("manual_qa", "手册问答"),
        ("case_search", "历史案例检索"),
    ]
    mode_html = "".join(
        f'<option value="{value}"{" selected" if search_mode == value else ""}>{label}</option>'
        for value, label in mode_options
    )
    rewrite_checked = "checked" if rewrite else ""
    return f"""
    <form action="/search-page" method="get" style="margin: 16px 0 24px;">
      <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 12px;">
        <input type="text" name="q" value="{html.escape(query)}" placeholder="例如：视频平台资源共享怎么操作" style="width: 520px; padding: 10px 12px;" />
        <select name="search_mode" style="padding: 10px 12px;">{mode_html}</select>
        <label style="font-size: 14px;">
          <input type="checkbox" name="rewrite" value="true" {rewrite_checked} />
          启用模型重写
        </label>
        <button type="submit" style="padding: 10px 16px;">搜索</button>
      </div>
      <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap;">
        <select name="system_name" style="padding: 8px 10px; min-width: 160px;">{_render_select_options(filter_options["system_names"], system_name, "全部系统")}</select>
        <select name="source_type" style="padding: 8px 10px; min-width: 180px;">{_render_select_options(filter_options["source_types"], source_type, "全部来源类型")}</select>
        <select name="tag" style="padding: 8px 10px; min-width: 180px;">{_render_select_options(filter_options["tags"], tag, "全部案例标签")}</select>
        <label style="font-size: 14px;">开始日期 <input type="date" name="date_from" value="{html.escape(date_from or '')}" style="padding: 8px 10px;" /></label>
        <label style="font-size: 14px;">结束日期 <input type="date" name="date_to" value="{html.escape(date_to or '')}" style="padding: 8px 10px;" /></label>
      </div>
    </form>
    """


def render_case_filters_form(
    *,
    action: str,
    system_name: str | None = None,
    source_type: str | None = None,
    tag: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int | None = None,
) -> str:
    filter_options = build_filter_options()
    limit_value = html.escape(str(limit or 40))
    return f"""
    <form action="{html.escape(action)}" method="get" style="margin: 16px 0 24px;">
      <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap;">
        <select name="system_name" style="padding: 8px 10px; min-width: 160px;">{_render_select_options(filter_options["system_names"], system_name, "全部系统")}</select>
        <select name="source_type" style="padding: 8px 10px; min-width: 180px;">{_render_select_options(filter_options["source_types"], source_type, "全部来源类型")}</select>
        <select name="tag" style="padding: 8px 10px; min-width: 180px;">{_render_select_options(filter_options["tags"], tag, "全部专题标签")}</select>
        <label style="font-size: 14px;">开始日期 <input type="date" name="date_from" value="{html.escape(date_from or '')}" style="padding: 8px 10px;" /></label>
        <label style="font-size: 14px;">结束日期 <input type="date" name="date_to" value="{html.escape(date_to or '')}" style="padding: 8px 10px;" /></label>
        <label style="font-size: 14px;">数量 <input type="number" min="1" max="100" name="limit" value="{limit_value}" style="padding: 8px 10px; width: 90px;" /></label>
        <button type="submit" style="padding: 10px 16px;">查看</button>
      </div>
    </form>
    """


@router.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse('/app', status_code=307)


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/catalog")
def catalog() -> dict[str, object]:
    settings = get_settings()
    return {
        "manifest": read_json(settings.manifest_path),
        "summary": read_json(settings.indexes_dir / "ingestion_summary.json"),
        "retrieval": read_json(settings.indexes_dir / "retrieval_summary.json"),
        "filter_options": build_filter_options(),
        "case_timeline_preview": build_case_timeline(limit=5),
        "topic_view_preview": build_topic_view(limit_topics=5),
    }


@router.get("/search")
def search(
    q: str = Query(..., min_length=1, description="自然语言问题或关键词"),
    limit: int = Query(8, ge=1, le=20),
    search_mode: str = Query(default="all", description="all/manual_qa/case_search"),
    system_name: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    date_from: str | None = Query(default=None, description="YYYY-MM-DD"),
    date_to: str | None = Query(default=None, description="YYYY-MM-DD"),
    rewrite: bool | None = Query(default=None, description="是否启用可选的大模型重写层"),
) -> dict[str, object]:
    filters = {
        key: value
        for key, value in {
            "search_mode": search_mode or "all",
            "system_name": system_name or "",
            "source_type": source_type or "",
            "tag": tag or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
        }.items()
        if value
    }
    results = hybrid_search(q, limit=limit, filters=filters or None)
    answer = generate_cited_answer(q, results, use_model_rewrite=rewrite)
    answer = enrich_answer_citations(answer)
    return {
        "query": q,
        "limit": limit,
        "search_mode": search_mode,
        "filters": filters,
        "rewrite_requested": rewrite,
        "count": len(results),
        "confidence": answer["confidence"],
        "answer_preview": answer["text"],
        "answer": answer,
        "results": results,
    }


@router.get("/search-page", response_class=HTMLResponse)
def search_page(
    q: str = Query(..., min_length=1, description="自然语言问题或关键词"),
    limit: int = Query(8, ge=1, le=20),
    search_mode: str = Query(default="all"),
    system_name: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    rewrite: bool | None = Query(default=None),
) -> str:
    filters = {
        key: value
        for key, value in {
            "search_mode": search_mode or "all",
            "system_name": system_name or "",
            "source_type": source_type or "",
            "tag": tag or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
        }.items()
        if value
    }
    results = hybrid_search(q, limit=limit, filters=filters or None)
    answer = generate_cited_answer(q, results, use_model_rewrite=rewrite)
    answer = enrich_answer_citations(answer)
    confidence = answer["confidence"]
    answer_preview = answer["text"]

    items = []
    for index, result in enumerate(results, start=1):
        source_name = html.escape(Path(str(result.get("source_file", ""))).name or "未知来源")
        section_title = html.escape(str(result.get("section_title", "未命名章节")))
        system_name_text = html.escape(str(result.get("system_name", "")))
        source_type_text = html.escape(str(result.get("source_type", "")))
        source_group_text = html.escape(str(result.get("source_group", "")))
        snippet_text = html.escape(str(result.get("snippet", "")))
        section_id_text = html.escape(str(result.get("section_id", "")))
        page_hint_text = html.escape(str(result.get("page_hint", "") or "-"))
        date_range_text = html.escape(
            format_date_range(result.get("date_start"), result.get("date_end"))
        )
        tags_json = str(result.get("tags_json", "") or "[]")
        try:
            tags = json.loads(tags_json)
        except json.JSONDecodeError:
            tags = []
        tags_text = html.escape("、".join(str(tag) for tag in tags if str(tag).strip()) or "-")
        items.append(
            f"""
            <li style="margin-bottom: 20px; padding: 16px; border: 1px solid #d0d7de; border-radius: 12px;">
              <div style="font-size: 14px; color: #57606a;">结果 {index} · score={float(result.get("score", 0.0)):.3f}</div>
              <div style="font-size: 18px; font-weight: 600; margin: 6px 0;">{section_title}</div>
              <div style="font-size: 13px; color: #57606a; margin-bottom: 10px;">{source_name} ｜ {system_name_text} ｜ {source_type_text} ｜ {source_group_text}</div>
              <div style="font-size: 15px; margin-bottom: 8px;">{snippet_text}</div>
              <div style="font-size: 12px; color: #57606a;">section_id={section_id_text} · page_hint={page_hint_text} · date={date_range_text} · tags={tags_text}</div>
            </li>
            """
        )

    escaped_query = html.escape(q)
    escaped_answer_preview = html.escape(answer_preview)
    rewrite_meta = answer.get("rewrite", {})
    citation_items = []
    for citation in answer["citations"]:
        detail_url = str(citation.get("detail_url", "") or "").strip()
        target_html = (
            f'<a href="{html.escape(detail_url)}">{html.escape(citation["section_title"])}</a>'
            if detail_url
            else html.escape(citation["section_title"])
        )
        citation_items.append(
            f"<li><strong>{html.escape(citation['id'])}</strong> "
            f"{html.escape(citation['source_name'])} ｜ "
            f"{target_html} ｜ "
            f"{html.escape(citation.get('source_type', ''))} ｜ "
            f"page_hint={html.escape(citation.get('page_hint', '') or '-')}</li>"
        )
    return f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>检索结果 - 政企运维知识库问答助手</title>
      </head>
      <body style="font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; max-width: 1080px; margin: 32px auto; line-height: 1.6;">
        <h1>检索结果</h1>
        {render_search_form(query=q, rewrite=(rewrite is True or bool(rewrite_meta.get("enabled"))), search_mode=search_mode, system_name=system_name, source_type=source_type, tag=tag, date_from=date_from, date_to=date_to)}
        <div style="padding: 16px; background: #f6f8fa; border-radius: 12px; margin-bottom: 24px;">
          <div style="font-size: 14px; color: #57606a;">问题</div>
          <div style="font-size: 18px; font-weight: 600; margin-bottom: 10px;">{escaped_query}</div>
          <div style="font-size: 14px; color: #57606a;">摘要建议</div>
          <pre style="white-space: pre-wrap; font-family: inherit; margin: 8px 0 0;">{escaped_answer_preview}</pre>
          <div style="font-size: 13px; color: #57606a; margin-top: 10px;">置信度：{html.escape(str(confidence.get('level', 'unknown')))} ｜ 已覆盖焦点词：{html.escape('、'.join(confidence.get('matched_focus_terms', [])) or '无')} ｜ 未覆盖焦点词：{html.escape('、'.join(confidence.get('missing_focus_terms', [])) or '无')}</div>
          <div style="font-size: 13px; color: #57606a; margin-top: 8px;">模型重写：{html.escape('已应用' if rewrite_meta.get('applied') else '未应用')} ｜ model={html.escape(str(rewrite_meta.get('model', '') or '-'))} ｜ error={html.escape(str(rewrite_meta.get('error', '') or '-'))}</div>
        </div>
        <div style="padding: 16px; background: #fff8e1; border-radius: 12px; margin-bottom: 24px;">
          <div style="font-size: 14px; color: #57606a;">引用来源</div>
          <ul style="margin: 8px 0 0; padding-left: 20px;">
            {''.join(citation_items) if citation_items else '<li>暂无引用</li>'}
          </ul>
        </div>
        <div style="font-size: 14px; color: #57606a; margin-bottom: 12px;">共命中 {len(results)} 条结果</div>
        <ol style="padding-left: 20px;">
          {''.join(items) if items else '<li>暂无结果</li>'}
        </ol>
      </body>
    </html>
    """


@router.get("/case-timeline")
def case_timeline(
    limit: int = Query(40, ge=1, le=100),
    system_name: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    date_from: str | None = Query(default=None, description="YYYY-MM-DD"),
    date_to: str | None = Query(default=None, description="YYYY-MM-DD"),
) -> dict[str, object]:
    filters = {
        key: value
        for key, value in {
            "system_name": system_name or "",
            "source_type": source_type or "",
            "tag": tag or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
        }.items()
        if value
    }
    entries = build_case_timeline(filters=filters or None, limit=limit)
    return {
        "count": len(entries),
        "limit": limit,
        "filters": filters,
        "entries": entries,
    }


@router.get("/case-timeline-page", response_class=HTMLResponse)
def case_timeline_page(
    limit: int = Query(40, ge=1, le=100),
    system_name: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
) -> str:
    filters = {
        key: value
        for key, value in {
            "system_name": system_name or "",
            "source_type": source_type or "",
            "tag": tag or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
        }.items()
        if value
    }
    entries = build_case_timeline(filters=filters or None, limit=limit)
    rows = []
    for index, entry in enumerate(entries, start=1):
        rows.append(
            f"""
            <li style="margin-bottom: 18px; padding: 16px; border: 1px solid #d0d7de; border-radius: 12px;">
              <div style="font-size: 14px; color: #57606a;">#{index} ｜ {html.escape(format_date_range(entry.get('date_start'), entry.get('date_end')))}</div>
              <div style="font-size: 18px; font-weight: 600; margin: 6px 0;">{html.escape(str(entry.get('section_title', '') or '未命名章节'))}</div>
              <div style="font-size: 13px; color: #57606a; margin-bottom: 10px;">{html.escape(Path(str(entry.get('source_file', ''))).name or '未知来源')} ｜ {html.escape(str(entry.get('system_name', '')))} ｜ {html.escape(str(entry.get('source_type', '')))}</div>
              <div style="font-size: 15px; margin-bottom: 10px;">{html.escape(str(entry.get('summary', '')))}</div>
              <div style="font-size: 12px; color: #57606a;">tags={html.escape('、'.join(entry.get('tags', [])) or '-')}</div>
              <div style="display: flex; gap: 16px; margin-top: 12px;">
                <a href="{html.escape(build_case_detail_url(str(entry.get('section_id', ''))))}" style="font-size: 13px;">案例详情</a>
                <a href="{html.escape(build_case_search_url(entry))}" style="font-size: 13px;">联动到问答</a>
              </div>
            </li>
            """
        )
    return f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>历史案例时间轴 - 政企运维知识库问答助手</title>
      </head>
      <body style="font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; max-width: 1080px; margin: 32px auto; line-height: 1.6;">
        <h1>历史案例时间轴</h1>
        <p>按时间顺序查看周报和月报中的故障处理记录，适合复盘某段时间出现过的问题和处理方式。</p>
        {render_case_filters_form(action='/case-timeline-page', system_name=system_name, source_type=source_type, tag=tag, date_from=date_from, date_to=date_to, limit=limit)}
        <div style="font-size: 14px; color: #57606a; margin-bottom: 12px;">共返回 {len(entries)} 条案例</div>
        <ol style="padding-left: 20px;">
          {''.join(rows) if rows else '<li>暂无符合条件的案例</li>'}
        </ol>
      </body>
    </html>
    """


@router.get("/topic-view")
def topic_view(
    limit_topics: int = Query(12, ge=1, le=30),
    system_name: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    date_from: str | None = Query(default=None, description="YYYY-MM-DD"),
    date_to: str | None = Query(default=None, description="YYYY-MM-DD"),
) -> dict[str, object]:
    filters = {
        key: value
        for key, value in {
            "system_name": system_name or "",
            "source_type": source_type or "",
            "tag": tag or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
        }.items()
        if value
    }
    topics = build_topic_view(filters=filters or None, limit_topics=limit_topics)
    return {
        "count": len(topics),
        "limit_topics": limit_topics,
        "filters": filters,
        "topics": topics,
    }


@router.get("/topic-view-page", response_class=HTMLResponse)
def topic_view_page(
    limit: int = Query(12, ge=1, le=30),
    system_name: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
) -> str:
    filters = {
        key: value
        for key, value in {
            "system_name": system_name or "",
            "source_type": source_type or "",
            "tag": tag or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
        }.items()
        if value
    }
    topics = build_topic_view(filters=filters or None, limit_topics=limit)
    cards = []
    for topic in topics:
        samples = []
        for sample in topic.get("samples", []):
            sample_section_id = str(sample.get("section_id", "") or "")
            samples.append(
                f"""
                <li style="margin-bottom: 10px;">
                  <div style="font-size: 14px; font-weight: 600;">{html.escape(str(sample.get('section_title', '')))}</div>
                  <div style="font-size: 12px; color: #57606a;">{html.escape(format_date_range(sample.get('date_start'), sample.get('date_end')))} ｜ {html.escape(Path(str(sample.get('source_file', ''))).name or '未知来源')}</div>
                  <div style="font-size: 13px; color: #24292f;">{html.escape(str(sample.get('summary', '')))}</div>
                  <div style="margin-top: 6px;"><a href="{html.escape(build_case_detail_url(sample_section_id))}" style="font-size: 12px;">查看案例详情</a></div>
                </li>
                """
            )
        topic_name = str(topic.get("tag", ""))
        topic_detail_url = build_topic_detail_url(
            topic_name,
            system_name=system_name,
            source_type=source_type,
            date_from=date_from,
            date_to=date_to,
            limit=100,
        )
        topic_search_url = build_topic_search_url(
            topic_name,
            system_name=system_name,
            source_type=source_type,
            date_from=date_from,
            date_to=date_to,
        )
        cards.append(
            f"""
            <section style="margin-bottom: 20px; padding: 18px; border: 1px solid #d0d7de; border-radius: 12px;">
              <div style="display: flex; justify-content: space-between; gap: 12px; align-items: baseline;">
                <h2 style="margin: 0; font-size: 20px;">{html.escape(topic_name)}</h2>
                <div style="font-size: 13px; color: #57606a;">{html.escape(str(topic.get('count', 0)))} 条 ｜ 最新 {html.escape(str(topic.get('latest_date', '') or '-'))}</div>
              </div>
              <ul style="margin: 14px 0 0; padding-left: 20px;">
                {''.join(samples) if samples else '<li>暂无样例</li>'}
              </ul>
              <div style="display: flex; gap: 16px; margin-top: 12px;">
                <a href="{html.escape(topic_detail_url)}" style="font-size: 13px;">进入专题详情</a>
                <a href="{html.escape(topic_search_url)}" style="font-size: 13px;">联动到问答</a>
              </div>
            </section>
            """
        )
    return f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>案例专题视图 - 政企运维知识库问答助手</title>
      </head>
      <body style="font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; max-width: 1080px; margin: 32px auto; line-height: 1.6;">
        <h1>案例专题视图</h1>
        <p>按 section 级标签聚合历史案例，适合快速查看高频专题，例如漏洞整改、轮询助手、客户端登录和网络故障。</p>
        {render_case_filters_form(action='/topic-view-page', system_name=system_name, source_type=source_type, tag=tag, date_from=date_from, date_to=date_to, limit=limit)}
        <div style="font-size: 14px; color: #57606a; margin-bottom: 12px;">共返回 {len(topics)} 个专题</div>
        {''.join(cards) if cards else '<p>暂无符合条件的专题。</p>'}
      </body>
    </html>
    """


@router.get("/topic-detail")
def topic_detail(
    tag: str = Query(..., min_length=1),
    limit: int = Query(100, ge=1, le=200),
    system_name: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    date_from: str | None = Query(default=None, description="YYYY-MM-DD"),
    date_to: str | None = Query(default=None, description="YYYY-MM-DD"),
) -> dict[str, object]:
    filters = {
        key: value
        for key, value in {
            "system_name": system_name or "",
            "source_type": source_type or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
        }.items()
        if value
    }
    detail = build_topic_detail(tag, filters=filters or None, limit=limit)
    return detail


@router.get("/topic-detail-page", response_class=HTMLResponse)
def topic_detail_page(
    tag: str = Query(..., min_length=1),
    limit: int = Query(100, ge=1, le=200),
    system_name: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
) -> str:
    filters = {
        key: value
        for key, value in {
            "system_name": system_name or "",
            "source_type": source_type or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
        }.items()
        if value
    }
    detail = build_topic_detail(tag, filters=filters or None, limit=limit)
    entries = detail.get("entries", [])
    rows = []
    for index, entry in enumerate(entries, start=1):
        rows.append(
            f"""
            <li style="margin-bottom: 18px; padding: 16px; border: 1px solid #d0d7de; border-radius: 12px;">
              <div style="font-size: 14px; color: #57606a;">#{index} ｜ {html.escape(format_date_range(entry.get('date_start'), entry.get('date_end')))}</div>
              <div style="font-size: 18px; font-weight: 600; margin: 6px 0;">{html.escape(str(entry.get('section_title', '') or '未命名章节'))}</div>
              <div style="font-size: 13px; color: #57606a; margin-bottom: 10px;">{html.escape(Path(str(entry.get('source_file', ''))).name or '未知来源')} ｜ {html.escape(str(entry.get('system_name', '')))} ｜ {html.escape(str(entry.get('source_type', '')))}</div>
              <div style="font-size: 15px; margin-bottom: 10px;">{html.escape(str(entry.get('summary', '')))}</div>
              <div style="font-size: 12px; color: #57606a;">tags={html.escape('、'.join(entry.get('tags', [])) or '-')}</div>
              <div style="display: flex; gap: 16px; margin-top: 12px;">
                <a href="{html.escape(build_case_detail_url(str(entry.get('section_id', ''))))}" style="font-size: 13px;">案例详情</a>
                <a href="{html.escape(build_case_search_url(entry))}" style="font-size: 13px;">联动到问答</a>
              </div>
            </li>
            """
        )
    back_to_topics = "/topic-view-page?" + urlencode(
        {
            key: value
            for key, value in {
                "system_name": system_name or "",
                "source_type": source_type or "",
                "date_from": date_from or "",
                "date_to": date_to or "",
                "limit": "12",
            }.items()
            if value
        }
    )
    return f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>专题详情 - 政企运维知识库问答助手</title>
      </head>
      <body style="font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; max-width: 1080px; margin: 32px auto; line-height: 1.6;">
        <h1>专题详情：{html.escape(tag)}</h1>
        <p>查看该专题下的完整案例集合，并可一键带入历史案例问答。</p>
        <div style="display: flex; gap: 16px; margin-bottom: 16px;">
          <a href="{html.escape(back_to_topics)}">返回专题总览</a>
          <a href="{html.escape(build_topic_search_url(tag, system_name=system_name, source_type=source_type, date_from=date_from, date_to=date_to))}">以该专题进入问答</a>
        </div>
        {render_case_filters_form(action='/topic-detail-page', system_name=system_name, source_type=source_type, tag=tag, date_from=date_from, date_to=date_to, limit=limit)}
        <div style="font-size: 14px; color: #57606a; margin-bottom: 12px;">共返回 {html.escape(str(detail.get('count', 0)))} 条案例 ｜ 最新 {html.escape(str(detail.get('latest_date', '') or '-'))}</div>
        <ol style="padding-left: 20px;">
          {''.join(rows) if rows else '<li>暂无符合条件的案例</li>'}
        </ol>
      </body>
    </html>
    """


@router.get("/manual-detail")
def manual_detail(
    section_id: str = Query(..., min_length=1),
) -> dict[str, object]:
    detail = get_manual_detail(section_id)
    return {
        "found": detail is not None,
        "section_id": section_id,
        "entry": detail,
    }


@router.get("/manual-detail-page", response_class=HTMLResponse)
def manual_detail_page(
    section_id: str = Query(..., min_length=1),
) -> str:
    detail = get_manual_detail(section_id)
    if detail is None:
        return f"""
        <html>
          <head>
            <meta charset="utf-8" />
            <title>手册详情 - 政企运维知识库问答助手</title>
          </head>
          <body style="font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; max-width: 960px; margin: 32px auto; line-height: 1.6;">
            <h1>手册详情</h1>
            <p>未找到 section_id={html.escape(section_id)} 对应的手册章节。</p>
            <p><a href="/">返回首页</a></p>
          </body>
        </html>
        """

    source_name = html.escape(Path(str(detail.get('source_file', ''))).name or '未知来源')
    page_hints = html.escape('、'.join(detail.get('page_hints', [])) or '-')
    return f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>手册详情 - 政企运维知识库问答助手</title>
      </head>
      <body style="font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; max-width: 960px; margin: 32px auto; line-height: 1.7;">
        <h1>手册详情</h1>
        <div style="display: flex; gap: 16px; margin-bottom: 16px;">
          <a href="/">返回首页</a>
          <a href="/search-page?q={html.escape(str(detail.get('section_title', '') or ''))}&search_mode=manual_qa">以该章节进入问答</a>
        </div>
        <section style="padding: 18px; border: 1px solid #d0d7de; border-radius: 12px; background: #fff;">
          <div style="font-size: 13px; color: #57606a; margin-bottom: 8px;">{source_name} ｜ {html.escape(str(detail.get('system_name', '')))} ｜ {html.escape(str(detail.get('source_type', '')))}</div>
          <h2 style="margin: 8px 0 12px; font-size: 24px;">{html.escape(str(detail.get('section_title', '') or '未命名章节'))}</h2>
          <div style="font-size: 13px; color: #57606a; margin-bottom: 16px;">section_id={html.escape(str(detail.get('section_id', '')))} ｜ page_hints={page_hints} ｜ tags={html.escape('、'.join(detail.get('tags', [])) or '-')}</div>
          <pre style="white-space: pre-wrap; font-family: inherit; margin: 0; background: #f6f8fa; padding: 16px; border-radius: 10px;">{html.escape(str(detail.get('content', '')))}</pre>
        </section>
      </body>
    </html>
    """


@router.get("/case-detail")
def case_detail(
    section_id: str = Query(..., min_length=1),
) -> dict[str, object]:
    detail = get_case_detail(section_id)
    return {
        "found": detail is not None,
        "section_id": section_id,
        "entry": detail,
    }


@router.get("/case-detail-page", response_class=HTMLResponse)
def case_detail_page(
    section_id: str = Query(..., min_length=1),
) -> str:
    detail = get_case_detail(section_id)
    if detail is None:
        return f"""
        <html>
          <head>
            <meta charset="utf-8" />
            <title>案例详情 - 政企运维知识库问答助手</title>
          </head>
          <body style="font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; max-width: 960px; margin: 32px auto; line-height: 1.6;">
            <h1>案例详情</h1>
            <p>未找到 section_id={html.escape(section_id)} 对应的案例。</p>
            <p><a href="/case-timeline-page">返回历史案例时间轴</a></p>
          </body>
        </html>
        """

    return f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>案例详情 - 政企运维知识库问答助手</title>
      </head>
      <body style="font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; max-width: 960px; margin: 32px auto; line-height: 1.7;">
        <h1>案例详情</h1>
        <div style="display: flex; gap: 16px; margin-bottom: 16px;">
          <a href="/case-timeline-page">返回历史案例时间轴</a>
          <a href="{html.escape(build_case_search_url(detail))}">联动到问答</a>
        </div>
        <section style="padding: 18px; border: 1px solid #d0d7de; border-radius: 12px; background: #fff;">
          <div style="font-size: 14px; color: #57606a;">{html.escape(format_date_range(detail.get('date_start'), detail.get('date_end')))}</div>
          <h2 style="margin: 8px 0 12px; font-size: 24px;">{html.escape(str(detail.get('section_title', '') or '未命名章节'))}</h2>
          <div style="font-size: 13px; color: #57606a; margin-bottom: 12px;">{html.escape(Path(str(detail.get('source_file', ''))).name or '未知来源')} ｜ {html.escape(str(detail.get('system_name', '')))} ｜ {html.escape(str(detail.get('source_type', '')))}</div>
          <div style="font-size: 13px; color: #57606a; margin-bottom: 16px;">section_id={html.escape(str(detail.get('section_id', '')))} ｜ tags={html.escape('、'.join(detail.get('tags', [])) or '-')}</div>
          <pre style="white-space: pre-wrap; font-family: inherit; margin: 0; background: #f6f8fa; padding: 16px; border-radius: 10px;">{html.escape(str(detail.get('content', '')))}</pre>
        </section>
      </body>
    </html>
    """
