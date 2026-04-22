from __future__ import annotations

import io
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.main import app
from app.retrieval.index_builder import build_indexes

REPORT_PATH = PROJECT_ROOT / 'docs' / '落地验收报告.md'
TESTS_DIR = PROJECT_ROOT / 'tests'


def run_unittests() -> tuple[bool, str]:
    loader = unittest.TestLoader()
    suite = loader.discover(str(TESTS_DIR), pattern='test_*.py')
    stream = io.StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2)
    result = runner.run(suite)
    output = stream.getvalue().strip()
    success = result.wasSuccessful()
    return success, output


def run_smoke_checks() -> list[dict[str, object]]:
    client = TestClient(app)
    manual_search = client.get('/search', params={'q': '视频平台资源共享怎么操作', 'search_mode': 'manual_qa'}).json()
    manual_section_id = manual_search['answer']['citations'][0]['section_id'] if manual_search.get('answer', {}).get('citations') else ''
    case_search = client.get('/search', params={'q': '视频平台客户端登录不上怎么处理', 'search_mode': 'case_search', 'tag': '客户端登录'}).json()
    checks = [
        ('/healthz', client.get('/healthz')),
        ('/catalog', client.get('/catalog')),
        ('/app', client.get('/app')),
        ('/app/search', client.get('/app/search')),
        ('/app/library', client.get('/app/library')),
        ('/app/timeline', client.get('/app/timeline')),
        ('/app/topics', client.get('/app/topics')),
        ('/search manual', client.get('/search', params={'q': '视频平台资源共享怎么操作', 'search_mode': 'manual_qa'})),
        ('/search unrelated', client.get('/search', params={'q': '火星基地怎么部署', 'search_mode': 'manual_qa'})),
        ('/manual-detail', client.get('/manual-detail', params={'section_id': manual_section_id})),
        ('/search case', client.get('/search', params={'q': '视频平台客户端登录不上怎么处理', 'search_mode': 'case_search', 'tag': '客户端登录'})),
        ('/case-timeline', client.get('/case-timeline', params={'tag': '轮询助手'})),
        ('/topic-detail', client.get('/topic-detail', params={'tag': '轮询助手'})),
        ('/app/library/export', client.get('/app/library/export', params={'kind': 'documents', 'format': 'csv'})),
    ]
    rows: list[dict[str, object]] = []
    for name, response in checks:
        payload: object
        try:
            payload = response.json()
        except Exception:
            payload = response.text[:200]
        rows.append(
            {
                'name': name,
                'status_code': response.status_code,
                'ok': response.status_code == 200,
                'count': payload.get('count') if isinstance(payload, dict) else None,
                'found': payload.get('found') if isinstance(payload, dict) else None,
            }
        )
    rows.append({
        'name': 'manual citation detail_url',
        'status_code': 200,
        'ok': str(manual_search['answer']['citations'][0].get('detail_url', '')).startswith('/manual-detail-page?'),
        'count': manual_search.get('count'),
        'found': None,
    })
    rows.append({
        'name': 'case citation detail_url',
        'status_code': 200,
        'ok': str(case_search['answer']['citations'][0].get('detail_url', '')).startswith('/case-detail-page?'),
        'count': case_search.get('count'),
        'found': None,
    })
    return rows


def write_report(index_summary: dict[str, object], test_ok: bool, test_output: str, smoke_rows: list[dict[str, object]]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    smoke_ok = all(bool(row['ok']) for row in smoke_rows)
    lines = [
        '# 政企运维知识库问答助手落地验收报告',
        '',
        f'- 验收时间：{now}',
        f'- 索引块数：{index_summary.get("chunk_count")}',
        f'- 关键词索引块数：{index_summary.get("keyword_index", {}).get("chunk_count")}',
        f'- 向量索引数：{index_summary.get("semantic_index", {}).get("vector_count")}',
        f'- 验收结论：{"通过" if test_ok and smoke_ok else "未通过"}',
        '',
        '## 1. 自动化测试',
        '',
        '```text',
        test_output or '无输出',
        '```',
        '',
        '## 2. Smoke Check',
        '',
        '| 检查项 | 状态码 | 通过 | count/found |',
        '| --- | ---: | --- | --- |',
    ]
    for row in smoke_rows:
        extra = row['count'] if row['count'] is not None else row['found']
        lines.append(f"| {row['name']} | {row['status_code']} | {'是' if row['ok'] else '否'} | {extra if extra is not None else '-'} |")
    lines += [
        '',
        '## 3. 本轮验收范围',
        '',
        '- 文档资料库上传、保存、分类归档与导出',
        '- 增量索引与全量重建链路',
        '- 手册问答 / 历史案例检索双模式',
        '- 新版前端总览、资料库、时间轴与专题页面',
        '- 权限控制入口与访问日志',
        '',
        '## 4. 当前可交付结论',
        '',
        '- 项目已从“面试演示版”推进到“本地可用的资料库问答系统”。',
        '- 当前版本已具备资料上传、索引更新、问答检索、案例复盘、数据导出和访问日志能力。',
        '- 若继续推进到内部长期使用，下一步重点应放在账户体系、定时任务和独立前端工程化。',
        '',
    ]
    REPORT_PATH.write_text('\n'.join(lines), encoding='utf-8')


def main() -> int:
    index_summary = build_indexes(PROJECT_ROOT)
    test_ok, test_output = run_unittests()
    smoke_rows = run_smoke_checks()
    write_report(index_summary, test_ok, test_output, smoke_rows)
    print(json.dumps({'test_ok': test_ok, 'smoke_ok': all(row['ok'] for row in smoke_rows), 'report_path': str(REPORT_PATH)}, ensure_ascii=False))
    return 0 if test_ok and all(row['ok'] for row in smoke_rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
