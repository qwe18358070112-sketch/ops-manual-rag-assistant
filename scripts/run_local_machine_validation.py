from __future__ import annotations

import csv
import http.cookiejar
import json
import mimetypes
import os
import sqlite3
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib import parse, request
from urllib.error import HTTPError, URLError

BASE_URL = os.environ.get('OPS_ASSISTANT_BASE_URL', 'http://127.0.0.1:8000')
AUTH_BASE_URL_ENV = os.environ.get('OPS_ASSISTANT_AUTH_BASE_URL', '').strip()
PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = PROJECT_ROOT / 'docs' / '本机全功能测试报告.md'
DATA_DIR = PROJECT_ROOT / 'data'
EXPORTS_DIR = DATA_DIR / 'exports'
ACCESS_LOG = DATA_DIR / 'logs' / 'access.log'
LIBRARY_DB = DATA_DIR / 'library' / 'metadata.sqlite3'

TEST_DOC_TITLE = f"本机全功能验收文档-{uuid.uuid4().hex[:6]}"
TEST_DOC_FILENAME = 'local-machine-validation.txt'
TEST_DOC_CONTENT = (
    '本机全功能验收文档\n\n'
    '本机全功能验收时，如果视频平台轮询助手异常，先检查客户端是否前台显示，'
    '再检查收藏夹顺序、宫格模式和全屏状态。\n'
    '如果仍未恢复，可执行重新索引并再次检索。\n'
)
AUTH_USERNAME = 'admin'
AUTH_PASSWORD = 'secret123'


class NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_opener(with_cookies: bool = False, follow_redirects: bool = True, jar: http.cookiejar.CookieJar | None = None):
    handlers: list[object] = [request.ProxyHandler({})]
    cookie_jar = jar
    if with_cookies:
        if cookie_jar is None:
            cookie_jar = http.cookiejar.CookieJar()
        handlers.append(request.HTTPCookieProcessor(cookie_jar))
    if not follow_redirects:
        handlers.append(NoRedirectHandler())
    opener = request.build_opener(*handlers)
    return opener, cookie_jar


OPENER, _ = build_opener(with_cookies=False)


def http_request(base_url: str, path: str, *, method: str = 'GET', params: dict[str, str] | None = None, headers: dict[str, str] | None = None, data: bytes | None = None, opener=None) -> tuple[int, dict[str, str], bytes, str]:
    opener = opener or OPENER
    url = base_url + path
    if params:
        url += ('?' if '?' not in url else '&') + parse.urlencode(params)
    req = request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status, dict(resp.headers.items()), resp.read(), resp.geturl()
    except HTTPError as error:
        return error.code, dict(error.headers.items()), error.read(), error.geturl()


def build_multipart(fields: dict[str, str], file_field: str, filename: str, content: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = '----CodexBoundary' + uuid.uuid4().hex
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.extend([
            f'--{boundary}'.encode(),
            f'Content-Disposition: form-data; name="{key}"'.encode(),
            b'',
            value.encode('utf-8'),
        ])
    parts.extend([
        f'--{boundary}'.encode(),
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"'.encode(),
        f'Content-Type: {content_type}'.encode(),
        b'',
        content,
        f'--{boundary}--'.encode(),
        b'',
    ])
    return b'\r\n'.join(parts), boundary


def parse_json_bytes(payload: bytes) -> dict:
    return json.loads(payload.decode('utf-8'))


def read_csv_header(path: Path) -> list[str]:
    with path.open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.reader(handle)
        return next(reader)


def wait_for_server(base_url: str, opener=None) -> None:
    for _ in range(40):
        try:
            status, _, body, _ = http_request(base_url, '/healthz', opener=opener)
        except URLError:
            time.sleep(0.5)
            continue
        if status == 200 and body:
            return
        time.sleep(0.5)
    raise RuntimeError(f'服务未就绪：{base_url}')


def run_manage_command(args: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(
        [str(PROJECT_ROOT / '.venv' / 'bin' / 'python'), str(PROJECT_ROOT / 'scripts' / 'manage_library.py'), *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def cleanup_created_docs(doc_ids: list[str]) -> None:
    if not doc_ids:
        return
    conn = sqlite3.connect(LIBRARY_DB)
    conn.row_factory = sqlite3.Row
    for doc_id in doc_ids:
        row = conn.execute('SELECT file_path FROM documents WHERE doc_id = ?', (doc_id,)).fetchone()
        if row is not None:
            file_path = Path(str(row['file_path']))
            if file_path.exists():
                file_path.unlink()
        for folder in [DATA_DIR / 'extracted', DATA_DIR / 'chunks']:
            target = folder / f'{doc_id}.json'
            if target.exists():
                target.unlink()
        conn.execute('DELETE FROM documents WHERE doc_id = ?', (doc_id,))
    conn.commit()
    conn.close()


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def start_auth_server(port: int) -> subprocess.Popen[str]:
    env = os.environ.copy()
    for key in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'all_proxy']:
        env.pop(key, None)
    env['OPS_ASSISTANT_REQUIRE_AUTH'] = 'true'
    env['OPS_ASSISTANT_ADMIN_USERNAME'] = AUTH_USERNAME
    env['OPS_ASSISTANT_ADMIN_PASSWORD'] = AUTH_PASSWORD
    process = subprocess.Popen(
        [str(PROJECT_ROOT / '.venv' / 'bin' / 'uvicorn'), 'app.main:app', '--host', '127.0.0.1', '--port', str(port)],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process


def stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    wait_for_server(BASE_URL)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    created_doc_ids: list[str] = []
    rows: list[dict[str, str]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        rows.append({'name': name, 'ok': '通过' if ok else '失败', 'detail': detail})

    auth_server = None
    auth_base_url = AUTH_BASE_URL_ENV or f'http://127.0.0.1:{_pick_free_port()}'
    auth_port = parse.urlsplit(auth_base_url).port or 8002
    try:
        page_expectations = {
            '/app': '先给结论，再给步骤，再回溯手册和案例',
            '/app/search': '开始检索',
            '/app/library': '上传并入库',
            '/app/timeline': '时间轴筛选',
            '/app/topics': '专题聚合筛选',
            '/catalog': 'filter_options',
            '/healthz': 'ok',
        }
        for path, expected in page_expectations.items():
            status, _, body, _ = http_request(BASE_URL, path)
            text = body.decode('utf-8', errors='ignore')
            ok = status == 200 and expected in text
            record(f'页面访问 {path}', ok, f'status={status}, expected={expected}')

        body, boundary = build_multipart(
            {
                'title': TEST_DOC_TITLE,
                'source_type': 'internal_manual',
                'system_name': '视频平台',
                'tags': '本机验收,轮询助手,索引重建',
                'notes': '本机全功能验收自动上传资料',
                'index_now': 'on',
            },
            'file',
            TEST_DOC_FILENAME,
            TEST_DOC_CONTENT.encode('utf-8'),
            mimetypes.guess_type(TEST_DOC_FILENAME)[0] or 'text/plain',
        )
        status, headers, _, final_url = http_request(
            BASE_URL,
            '/app/library/upload',
            method='POST',
            headers={'Content-Type': f'multipart/form-data; boundary={boundary}', 'Content-Length': str(len(body))},
            data=body,
        )
        record('上传资料并立即增量索引', status in {200, 303}, f'status={status}, location={headers.get("Location", "") or final_url}')

        conn = sqlite3.connect(LIBRARY_DB)
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT * FROM documents WHERE title = ? ORDER BY id DESC LIMIT 1', (TEST_DOC_TITLE,)).fetchone()
        conn.close()
        if row is None:
            record('上传资料写入资料库', False, 'metadata.sqlite3 未找到测试资料')
            raise RuntimeError('测试资料未写入资料库')
        doc_id = str(row['doc_id'])
        created_doc_ids.append(doc_id)
        upload_file = Path(str(row['file_path']))
        record('上传资料已保存到本地目录', upload_file.exists(), f'doc_id={doc_id}, path={upload_file}')
        record('增量索引已生成 section/chunk', str(row['status']) == 'indexed' and int(row['chunk_count']) > 0, f'status={row["status"]}, sections={row["section_count"]}, chunks={row["chunk_count"]}')

        status, _, body, _ = http_request(BASE_URL, '/search', params={'q': '本机全功能验收时轮询助手异常先检查什么', 'search_mode': 'manual_qa'})
        search_payload = parse_json_bytes(body) if body else {}
        top_title = search_payload.get('results', [{}])[0].get('section_title', '') if search_payload.get('results') else ''
        record('新上传资料可被检索命中', status == 200 and search_payload.get('count', 0) > 0 and TEST_DOC_TITLE in top_title, f'status={status}, count={search_payload.get("count")}, top={top_title}')

        status, _, body, _ = http_request(BASE_URL, '/app/library')
        library_html = body.decode('utf-8', errors='ignore')
        record('资料库页面展示新资料', status == 200 and TEST_DOC_TITLE in library_html, f'status={status}')

        status, _, body, _ = http_request(BASE_URL, f'/app/library/documents/{parse.quote(doc_id)}/download')
        record('下载上传原件', status == 200 and body == TEST_DOC_CONTENT.encode('utf-8'), f'status={status}, bytes={len(body)}')

        status, headers, _, final_url = http_request(BASE_URL, f'/app/library/documents/{parse.quote(doc_id)}/reindex', method='POST')
        record('单文档重新索引', status in {200, 303}, f'status={status}, location={headers.get("Location", "") or final_url}')

        status, headers, _, final_url = http_request(BASE_URL, '/app/library/rebuild', method='POST')
        record('全量重建索引', status in {200, 303}, f'status={status}, location={headers.get("Location", "") or final_url}')

        status, _, body, _ = http_request(BASE_URL, '/app/library/export', params={'kind': 'documents', 'format': 'csv'})
        export_csv = EXPORTS_DIR / 'machine-validation-library-documents.csv'
        export_csv.write_bytes(body)
        header = read_csv_header(export_csv) if status == 200 else []
        record('导出资料列表 CSV', status == 200 and 'doc_id' in header, f'status={status}, header={header[:5]}')

        status, _, body, _ = http_request(BASE_URL, '/app/library/export', params={'kind': 'analysis', 'format': 'json'})
        analysis_json = EXPORTS_DIR / 'machine-validation-library-analysis.json'
        analysis_json.write_bytes(body)
        analysis_payload = parse_json_bytes(body) if status == 200 else {}
        record('导出资料分析 JSON', status == 200 and 'status_counts' in analysis_payload, f'status={status}, keys={list(analysis_payload.keys())[:5]}')

        status, _, body, _ = http_request(BASE_URL, '/app/library/bundle-export')
        bundle_zip = EXPORTS_DIR / 'machine-validation-library-bundle.zip'
        bundle_zip.write_bytes(body)
        record('导出资料库迁移包 ZIP', status == 200 and body.startswith(b'PK'), f'status={status}, bytes={len(body)}')

        status, _, body, _ = http_request(BASE_URL, '/app/timeline')
        record('历史案例时间轴页面', status == 200 and '案例详情' in body.decode('utf-8', errors='ignore'), f'status={status}')
        status, _, body, _ = http_request(BASE_URL, '/app/topics')
        record('专题视图页面', status == 200 and '专题详情' in body.decode('utf-8', errors='ignore'), f'status={status}')
        status, _, body, _ = http_request(BASE_URL, '/app/topics/' + parse.quote('轮询助手'))
        record('专题详情页面', status == 200 and '轮询助手' in body.decode('utf-8', errors='ignore'), f'status={status}')

        status, _, body, _ = http_request(BASE_URL, '/case-timeline', params={'tag': '轮询助手'})
        case_timeline = parse_json_bytes(body) if status == 200 else {}
        first_case_id = case_timeline.get('entries', [{}])[0].get('section_id', '') if case_timeline.get('entries') else ''
        record('案例时间轴 API', status == 200 and case_timeline.get('count', 0) > 0, f'status={status}, count={case_timeline.get("count")}')

        status, _, body, _ = http_request(BASE_URL, '/topic-view', params={'tag': '轮询助手'})
        topic_view = parse_json_bytes(body) if status == 200 else {}
        record('专题视图 API', status == 200 and topic_view.get('count', 0) > 0, f'status={status}, count={topic_view.get("count")}')

        citations = search_payload.get('answer', {}).get('citations', [])
        manual_section_id = citations[0].get('section_id') if citations else ''
        status, _, body, _ = http_request(BASE_URL, '/manual-detail', params={'section_id': manual_section_id})
        manual_detail = parse_json_bytes(body) if status == 200 else {}
        record('手册章节详情 API', status == 200 and bool(manual_detail.get('found')), f'status={status}, found={manual_detail.get("found")}')

        if manual_section_id:
            status, _, body, _ = http_request(BASE_URL, '/app/manuals/' + parse.quote(manual_section_id))
            record('手册章节详情页面', status == 200 and '以当前章节进入问答' in body.decode('utf-8', errors='ignore'), f'status={status}')
        if first_case_id:
            status, _, body, _ = http_request(BASE_URL, '/app/cases/' + parse.quote(first_case_id))
            record('案例详情页面', status == 200 and '以当前案例进入问答' in body.decode('utf-8', errors='ignore'), f'status={status}')

        log_exists = ACCESS_LOG.exists() and ACCESS_LOG.stat().st_size > 0
        record('访问日志写入', log_exists, f'path={ACCESS_LOG}, size={ACCESS_LOG.stat().st_size if ACCESS_LOG.exists() else 0}')

        code, out, err = run_manage_command(['analysis'])
        record('管理脚本 analysis', code == 0 and 'documents' in out, f'code={code}, out={out[:120]}, err={err[:120]}')
        code, out, err = run_manage_command(['export', '--kind', 'analysis', '--format', 'json'])
        record('管理脚本 export', code == 0 and out and Path(out).exists(), f'code={code}, out={out[:120]}, err={err[:120]}')
        code, out, err = run_manage_command(['bundle-export', '--output', str(EXPORTS_DIR / 'machine-validation-cli-bundle.zip')])
        record('管理脚本 bundle-export', code == 0 and out and Path(out).exists(), f'code={code}, out={out[:120]}, err={err[:120]}')
        code, out, err = run_manage_command(['reindex', doc_id])
        record('管理脚本 reindex', code == 0 and doc_id in out, f'code={code}, out={out[:120]}, err={err[:120]}')

        # Real auth server test
        auth_server = start_auth_server(auth_port)
        shared_jar = http.cookiejar.CookieJar()
        auth_opener, _ = build_opener(with_cookies=True, jar=shared_jar)
        auth_noredirect_opener, _ = build_opener(with_cookies=True, follow_redirects=False, jar=shared_jar)
        wait_for_server(auth_base_url, opener=auth_opener)
        status, headers, _, _ = http_request(auth_base_url, '/app/library', opener=auth_noredirect_opener)
        redirect_location = next((value for key, value in headers.items() if key.lower() == 'location'), '')
        record('鉴权模式下未登录访问资料库会跳转登录', status in {302, 303} and '/login' in redirect_location, f'status={status}, location={redirect_location}')

        login_body = parse.urlencode({'username': AUTH_USERNAME, 'password': AUTH_PASSWORD, 'next': '/app/library'}).encode('utf-8')
        status, _, _, final_url = http_request(auth_base_url, '/login', method='POST', headers={'Content-Type': 'application/x-www-form-urlencoded'}, data=login_body, opener=auth_opener)
        record('鉴权模式下管理员登录', status == 200 and final_url.endswith('/app/library') and len(shared_jar) > 0, f'status={status}, final_url={final_url}, cookies={len(shared_jar)}')
        status, _, body, final_url = http_request(auth_base_url, '/app/library', opener=auth_opener)
        library_text = body.decode('utf-8', errors='ignore')
        record('鉴权模式下登录后访问资料库', status == 200 and ('资料库管理' in library_text or '上传并入库' in library_text), f'status={status}, final_url={final_url}, cookies={len(shared_jar)}')

    finally:
        cleanup_created_docs(created_doc_ids)
        try:
            code, out, err = run_manage_command(['rebuild'])
            record('清理后重建索引恢复现场', code == 0, f'code={code}, out={out[:120]}, err={err[:120]}')
        except Exception as error:  # noqa: BLE001
            record('清理后重建索引恢复现场', False, str(error))
        stop_process(auth_server)

    passed = sum(1 for row in rows if row['ok'] == '通过')
    total = len(rows)
    lines = [
        '# 本机全功能测试报告',
        '',
        f'- 测试地址：{BASE_URL}',
        f'- 鉴权测试地址：{auth_base_url}',
        f'- 测试时间：{time.strftime("%Y-%m-%d %H:%M:%S")}',
        f'- 测试结论：{"通过" if passed == total else "存在失败项"}',
        f'- 通过情况：{passed}/{total}',
        '',
        '| 测试项 | 结果 | 说明 |',
        '| --- | --- | --- |',
    ]
    for row in rows:
        lines.append(f"| {row['name']} | {row['ok']} | {row['detail']} |")
    REPORT_PATH.write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'passed': passed, 'total': total, 'report_path': str(REPORT_PATH)}, ensure_ascii=False))
    return 0 if passed == total else 1


if __name__ == '__main__':
    raise SystemExit(main())
