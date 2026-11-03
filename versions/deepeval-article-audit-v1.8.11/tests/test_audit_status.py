from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_preflight import _id_ranges, audit_status  # noqa: E402


def write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class AuditStatusTests(unittest.TestCase):
    def test_reports_missing_primary_verdict_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = Path(temp)
            work = result / "_work"
            work.mkdir()
            write(result / "A-1-prepared.json", {})
            (work / "A-1-audit-view.md").write_text("view", encoding="utf-8")
            write(work / "first.json", {})
            write(work / "blind.json", {})
            write(work / "frozen.json", {"claims": [{"claim_id": f"C{n:03d}"} for n in range(1, 8)]})
            write(work / "verdicts-1.json", [{"claim_id": "C001"}, {"claim_id": "C002"}, {"claim_id": "C003"}])
            write(work / "verdicts-2.json", {"verdicts": [{"claim_id": "C005"}]})
            status = audit_status(result)
            self.assertEqual(status["article_id"], "A-1")
            self.assertEqual(status["next_stage"], "primary_verdicts")
            row = next(item for item in status["stages"] if item["stage"] == "primary_verdicts")
            self.assertEqual(row["missing"], ["C004", "C006-C007"])

    def test_reports_missing_second_reviews_after_judgments(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = Path(temp)
            work = result / "_work"
            work.mkdir()
            write(result / "A-1-prepared.json", {})
            (work / "A-1-audit-view.md").write_text("view", encoding="utf-8")
            write(work / "first.json", {})
            write(work / "blind.json", {})
            write(work / "frozen.json", {"claims": [{"claim_id": "C001"}, {"claim_id": "C002"}]})
            write(result / "A-1-judgments.json", {"claims": []})
            write(work / "recheck.json", {"claims": [{"claim_id": "C001"}, {"claim_id": "C002"}]})
            write(work / "reviews-1.json", {"reviews": [{"claim_id": "C002"}]})
            status = audit_status(result)
            self.assertEqual(status["next_stage"], "second_reviews")
            row = next(item for item in status["stages"] if item["stage"] == "second_reviews")
            self.assertEqual(row["missing"], ["C001"])

    def test_rendered_result_is_done_and_prints_no_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = Path(temp)
            write(result / "A-1-prepared.json", {"secret": "x"})
            write(result / "A-1-judgments.json", {"reconciliation": {"completed": True}, "claims": []})
            write(result / "A-1-adversarial-review.json", {})
            (result / "faithfulness_summary.md").write_text("summary", encoding="utf-8")
            status = audit_status(result)
            self.assertEqual(status["next_stage"], "done")
            self.assertNotIn("secret", json.dumps(status))

    def test_id_ranges(self) -> None:
        self.assertEqual(_id_ranges(["C001", "C002", "C004", "X"]), ["C001-C002", "C004", "X"])


if __name__ == "__main__":
    unittest.main()
