from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.core.config import get_settings
from app.retrieval.index_builder import build_indexes
from app.services.library import sync_ingestion_summary
from app.services.runtime_cache import clear_runtime_caches

TEST_FILENAME = 'test-upload-workflow.txt'
TEST_TITLE = '测试上传工作流资料'
TEST_QUERY = '自动化回归验收里轮询助手异常先检查什么'
TEST_CONTENT = '测试上传工作流资料\n\n自动化回归验收时，如果轮询助手异常，先检查客户端是否前台显示，再检查收藏夹顺序与全屏状态。\n如果仍然异常，可执行重建索引并重新加载配置。\n'


class LibraryWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)
        cls.settings = get_settings()
        cls.created_doc_ids: list[str] = []

    @classmethod
    def tearDownClass(cls) -> None:
        conn = sqlite3.connect(cls.settings.library_db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT doc_id, file_path FROM documents WHERE original_filename = ? OR title = ?",
            (TEST_FILENAME, TEST_TITLE),
        ).fetchall()
        for row in rows:
            doc_id = str(row['doc_id'])
            file_path = Path(str(row['file_path']))
            if file_path.exists():
                file_path.unlink()
            for directory in [cls.settings.extracted_dir, cls.settings.chunks_dir]:
                target = directory / f'{doc_id}.json'
                if target.exists():
                    target.unlink()
        conn.execute("DELETE FROM documents WHERE original_filename = ? OR title = ?", (TEST_FILENAME, TEST_TITLE))
        conn.commit()
        conn.close()
        build_indexes(cls.settings.project_root)
        clear_runtime_caches()
        sync_ingestion_summary(cls.settings)

    def test_app_pages_render(self) -> None:
        for path in ['/app', '/app/search', '/app/library', '/app/timeline', '/app/topics']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)

    def test_upload_indexes_and_export(self) -> None:
        upload_response = self.client.post(
            '/app/library/upload',
            data={
                'title': TEST_TITLE,
                'source_type': 'internal_manual',
                'system_name': '视频平台',
                'tags': '测试上传,轮询助手,索引验收',
                'notes': '自动化回归测试资料',
                'index_now': 'on',
            },
            files={'file': (TEST_FILENAME, TEST_CONTENT.encode('utf-8'), 'text/plain')},
            follow_redirects=False,
        )
        self.assertEqual(upload_response.status_code, 303)
        self.assertIn('/app/library?message=', upload_response.headers['location'])

        search_response = self.client.get('/search', params={'q': TEST_QUERY, 'search_mode': 'manual_qa'})
        self.assertEqual(search_response.status_code, 200)
        payload = search_response.json()
        self.assertGreater(payload['count'], 0)
        self.assertIn(TEST_TITLE, payload['results'][0]['section_title'])

        library_response = self.client.get('/app/library')
        self.assertEqual(library_response.status_code, 200)
        self.assertIn(TEST_TITLE, library_response.text)
        self.assertIn('迁移与共享', library_response.text)
        self.assertIn('导出迁移包 ZIP', library_response.text)

        search_page_response = self.client.get('/app/search', params={'q': TEST_QUERY, 'search_mode': 'manual_qa'})
        self.assertEqual(search_page_response.status_code, 200)
        self.assertIn(TEST_TITLE, search_page_response.text)
        self.assertIn('知识助手回答', search_page_response.text)
        self.assertIn('直接回答', search_page_response.text)
        self.assertIn('打开原始手册', search_page_response.text)

        export_response = self.client.get('/app/library/export', params={'kind': 'documents', 'format': 'csv'})
        self.assertEqual(export_response.status_code, 200)
        self.assertIn('text/csv', export_response.headers.get('content-type', ''))
        self.assertIn('doc_id', export_response.text)

        analysis_response = self.client.get('/app/library/export', params={'kind': 'analysis', 'format': 'json'})
        self.assertEqual(analysis_response.status_code, 200)
        self.assertIn('application/json', analysis_response.headers.get('content-type', ''))
        self.assertIn('status_counts', analysis_response.text)

        bundle_response = self.client.get('/app/library/bundle-export')
        self.assertEqual(bundle_response.status_code, 200)
        self.assertIn('application/zip', bundle_response.headers.get('content-type', ''))
        self.assertTrue(bundle_response.content.startswith(b'PK'))


if __name__ == '__main__':
    unittest.main()
