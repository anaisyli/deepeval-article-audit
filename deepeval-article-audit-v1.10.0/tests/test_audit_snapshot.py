import sys
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_common import article_content_sha256, extract_article, extract_knowledge_chunks, is_table_rule, public_chunk
from audit_compact import build_view
from audit_snapshot import parsed_input_conflicts
from evidence_identity import contexts_for_files
from prepare_article_audit import sha256_file


class AuditSnapshotTests(unittest.TestCase):
    def test_table_rule_accepts_optional_outer_pipes(self):
        self.assertTrue(is_table_rule("| --- | --- |"))
        self.assertTrue(is_table_rule("---|---"))
        self.assertTrue(is_table_rule("---|---|"))

    def test_serialization_only_changes_keep_the_parsed_snapshot(self):
        root = ROOT / f".test-audit-snapshot-{uuid4().hex}"
        root.mkdir()
        self.addCleanup(shutil.rmtree, root)
        article = root / "40_最终文章.md"
        knowledge = root / "30_本篇知识库资料.md"
        article.write_bytes("# Drawer Guide\r\nThe tray holds rings.\r\n".encode("utf-8"))
        knowledge.write_bytes(
            "# Source\r\n| Option | Material |\r\n| --- | --- |\r\n| Tray | EVA |\r\n".encode("utf-8")
        )

        parsed_article = extract_article(article)
        chunks = extract_knowledge_chunks(knowledge)
        for index, chunk in enumerate(chunks, 1):
            chunk["chunk_id"] = f"K{index:04d}"
        prepared = {
            "article_id": "ART-001",
            "article_version": "v1",
            "article_title": parsed_article["title"],
            "article_file": str(article.resolve()),
            "article_sha256": sha256_file(article),
            "article_content_sha256": article_content_sha256(parsed_article["article_lines"]),
            "article_lines": parsed_article["article_lines"],
            "article_units": [dict(unit, candidate_evidence=[]) for unit in parsed_article["article_units"]],
            "knowledge_files": [str(knowledge.resolve())],
            "knowledge_sha256": [sha256_file(knowledge)],
            "knowledge_chunks": [public_chunk(chunk) for chunk in chunks],
            "identity_contexts": contexts_for_files([knowledge]),
        }
        view = root / "ART-001-audit-view.md"
        view.write_text(build_view(prepared), encoding="utf-8")

        article.write_bytes("# Drawer Guide\nThe tray holds rings.\n".encode("utf-8"))
        knowledge.write_bytes(
            "# Source\nOption | Material\n---|---\nTray | EVA\n".encode("utf-8")
        )
        self.assertNotEqual(sha256_file(article), prepared["article_sha256"])
        self.assertNotEqual(sha256_file(knowledge), prepared["knowledge_sha256"][0])
        self.assertEqual(parsed_input_conflicts(prepared, view_path=view), [])

        article.write_text("# Drawer Guide\nThe tray has four compartments.\n", encoding="utf-8")
        conflicts = parsed_input_conflicts(prepared, view_path=view)
        self.assertTrue(any("40_最终文章.md" in item for item in conflicts))

        article.write_bytes("# Drawer Guide\nThe tray holds rings.\n".encode("utf-8"))
        knowledge.write_text(
            "# Source\nOption | Material\n---|---\nTray | PE foam\n", encoding="utf-8"
        )
        conflicts = parsed_input_conflicts(prepared, view_path=view)
        self.assertTrue(any("30_本篇知识库资料.md" in item for item in conflicts))


if __name__ == "__main__":
    unittest.main()
