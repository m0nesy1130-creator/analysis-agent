from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from app.directories import DirectoryInputError, load_directory
from app.documents import load_evidence, search_documents


class DirectoryTest(TestCase):
    def test_recursion_preserves_distinct_references_and_skipped_counts(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            quote = root / "报价.md"
            quote.write_text("# 报价\n年费 12,000 元。", encoding="utf-8")
            (root / "图片.png").touch()
            (root / "无效编码.txt").write_bytes(b"\xff")
            child = root / "附件"
            child.mkdir()
            services = child / "服务.txt"
            services.write_text("# 服务\n费用包含巡检。", encoding="utf-8")

            source, rows = load_directory(f'"{root}"')
            self.assertEqual(source["file_count"], 1)
            self.assertEqual(source["skipped"], {"不支持的格式": 1, "非 UTF-8": 1})
            self.assertEqual(search_documents("巡检", "", rows, hybrid=False), ())

            source, rows = load_directory(str(root), recursive=True)
            self.assertEqual(source["file_count"], 2)
            self.assertTrue(source["recursive"])
            fee, = search_documents("年费", "", rows, hybrid=False)
            service, = search_documents("巡检", "", rows, hybrid=False)
            self.assertEqual((fee.evidence_id, service.evidence_id), ("D1-L2", "D2-L2"))
            self.assertEqual((fee.source, service.source), (str(quote.resolve()), str(services.resolve())))
            self.assertEqual(service.version, load_evidence(services)[1].version)

    def test_missing_or_oversized_scope_fails_instead_of_returning_partial_documents(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(DirectoryInputError, "没有可读取"):
                load_directory(str(root))
            (root / "test.txt").write_text("第一行\n第二行", encoding="utf-8")
            with patch("app.directories.MAX_DIRECTORY_LINES", 1):
                with self.assertRaisesRegex(DirectoryInputError, "有效文本"):
                    load_directory(str(root))
        with self.assertRaisesRegex(DirectoryInputError, "绝对路径"):
            load_directory("相对路径")
