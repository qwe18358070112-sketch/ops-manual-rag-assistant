from __future__ import annotations

import unittest

try:
    from fastapi.testclient import TestClient
except RuntimeError as exc:
    TestClient = None
    TESTCLIENT_IMPORT_ERROR = exc
else:
    TESTCLIENT_IMPORT_ERROR = None

from app.main import app


@unittest.skipIf(TestClient is None, f'缺少测试依赖：httpx。{TESTCLIENT_IMPORT_ERROR}')
class ApiRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_healthz(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_catalog_contains_filters_and_previews(self) -> None:
        response = self.client.get("/catalog")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("filter_options", payload)
        self.assertIn("case_timeline_preview", payload)
        self.assertIn("topic_view_preview", payload)
        self.assertGreater(len(payload["filter_options"]["tags"]), 0)

    def test_manual_search_returns_manual_results(self) -> None:
        response = self.client.get(
            "/search",
            params={
                "q": "视频平台资源共享怎么操作",
                "search_mode": "manual_qa",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreater(payload["count"], 0)
        self.assertGreater(len(payload["answer"]["citations"]), 0)
        self.assertEqual(payload["results"][0]["source_group"], "manual")
        self.assertIn("direct_answer", payload["answer"])
        self.assertGreater(len(payload["answer"]["steps"]), 0)
        citation = payload["answer"]["citations"][0]
        self.assertIn("detail_url", citation)
        self.assertIn("/manual-detail-page?", citation["detail_url"])
        self.assertIn("original_url", citation)
        self.assertIn("/app/manuals/", citation["original_url"])

    def test_manual_detail_from_citation(self) -> None:
        payload = self.client.get(
            "/search",
            params={
                "q": "视频平台资源共享怎么操作",
                "search_mode": "manual_qa",
            },
        ).json()
        citation = payload["answer"]["citations"][0]
        section_id = citation["section_id"]
        response = self.client.get("/manual-detail", params={"section_id": section_id})
        self.assertEqual(response.status_code, 200)
        detail_payload = response.json()
        self.assertTrue(detail_payload["found"])
        self.assertEqual(detail_payload["entry"]["section_id"], section_id)

    def test_manual_original_route_downloads_file(self) -> None:
        payload = self.client.get(
            "/search",
            params={
                "q": "视频平台资源共享怎么操作",
                "search_mode": "manual_qa",
            },
        ).json()
        citation = payload["answer"]["citations"][0]
        section_id = citation["section_id"]
        response = self.client.get(f"/app/manuals/{section_id}/original")
        self.assertEqual(response.status_code, 200)
        self.assertIn("content-disposition", response.headers)

    def test_case_search_returns_case_results_and_reverse_link(self) -> None:
        response = self.client.get(
            "/search",
            params={
                "q": "视频平台客户端登录不上怎么处理",
                "search_mode": "case_search",
                "tag": "客户端登录",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreater(payload["count"], 0)
        self.assertEqual(payload["results"][0]["source_group"], "case")
        self.assertGreater(len(payload["answer"]["citations"]), 0)
        citation = payload["answer"]["citations"][0]
        self.assertIn("detail_url", citation)
        self.assertIn("/case-detail-page?", citation["detail_url"])

    def test_demo_question_case_search(self) -> None:
        response = self.client.get(
            "/search",
            params={
                "q": "10.25.7.158 漏洞怎么跟进",
                "search_mode": "case_search",
                "tag": "漏洞整改",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreater(payload["count"], 0)


    def test_unrelated_query_returns_no_results(self) -> None:
        response = self.client.get(
            "/search",
            params={
                "q": "火星基地怎么部署",
                "search_mode": "manual_qa",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["confidence"]["level"], "low")

    def test_case_timeline_filter(self) -> None:
        response = self.client.get(
            "/case-timeline",
            params={
                "tag": "轮询助手",
                "date_from": "2026-03-01",
                "date_to": "2026-04-10",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreater(payload["count"], 0)
        self.assertIn("轮询助手", payload["entries"][0]["tags"])

    def test_topic_view_and_topic_detail(self) -> None:
        topic_response = self.client.get("/topic-view", params={"tag": "轮询助手"})
        self.assertEqual(topic_response.status_code, 200)
        topic_payload = topic_response.json()
        self.assertGreater(topic_payload["count"], 0)

        detail_response = self.client.get("/topic-detail", params={"tag": "轮询助手"})
        self.assertEqual(detail_response.status_code, 200)
        detail_payload = detail_response.json()
        self.assertGreater(detail_payload["count"], 0)
        self.assertEqual(detail_payload["tag"], "轮询助手")

    def test_case_detail_from_topic_entry(self) -> None:
        topic_detail = self.client.get("/topic-detail", params={"tag": "轮询助手"}).json()
        self.assertGreater(topic_detail["count"], 0)
        section_id = topic_detail["entries"][0]["section_id"]

        case_response = self.client.get("/case-detail", params={"section_id": section_id})
        self.assertEqual(case_response.status_code, 200)
        case_payload = case_response.json()
        self.assertTrue(case_payload["found"])
        self.assertEqual(case_payload["entry"]["section_id"], section_id)

    def test_search_page_contains_case_detail_link(self) -> None:
        response = self.client.get(
            "/search-page",
            params={
                "q": "开发视频平台客户端轮询助手",
                "search_mode": "case_search",
                "tag": "轮询助手",
                "date_from": "2026-04-01",
                "date_to": "2026-04-10",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("/case-detail-page?", response.text)


if __name__ == "__main__":
    unittest.main()
