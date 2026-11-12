from unittest import TestCase
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

from app.checkpoints import checkpoint_serializer
from app.context_budget import fit_context, keep_visible_references
from app.contracts import Evidence
from app.database_query import compile_query
from app.documents import evidence_from_text
from app.graphs import build_graph
from app.tasks import initial_state, report_from_state
from app.web_search import WebSourceError, fetch_public


class AdaptiveModel:
    plan_calls = 0

    def complete_json(self, _instructions, data):
        if data.get("phase") == "plan":
            return {"tool": "finish"} if data["observations"] else {"tool": "document", "query": "环境"}
        if data.get("phase") == "resolve":
            if data["feedback"]:
                return {"tool": "open_url", "url": "https://example.com/docs", "query": "strict missing"}
            return {"tool": "document", "query": "strict"}
        if "allowed_tools" in data:
            self.plan_calls += 1
            return {"steps": [{"id": "s1", "question": "strict 模式下会怎样？", "depends_on": [],
                               "tool": "document", "queries": ["strict"]}]}
        if "cited_ids" in data:
            if not data["claim"]:
                return {"status": "insufficient", "note": "缺少官方的严格模式说明。"}
            return {"status": "supported", "note": "公开原文直接支持。", "rejected": [
                {"direction": "静默返回", "reason": "文档写明会抛出异常。", "id": "W-s1", "quote": "FileNotFoundError is raised"},
                {"direction": "伪造方向", "reason": "测试过滤", "id": "W-s1", "quote": "unsupported content"},
            ]}
        if any(row["id"] == "W-s1" for row in data["evidence"]):
            return {"claim": "路径不存在时抛出 FileNotFoundError。", "evidence_ids": ["W-s1"]}
        return {"claim": None}


class AdaptiveTest(TestCase):
    def test_switches_source_after_feedback_and_cites_valid_rejection(self):
        document = evidence_from_text("项目运行环境为 Python 3.12。", "本地测试")
        page = Evidence("W-s1", "https://example.com/docs", None, "FileNotFoundError is raised when strict is true.",
                        "test-hash", "网页段落 1", "测试文档", retrieved_at="2026-11-12")
        model = AdaptiveModel()
        graph = build_graph(model, checkpointer=InMemorySaver(serde=checkpoint_serializer()))
        with patch("app.adaptive.web_evidence", return_value=((page,), "找到一段原文。")):
            state = graph.invoke(initial_state(
                "核对 strict", document, mode="live", hybrid=False, project=None, web_enabled=True,
            ), {"configurable": {"thread_id": "adaptive-test"}})
        self.assertEqual(model.plan_calls, 1)
        self.assertEqual([(row["phase"], row["action"]["tool"]) for row in state["tool_observations"]], [
            ("plan", "document"), ("resolve", "document"), ("resolve", "open_url"),
        ])
        report = report_from_state(state)
        self.assertIn("已排除的分析方向", report)
        self.assertIn("[查看原文](<https://example.com/docs>)", report)
        self.assertNotIn("伪造方向", report)

    def test_public_web_rejects_loopback_and_nonstandard_schemes(self):
        for url in ("http://127.0.0.1/", "http://[::1]/", "file:///C:/test.txt", "https://example.com:8080/"):
            with self.subTest(url=url), self.assertRaises(WebSourceError):
                fetch_public(url)

    def test_sql_binds_values_and_rejects_columns_outside_scope(self):
        source = {"table": "service_tickets", "key": "ticket_id"}
        fields = {"ticket_id": "varchar", "status": "varchar"}
        sql, parameters, output = compile_query(source, fields, {
            "columns": ["status"], "filters": [{"column": "status", "op": "eq", "value": "O'Connor"}],
        })
        self.assertIn("WHERE `status` = %s", sql)
        self.assertNotIn("O'Connor", sql)
        self.assertEqual(parameters, ("O'Connor",))
        self.assertEqual(output, ("ticket_id", "status"))
        with self.assertRaisesRegex(ValueError, "授权"):
            compile_query(source, fields, {"columns": ["other_field"]})

    def test_context_keeps_cited_excerpt_and_rejects_unseen_references(self):
        data = {"question": "收费多少？", "cited_ids": ["L2"], "evidence": [
            {"id": "L1", "text": "无关内容" * 1_000}, {"id": "L2", "text": "收费 12,000 元。"},
        ]}
        with patch("app.context_budget._rank", side_effect=lambda _question, rows: rows):
            visible, used = fit_context("复核", data, len, budget=260)
        self.assertLessEqual(used, 260)
        self.assertEqual(visible["evidence"][0], data["evidence"][1])
        self.assertEqual(keep_visible_references({"evidence_ids": ["L2", "L1"]}, visible)["evidence_ids"], [])
