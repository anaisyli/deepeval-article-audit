import copy
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import test_audit_pipeline as pipeline
from audit_common import extract_knowledge_chunks
from evidence_identity import material_contexts, render_context
from render_article_audit import discover_cases
from audit_compact import _source_files, assemble_judgments, build_view
from audit_preflight import freeze_inventories


class EC1AuditPipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = pipeline.AuditPipelineTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        context = dict(内容提供方="Example Company", 对应对象="Test product",
                       指代关系="Our refers to Example Company.", 核验状态="已核验")
        text = "- 资料视图：写作素材包\n- 证据模板版本：EC-1\n## 一、可直接用于正文的事实\n### Rating\n"
        text += render_context(context) + "#### 证据正文（供Faithfulness核验）\n> Our product is **IP67** certified.\n"
        text += "### Protocol\n" + render_context(context) + "#### 证据正文（供Faithfulness核验）\n> 产品支持双协议。\n"
        text += "## 四、按大纲使用\n无\n"
        self.fixture.knowledge.write_text(text, encoding="utf-8")
        prepared = self.fixture.run_prepare()
        self.prepared = prepared
        self.claims = self.fixture.valid_judgments(prepared)
        contexts = material_contexts(text.splitlines(), str(self.fixture.knowledge.resolve()))
        self.context = contexts[0]
        for claim, block, quote in zip(self.claims["claims"], contexts,
                ("> Our product is **IP67** certified.", "> 产品支持双协议。")):
            claim["evidence"] = [dict(source_file=block["source_file"], line_start=block["evidence_line_start"],
                line_end=block["evidence_line_end"], quote=quote)]

    def ref(self):
        return {key: self.context[key] for key in ("source_file", "line_start", "line_end", "quote")} | {
            "reason": "Our在当前证据块指向已核验来源Example Company，不改变认证事实。"}

    def write(self):
        self.fixture.judgments.write_text(json.dumps(self.claims, ensure_ascii=False), encoding="utf-8")

    def test_renderer_accepts_bound_identity_and_keeps_it_out_of_factual_chunks(self):
        chunks = extract_knowledge_chunks(self.fixture.knowledge)
        self.assertFalse(any("Example Company" in chunk["text"] for chunk in chunks))
        self.claims["claims"][0]["coreference_evidence"] = [self.ref()]
        self.write()
        cases = discover_cases(self.fixture.work)
        self.assertEqual(cases[0]["claims"][0]["verdict"], "supported")

    def test_renderer_rejects_supported_pronoun_without_bound_context(self):
        self.write()
        before = self.fixture.judgments.read_bytes()
        with self.assertRaisesRegex(ValueError, "coreference_evidence"):
            discover_cases(self.fixture.work)
        self.assertEqual(self.fixture.judgments.read_bytes(), before)

    def test_unknown_subject_continues_without_fabricated_coreference(self):
        for claim in self.claims["claims"]:
            claim["semantic_status"] = "entailed" if claim["verdict"] == "supported" else "unknown"
        self.claims["claims"][0].update(verdict="unsupported", semantic_status="unknown")
        self.write()
        cases = discover_cases(self.fixture.work)
        self.assertEqual(cases[0]["claims"][0]["verdict"], "unsupported")

    def test_render_fills_a_missing_record_for_evidence_added_after_assembly(self):
        # Evidence a reviewer added reaches the claim at check, after assembly attached EC-1 records
        # (P-005-ART-20261022-002 C060); render fills the mechanical record instead of failing.
        for claim in self.claims["claims"]:
            claim["semantic_status"] = "entailed" if claim["verdict"] == "supported" else "unknown"
        self.write()
        cases = discover_cases(self.fixture.work)
        self.assertEqual(cases[0]["claims"][0]["coreference_evidence"][0]["quote"], self.context["quote"])
        self.assertNotIn("coreference_evidence", json.loads(self.fixture.judgments.read_text(encoding="utf-8"))["claims"][0])
        discover_cases(self.fixture.work, persist_coreference=True)
        saved = json.loads(self.fixture.judgments.read_text(encoding="utf-8"))["claims"][0]
        self.assertEqual(saved["coreference_evidence"][0]["line_start"], self.context["line_start"])

    def compact_passes(self):
        claimed = {c["unit_id"] for c in self.claims["claims"]}
        rows = [dict(unit_id=u["unit_id"], classification="non_factual", category="transition",
                     reason="该单元只承上启下，不陈述可核验关系。") for u in self.prepared["article_units"]
                if u["unit_id"] not in claimed and u.get("unit_type") not in {"title", "heading", "table_header"}]
        inventory = [dict(unit_id=c["unit_id"], article_quote=c["article_quote"], claim=c["claim"])
                     for c in self.claims["claims"]]
        inventory[2]["derivation_note"] = "“另有”承接前句主语“产品”，补全主体后断言三年质保。"
        return dict(coverage_review=dict(unit_classifications=rows), claim_inventory=inventory)

    def test_assembly_fills_ec1_records_the_model_never_writes(self):
        passes = self.compact_passes()
        frozen = freeze_inventories(self.prepared, copy.deepcopy(passes), copy.deepcopy(passes))
        by_claim = {c["claim"]: c for c in self.claims["claims"]}
        verdicts = []
        for frozen_claim in frozen["claims"]:
            ref = by_claim[frozen_claim["claim"]]
            verdicts.append(dict(claim_id=frozen_claim["claim_id"], reason=ref["reason"],
                                 semantic_status="entailed" if ref["verdict"] == "supported" else "unknown",
                                 evidence=[dict(quote=e["quote"]) for e in ref["evidence"]]))  # no lines, no file
        verdicts[0]["coreference_evidence"] = [dict(quote="hand-copied, wrong")]  # replaced by the script
        claims = assemble_judgments(self.prepared, frozen, passes, [verdicts], passes)["claims"]
        ip67 = next(c for c in claims if "IP67" in c["claim"])
        record = ip67["coreference_evidence"][0]
        # The block spans one line more than its four shown lines and its quote ends with a newline.
        self.assertEqual({k: record[k] for k in ("line_start", "line_end", "quote")},
                         {k: self.context[k] for k in ("line_start", "line_end", "quote")})
        self.assertIn("Our refers to Example Company", record["reason"])
        self.assertEqual(ip67["evidence"][0]["line_start"], self.context["evidence_line_start"])
        self.assertNotIn("coreference_evidence", next(c for c in claims if "双协议" in c["claim"]))  # no pronoun

    def test_view_block_numbers_resolve_as_source_file(self):
        blocks = re.findall(r"### (K\d{4}) · F1 L(\d+)–(\d+)", build_view(self.prepared))
        start = self.context["evidence_line_start"]
        ip67 = next(k for k, a, b in blocks if int(a) <= start <= int(b))
        other = next(k for k, a, b in blocks if k != ip67)
        quote = "> Our product is **IP67** certified."
        item = _source_files(self.prepared, [dict(source_file=ip67, quote=quote)], "C001", supported=True)[0]
        self.assertEqual(item["source_file"], self.prepared["knowledge_files"][0])
        self.assertEqual((item["line_start"], item["line_end"]), (start, start))
        with self.assertRaisesRegex(ValueError, f"not inside {other}"):
            _source_files(self.prepared, [dict(source_file=other, quote=quote)], "C001", supported=True)
        with self.assertRaisesRegex(ValueError, "K9999 is not an evidence block"):
            _source_files(self.prepared, [dict(source_file="K9999", quote=quote)], "C001")
        # A supported claim may cite only allowed evidence; other rows just get a span where the quote is.
        with self.assertRaisesRegex(ValueError, "outside the allowed evidence"):
            _source_files(self.prepared, [dict(quote="## 四、按大纲使用")], "C001", supported=True)
        self.assertIsInstance(_source_files(self.prepared, [dict(quote="## 四、按大纲使用")], "C001")[0]["line_start"], int)


if __name__ == "__main__":
    unittest.main()
