"""Subject quoted from the bound EC-1 block, and reopening a review that failed only on a record.

P-005-ART-20261029-002 (2026-10-30): C055/C090/C094 were entailed by both passes from "We can
manufacture …"; the reviewer quoted the subject "JERL Packaging" from the EC-1 block and reconciliation
downgraded all three.  The only way back was `reset`, re-judging all 105 claims.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from audit_preflight import reopen_review, reopened_review_errors  # noqa: E402
from reconcile_article_audit import reconcile_files, reconcile_payloads  # noqa: E402
from test_reconciliation import base_claim, check, review_row  # noqa: E402

CLAIM = "JERL Packaging's customization scope covers size and logo file."
EVIDENCE = "We can manufacture custom foldable boxes according to your required size and logo file."
CONTEXT = "- 内容提供方：JERL Packaging\n- 对应对象：Foldable box OEM service\n- 指代关系：we指JERL Packaging\n- 核验状态：已核验\n"


def claim(claim_id: str = "C001", *, coreference: bool = True) -> dict:
    item = base_claim(claim_id, CLAIM, EVIDENCE)
    if coreference:
        item["coreference_evidence"] = [{"quote": CONTEXT, "line_start": 10, "line_end": 14,
                                         "reason": "按EC-1归属解析证据中的指代：we指JERL Packaging"}]
    return item


def row(claim_id: str = "C001", subject: str = "JERL Packaging") -> dict:
    item = review_row(claim_id, CLAIM, EVIDENCE)
    item["entailment_checks"] = {
        "subject_object": check("covered", "JERL Packaging", subject),
        "predicate_relation": check("covered", "covers", "can manufacture custom foldable boxes according to"),
        "scope_condition": check("covered", "size and logo file", "size and logo file"),
        "quantity_time_version": check("not_applicable"),
        "causal_effect": check("not_applicable"),
    }
    item["challenge_reason"] = "证据中的We按同一证据块的EC-1归属指JERL Packaging，定制范围逐项一致。"
    return item


def payloads(claims: list[dict], rows: list[dict]) -> tuple[dict, dict]:
    return ({"schema_version": "1.1", "evaluation_protocol_version": "2.0", "article_id": "ART-001", "claims": claims},
            {"schema_version": "1.0", "evaluation_protocol_version": "2.0", "article_id": "ART-001",
             "review_mode": "independent_adversarial", "claim_reviews": rows})


class SubjectFromCoreferenceTests(unittest.TestCase):
    def status(self, item: dict, review: dict) -> str:
        judgments, adversarial = payloads([item], [review])
        reconcile_payloads(judgments, adversarial)
        return judgments["claims"][0]["semantic_status"]

    def test_subject_quoted_from_the_bound_ec1_block_is_support(self):
        self.assertEqual(self.status(claim(), row()), "entailed")

    def test_subject_quoted_as_the_evidence_pronoun_is_support(self):
        self.assertEqual(self.status(claim(), row(subject="We")), "entailed")

    def test_subject_without_a_bound_block_stays_unknown(self):
        self.assertEqual(self.status(claim(coreference=False), row()), "unknown")

    def test_other_dimensions_may_not_quote_the_ec1_block(self):
        review = row()
        review["entailment_checks"]["predicate_relation"] = check("covered", "covers", "内容提供方：JERL Packaging")
        self.assertEqual(self.status(claim(), review), "unknown")


class ReopenReviewTests(unittest.TestCase):
    """A reconciled result whose C002 failed only on a record; C003 is a real disagreement."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "P-T" / "ART-001" / "v1"
        (self.dir / "_work").mkdir(parents=True)
        good, bad, disagree = claim("C001"), claim("C002"), claim("C003")
        for item in (bad, disagree):
            item["unit_id"] = item["claim_id"].replace("C", "U")
        rows = [row("C001"), row("C002"), row("C003")]
        rows[1]["unit_id"], rows[2]["unit_id"] = "U002", "U003"
        for number, item in enumerate(rows, 1):  # one stamped reason on several rows is itself a downgrade
            item["challenge_reason"] += f"（第{number}处正文）"
        rows[1]["entailment_checks"]["scope_condition"] = check("covered", "size and logo file", "not in the evidence")
        rows[2]["independent_semantic_status"] = rows[2]["semantic_status"] = "unknown"
        judgments, adversarial = payloads([good, bad, disagree], rows)
        self.judgments = self.dir / "ART-001-judgments.json"
        self.review = self.dir / "ART-001-adversarial-review.json"
        self.judgments.write_text(json.dumps(judgments, ensure_ascii=False), encoding="utf-8")
        self.review.write_text(json.dumps(adversarial, ensure_ascii=False), encoding="utf-8")
        reconcile_files(self.judgments, self.review, prepared_path=self.dir / "ART-001-prepared.json")
        (self.dir / "faithfulness_summary.md").write_text("rendered", encoding="utf-8")

    def statuses(self) -> dict:
        return {c["claim_id"]: c["semantic_status"]
                for c in json.loads(self.judgments.read_text(encoding="utf-8"))["claims"]}

    def test_reconciliation_keeps_the_unreconciled_records(self):
        self.assertEqual(self.statuses(), {"C001": "entailed", "C002": "unknown", "C003": "unknown"})
        saved = json.loads((self.dir / "_work/pre-reconcile/ART-001-judgments.json").read_text(encoding="utf-8"))
        self.assertNotIn("reconciliation", saved)

    def test_reopen_restores_only_the_record_only_downgrade(self):
        result = reopen_review(self.dir, "ART-001")
        self.assertEqual(result["reopened_claim_ids"], ["C002"])  # C003 disagreed: a conclusion, not reopened
        restored = json.loads(self.judgments.read_text(encoding="utf-8"))
        self.assertNotIn("reconciliation", restored)
        self.assertTrue((Path(result["archived"]) / "faithfulness_summary.md").is_file())
        self.assertFalse((self.dir / "faithfulness_summary.md").exists())

    def test_reopened_review_may_fix_only_that_claim_and_keep_its_conclusion(self):
        reopen_review(self.dir, "ART-001")
        adversarial = json.loads(self.review.read_text(encoding="utf-8"))
        fixed = copy.deepcopy(adversarial)
        fixed["claim_reviews"][1]["entailment_checks"]["scope_condition"] = check(
            "covered", "size and logo file", "size and logo file")
        self.assertEqual(reopened_review_errors(self.dir, fixed), [])
        tampered = copy.deepcopy(fixed)
        tampered["claim_reviews"][2]["independent_semantic_status"] = "entailed"
        self.assertTrue(any("C003" in e for e in reopened_review_errors(self.dir, tampered)))
        flipped = copy.deepcopy(fixed)
        flipped["claim_reviews"][1]["independent_semantic_status"] = "unknown"
        self.assertTrue(any("保持entailed" in e for e in reopened_review_errors(self.dir, flipped)))
        # The corrected record reconciles to support; nothing else moved.
        self.review.write_text(json.dumps(fixed, ensure_ascii=False), encoding="utf-8")
        reconcile_files(self.judgments, self.review, prepared_path=self.dir / "ART-001-prepared.json")
        self.assertEqual(self.statuses(), {"C001": "entailed", "C002": "entailed", "C003": "unknown"})

    def test_render_keeps_the_work_directory_while_a_claim_can_be_reopened(self):
        # A successful managed render deletes _work, which holds pre-reconcile/ and the review files.
        from render_article_audit import reopenable_claims
        self.assertEqual(reopenable_claims(self.dir), ["C002"])
        reopen_review(self.dir, "ART-001")
        reconcile_files(self.judgments, self.review, prepared_path=self.dir / "ART-001-prepared.json")
        self.assertEqual(reopenable_claims(self.dir), [])  # one reopen per result: render may clean up

    def test_reopen_is_refused_twice_and_after_import(self):
        reopen_review(self.dir, "ART-001")
        reconcile_files(self.judgments, self.review, prepared_path=self.dir / "ART-001-prepared.json")
        with self.assertRaisesRegex(ValueError, "已重开过一次"):
            reopen_review(self.dir, "ART-001")
        other = Path(self.tmp.name) / "imported"
        (other / "_work").mkdir(parents=True)
        (other / "article_completed.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "已导入知识库"):
            reopen_review(other, "ART-001")


if __name__ == "__main__":
    unittest.main()
