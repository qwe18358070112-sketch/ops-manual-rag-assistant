from __future__ import annotations

import html
import json
import tempfile
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.auth import clear_admin_session, credentials_valid, ensure_admin_api, ensure_admin_page, is_authenticated, issue_admin_session
from app.core.config import get_settings
from app.core.logging import load_recent_access_logs
from app.retrieval.service import hybrid_search
from app.services.answering import generate_cited_answer
from app.services.case_views import build_case_timeline, build_topic_detail, build_topic_view, get_case_detail, load_chunk_records
from app.services.library_bundle import export_bundle, import_bundle
from app.services.library import (
    build_library_analysis,
    export_analysis,
    export_documents,
    get_document,
    list_documents,
    rebuild_all_documents,
    reindex_document,
    save_uploaded_document,
)
from app.services.manual_views import get_manual_detail

router = APIRouter(include_in_schema=False)
settings = get_settings()
templates = Jinja2Templates(directory=str(settings.templates_dir))

SOURCE_TYPE_CHOICES = [
    ('internal_manual', '内部手册'),
    ('official_manual', '官方手册'),
    ('emergency_plan', '应急预案'),
    ('weekly_report', '周报案例'),
    ('monthly_report', '月报案例'),
]


def _app_context(request: Request, *, page_title: str, active_nav: str, **kwargs):
    payload = {
        'request': request,
        'page_title': page_title,
        'active_nav': active_nav,
        'app_title': settings.app_title,
        'auth_enabled': settings.require_auth,
        'authenticated': is_authenticated(request, settings),
    }
    payload.update(kwargs)
    return payload


def _filter_options() -> dict[str, list[str]]:
    chunk_records = load_chunk_records()
    docs = list_documents(limit=5000, settings=settings)
    system_names = sorted({str(item.get('system_name', '')).strip() for item in [*chunk_records, *docs] if str(item.get('system_name', '')).strip()})
    source_types = sorted({str(item.get('source_type', '')).strip() for item in [*chunk_records, *docs] if str(item.get('source_type', '')).strip()})
    source_groups = sorted({str(item.get('source_group', '')).strip() for item in [*chunk_records, *docs] if str(item.get('source_group', '')).strip()})
    tags = sorted({str(tag).strip() for item in [*chunk_records, *docs] for tag in item.get('tags', []) if str(tag).strip()})
    return {
        'system_names': system_names,
        'source_types': source_types,
        'source_groups': source_groups,
        'tags': tags,
    }


def _format_range(date_start: str | None, date_end: str | None) -> str:
    values = [value for value in [str(date_start or '').strip(), str(date_end or '').strip()] if value]
    return ' ~ '.join(values) if values else '-'


def _format_size(size: int) -> str:
    value = float(size)
    units = ['B', 'KB', 'MB', 'GB']
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f'{value:.0f}{unit}' if unit == 'B' else f'{value:.1f}{unit}'
        value /= 1024
    return f'{size}B'


def _sort_count_rows(values: dict[str, int]) -> list[dict[str, object]]:
    return [{'name': key, 'count': count} for key, count in sorted(values.items(), key=lambda item: item[1], reverse=True)]


def _build_search_filters(
    *,
    search_mode: str = 'all',
    system_name: str = '',
    source_type: str = '',
    tag: str = '',
    date_from: str = '',
    date_to: str = '',
) -> dict[str, str]:
    filters: dict[str, str] = {'search_mode': search_mode or 'all'}
    if system_name:
        filters['system_name'] = system_name
    if source_type:
        filters['source_type'] = source_type
    if tag:
        filters['tag'] = tag
    if date_from:
        filters['date_from'] = date_from
    if date_to:
        filters['date_to'] = date_to
    return filters


def _ui_case_detail_url(section_id: str) -> str:
    return f"/app/cases/{quote(section_id)}"


def _ui_manual_detail_url(section_id: str) -> str:
    return f"/app/manuals/{quote(section_id)}"


def _ui_manual_original_url(section_id: str) -> str:
    return f"/app/manuals/{quote(section_id)}/original"


def _ui_search_url(query: str, **filters: str) -> str:
    params = {'q': query, **{key: value for key, value in filters.items() if value}}
    return '/app/search?' + urlencode(params)


def _enrich_citations_for_ui(answer: dict[str, object]) -> dict[str, object]:
    citations = answer.get('citations', [])
    if not isinstance(citations, list):
        return answer
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        source_group = str(citation.get('source_group', '') or '')
        section_id = str(citation.get('section_id', '') or '')
        if source_group == 'case' and section_id:
            citation['detail_url'] = _ui_case_detail_url(section_id)
        elif source_group == 'manual' and section_id:
            citation['detail_url'] = _ui_manual_detail_url(section_id)
            citation['original_url'] = _ui_manual_original_url(section_id)
    return answer


def _dashboard_cards() -> list[dict[str, str]]:
    analysis = build_library_analysis(settings)
    totals = analysis['totals']
    return [
        {'label': '资料总数', 'value': str(totals['documents']), 'hint': '已纳入资料库的文档'},
        {'label': '已建立索引', 'value': str(totals['indexed']), 'hint': '可参与问答检索'},
        {'label': '待处理', 'value': str(totals['pending']), 'hint': '已上传但未完成索引'},
        {'label': '异常资料', 'value': str(totals['errors']), 'hint': '需要重新处理的文档'},
    ]


@router.get('/app', response_class=HTMLResponse)
def app_dashboard(request: Request, message: str = ''):
    analysis = build_library_analysis(settings)
    timeline = build_case_timeline(limit=6)
    for row in timeline:
        row['detail_url'] = _ui_case_detail_url(str(row.get('section_id', '')))
    topics = build_topic_view(limit_topics=6)
    for topic in topics:
        topic['detail_url'] = '/app/topics/' + quote(str(topic['tag']))
    recent_logs = load_recent_access_logs(limit=8, settings=settings)
    return templates.TemplateResponse(
        request,
        'dashboard.html',
        _app_context(
            request,
            page_title='资料库总览',
            active_nav='dashboard',
            message=message,
            cards=_dashboard_cards(),
            analysis=analysis,
            topic_rows=topics,
            timeline_rows=timeline,
            recent_logs=recent_logs,
        ),
    )


@router.get('/app/search', response_class=HTMLResponse)
def search_page(
    request: Request,
    q: str = '',
    rewrite: bool = False,
    search_mode: str = 'all',
    system_name: str = '',
    source_type: str = '',
    tag: str = '',
    date_from: str = '',
    date_to: str = '',
):
    filter_options = _filter_options()
    filters = _build_search_filters(
        search_mode=search_mode,
        system_name=system_name,
        source_type=source_type,
        tag=tag,
        date_from=date_from,
        date_to=date_to,
    )
    results: list[dict] = []
    answer: dict[str, object] | None = None
    if q.strip():
        results = hybrid_search(q, limit=12, filters=filters)
        answer = _enrich_citations_for_ui(generate_cited_answer(q, results, use_model_rewrite=rewrite))
        for item in results:
            raw_tags = item.get('tags')
            if not raw_tags:
                try:
                    raw_tags = json.loads(str(item.get('tags_json') or '[]'))
                except json.JSONDecodeError:
                    raw_tags = [part.strip() for part in str(item.get('tag_text') or '').split() if part.strip()]
            item['tags'] = [str(tag_name).strip() for tag_name in raw_tags if str(tag_name).strip()]
            if str(item.get('source_group', '')) == 'case':
                item['detail_url'] = _ui_case_detail_url(str(item.get('section_id', '')))
            elif str(item.get('source_group', '')) == 'manual':
                item['detail_url'] = _ui_manual_detail_url(str(item.get('section_id', '')))
                item['original_url'] = _ui_manual_original_url(str(item.get('section_id', '')))
    samples = [
        {'text': '视频平台资源共享怎么操作', 'url': _ui_search_url('视频平台资源共享怎么操作', search_mode='manual_qa')},
        {'text': '视联网会议蓝屏时先查什么', 'url': _ui_search_url('视联网会议蓝屏时先查什么', search_mode='manual_qa', system_name='视联网')},
        {'text': '视频平台客户端登录不上怎么处理', 'url': _ui_search_url('视频平台客户端登录不上怎么处理', search_mode='case_search')},
        {'text': '轮询助手相关问题如何处理', 'url': _ui_search_url('轮询助手相关问题如何处理', search_mode='case_search', tag='轮询助手')},
    ]
    return templates.TemplateResponse(
        request,
        'search.html',
        _app_context(
            request,
            page_title='知识问答与检索',
            active_nav='search',
            query=q,
            rewrite=rewrite,
            search_mode=search_mode,
            system_name=system_name,
            source_type=source_type,
            tag=tag,
            date_from=date_from,
            date_to=date_to,
            filter_options=filter_options,
            answer=answer,
            results=results,
            samples=samples,
        ),
    )


@router.get('/app/library', response_class=HTMLResponse)
def library_page(
    request: Request,
    message: str = '',
    query: str = '',
    source_group: str = '',
    source_type: str = '',
    system_name: str = '',
    status: str = '',
):
    guard = ensure_admin_page(request, settings)
    if guard is not None:
        return guard
    documents = list_documents(
        query=query,
        source_group=source_group,
        source_type=source_type,
        system_name=system_name,
        status=status,
        limit=400,
        settings=settings,
    )
    analysis = build_library_analysis(settings)
    filter_options = _filter_options()
    recent_logs = load_recent_access_logs(limit=12, settings=settings)
    for document in documents:
        document['size_label'] = _format_size(int(document['file_size']))
        document['date_range'] = _format_range(document.get('date_start'), document.get('date_end'))
    return templates.TemplateResponse(
        request,
        'library.html',
        _app_context(
            request,
            page_title='资料库管理',
            active_nav='library',
            message=message,
            documents=documents,
            analysis=analysis,
            recent_logs=recent_logs,
            filter_options=filter_options,
            source_type_choices=SOURCE_TYPE_CHOICES,
            filters={'query': query, 'source_group': source_group, 'source_type': source_type, 'system_name': system_name, 'status': status},
            status_rows=_sort_count_rows(analysis['status_counts']),
            system_rows=_sort_count_rows(analysis['system_counts']),
            tag_rows=_sort_count_rows(analysis['tag_counts']),
            timeline_rows=_sort_count_rows(analysis['timeline_counts']),
            deploy_info={
                'data_dir': str(settings.data_dir),
                'library_db_path': str(settings.library_db_path),
                'access_log_path': str(settings.access_log_path),
                'share_url': f"http://{settings.app_host}:{settings.app_port}/app",
                'auth_enabled': settings.require_auth,
            },
        ),
    )


@router.post('/app/library/upload')
async def upload_library_document(
    request: Request,
    title: str = Form(...),
    source_type: str = Form(...),
    system_name: str = Form(...),
    tags: str = Form(''),
    notes: str = Form(''),
    date_start: str = Form(''),
    date_end: str = Form(''),
    index_now: str | None = Form(None),
    file: UploadFile = File(...),
):
    ensure_admin_api(request, settings)
    payload = await file.read()
    document = save_uploaded_document(
        filename=file.filename or 'upload.txt',
        content=payload,
        title=title,
        source_type=source_type,
        system_name=system_name,
        tags=[item.strip() for item in tags.replace('，', ',').split(',') if item.strip()],
        notes=notes,
        date_start=date_start or None,
        date_end=date_end or None,
        index_now=index_now is not None,
        settings=settings,
    )
    return RedirectResponse(
        url=f"/app/library?message={quote('上传完成：' + str(document['title']))}",
        status_code=303,
    )


@router.post('/app/library/rebuild')
def rebuild_library_indexes(request: Request):
    ensure_admin_api(request, settings)
    summary = rebuild_all_documents(settings)
    message = f"全量重建完成：成功 {summary['indexed_count']}，失败 {summary['error_count']}"
    return RedirectResponse(url=f"/app/library?message={quote(message)}", status_code=303)


@router.post('/app/library/documents/{doc_id}/reindex')
def reindex_library_document(request: Request, doc_id: str):
    ensure_admin_api(request, settings)
    document = reindex_document(doc_id, settings)
    return RedirectResponse(
        url=f"/app/library?message={quote('重新索引完成：' + str(document['title']))}",
        status_code=303,
    )


@router.get('/app/library/documents/{doc_id}/download')
def download_library_document(request: Request, doc_id: str):
    guard = ensure_admin_page(request, settings)
    if guard is not None:
        return guard
    document = get_document(doc_id, settings)
    if document is None:
        return RedirectResponse(url='/app/library?message=' + quote('未找到对应资料'), status_code=303)
    path = Path(str(document['file_path']))
    return FileResponse(path, filename=str(document['original_filename']) or path.name)


@router.get('/app/library/export')
def export_library_data(request: Request, format: str = 'csv', kind: str = 'documents'):
    guard = ensure_admin_page(request, settings)
    if guard is not None:
        return guard
    if kind == 'analysis':
        filename, media_type, content = export_analysis(format, settings=settings)
    else:
        filename, media_type, content = export_documents(format, settings=settings)
    export_path = settings.data_dir / 'exports' / filename
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_bytes(content)
    return FileResponse(export_path, media_type=media_type, filename=filename)


@router.get('/app/library/bundle-export')
def export_library_bundle_file(request: Request):
    guard = ensure_admin_page(request, settings)
    if guard is not None:
        return guard
    export_path = settings.data_dir / 'exports' / 'ops-manual-rag-assistant-library-bundle.zip'
    export_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path = export_bundle(export_path)
    return FileResponse(bundle_path, media_type='application/zip', filename=bundle_path.name)


@router.post('/app/library/bundle-import')
async def import_library_bundle_file(
    request: Request,
    replace_existing: str | None = Form(None),
    bundle_file: UploadFile = File(...),
):
    ensure_admin_api(request, settings)
    suffix = Path(bundle_file.filename or 'library-bundle.zip').suffix or '.zip'
    with tempfile.NamedTemporaryFile(prefix='ops-assistant-bundle-', suffix=suffix, delete=False) as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write(await bundle_file.read())
    try:
        summary = import_bundle(temp_path, replace_existing=replace_existing is not None)
    finally:
        temp_path.unlink(missing_ok=True)
    message = (
        f"迁移包导入完成：导入 {summary.get('imported', 0)} 份，"
        f"跳过 {summary.get('skipped_existing', 0)} 份，"
        f"成功索引 {summary.get('indexed_count', 0)} 份"
    )
    return RedirectResponse(url=f"/app/library?message={quote(message)}", status_code=303)


@router.get('/app/timeline', response_class=HTMLResponse)
def timeline_page(
    request: Request,
    system_name: str = '',
    source_type: str = '',
    tag: str = '',
    date_from: str = '',
    date_to: str = '',
    limit: int = 40,
):
    filters = {key: value for key, value in {
        'system_name': system_name,
        'source_type': source_type,
        'tag': tag,
        'date_from': date_from,
        'date_to': date_to,
    }.items() if value}
    entries = build_case_timeline(filters, limit=limit)
    for entry in entries:
        entry['detail_url'] = _ui_case_detail_url(str(entry['section_id']))
        entry['search_url'] = _ui_search_url(
            str(entry.get('section_title') or entry.get('title') or '历史案例'),
            search_mode='case_search',
            system_name=str(entry.get('system_name', '')),
            source_type=str(entry.get('source_type', '')),
            tag=str(next((tag for tag in entry.get('tags', []) if tag not in {'案例库', '周报', '月报', '历史案例', '政企驻场运维'}), '')),
            date_from=str(entry.get('date_start', '') or ''),
            date_to=str(entry.get('date_end', '') or ''),
        )
    return templates.TemplateResponse(
        request,
        'timeline.html',
        _app_context(
            request,
            page_title='历史案例时间轴',
            active_nav='timeline',
            entries=entries,
            filters={'system_name': system_name, 'source_type': source_type, 'tag': tag, 'date_from': date_from, 'date_to': date_to, 'limit': limit},
            filter_options=_filter_options(),
        ),
    )


@router.get('/app/topics', response_class=HTMLResponse)
def topic_view_page(
    request: Request,
    system_name: str = '',
    source_type: str = '',
    tag: str = '',
    date_from: str = '',
    date_to: str = '',
    limit_topics: int = 15,
):
    filters = {key: value for key, value in {
        'system_name': system_name,
        'source_type': source_type,
        'tag': tag,
        'date_from': date_from,
        'date_to': date_to,
    }.items() if value}
    topics = build_topic_view(filters, limit_topics=limit_topics)
    for topic in topics:
        topic['detail_url'] = '/app/topics/' + quote(str(topic['tag'])) + ('?' + urlencode(filters) if filters else '')
        topic['search_url'] = _ui_search_url(f"{topic['tag']}怎么处理", search_mode='case_search', tag=str(topic['tag']), system_name=system_name, source_type=source_type, date_from=date_from, date_to=date_to)
    return templates.TemplateResponse(
        request,
        'topics.html',
        _app_context(
            request,
            page_title='专题视图',
            active_nav='topics',
            topics=topics,
            filters={'system_name': system_name, 'source_type': source_type, 'tag': tag, 'date_from': date_from, 'date_to': date_to},
            filter_options=_filter_options(),
        ),
    )


@router.get('/app/topics/{tag}', response_class=HTMLResponse)
def topic_detail_page(
    request: Request,
    tag: str,
    system_name: str = '',
    source_type: str = '',
    date_from: str = '',
    date_to: str = '',
    limit: int = 100,
):
    filters = {key: value for key, value in {
        'system_name': system_name,
        'source_type': source_type,
        'date_from': date_from,
        'date_to': date_to,
    }.items() if value}
    detail = build_topic_detail(tag, filters, limit=limit)
    for entry in detail['entries']:
        entry['detail_url'] = _ui_case_detail_url(str(entry['section_id']))
        entry['search_url'] = _ui_search_url(
            str(entry.get('section_title') or entry.get('title') or tag),
            search_mode='case_search',
            system_name=str(entry.get('system_name', '')),
            source_type=str(entry.get('source_type', '')),
            tag=tag,
            date_from=str(entry.get('date_start', '') or ''),
            date_to=str(entry.get('date_end', '') or ''),
        )
    return templates.TemplateResponse(
        request,
        'topic_detail.html',
        _app_context(
            request,
            page_title=f'专题详情 · {tag}',
            active_nav='topics',
            detail=detail,
            back_url='/app/topics?' + urlencode(filters) if filters else '/app/topics',
        ),
    )


@router.get('/app/cases/{section_id}', response_class=HTMLResponse)
def case_detail_page(request: Request, section_id: str):
    detail = get_case_detail(section_id)
    if detail is None:
        return templates.TemplateResponse(request, 'not_found.html', _app_context(request, page_title='未找到案例', active_nav='timeline', message='未找到对应案例'))
    detail['search_url'] = _ui_search_url(
        str(detail.get('section_title') or detail.get('title') or '历史案例'),
        search_mode='case_search',
        system_name=str(detail.get('system_name', '')),
        source_type=str(detail.get('source_type', '')),
        tag=str(next((tag for tag in detail.get('tags', []) if tag not in {'案例库', '周报', '月报', '历史案例', '政企驻场运维'}), '')),
        date_from=str(detail.get('date_start', '') or ''),
        date_to=str(detail.get('date_end', '') or ''),
    )
    return templates.TemplateResponse(
        request,
        'case_detail.html',
        _app_context(request, page_title='案例详情', active_nav='timeline', detail=detail),
    )


@router.get('/app/manuals/{section_id}', response_class=HTMLResponse)
def manual_detail_page(request: Request, section_id: str):
    detail = get_manual_detail(section_id)
    if detail is None:
        return templates.TemplateResponse(request, 'not_found.html', _app_context(request, page_title='未找到章节', active_nav='search', message='未找到对应手册章节'))
    detail['search_url'] = _ui_search_url(str(detail.get('section_title') or detail.get('title') or '手册章节'), search_mode='manual_qa')
    detail['original_url'] = _ui_manual_original_url(section_id)
    return templates.TemplateResponse(
        request,
        'manual_detail.html',
        _app_context(request, page_title='手册章节详情', active_nav='search', detail=detail),
    )


@router.get('/app/manuals/{section_id}/original')
def manual_original_file(request: Request, section_id: str):
    detail = get_manual_detail(section_id)
    if detail is None:
        return RedirectResponse(url='/app/search?message=' + quote('未找到对应原始手册'), status_code=303)
    source_path = Path(str(detail.get('source_file', '')))
    if not source_path.exists():
        return RedirectResponse(url='/app/search?message=' + quote('原始手册文件不存在'), status_code=303)
    return FileResponse(source_path, filename=source_path.name)


@router.get('/login', response_class=HTMLResponse)
def login_page(request: Request, next: str = '/app/library', error: str = ''):
    return templates.TemplateResponse(
        request,
        'login.html',
        _app_context(request, page_title='管理员登录', active_nav='login', next=next, error=error),
    )


@router.post('/login')
def login_submit(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form('/app/library')):
    if not credentials_valid(username, password, settings):
        return RedirectResponse(url=f"/login?next={quote(next)}&error={quote('账号或密码不正确')}", status_code=303)
    response = RedirectResponse(url=next or '/app/library', status_code=303)
    issue_admin_session(response, settings)
    return response


@router.post('/logout')
def logout_submit(request: Request):
    response = RedirectResponse(url='/app', status_code=303)
    clear_admin_session(response)
    return response
