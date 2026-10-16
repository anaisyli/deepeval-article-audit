import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import test_audit_pipeline as pipeline
from audit_common import extract_knowledge_chunks
from evidence_identity import material_contexts, render_context
from render_article_audit import discover_cases


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


if __name__ == "__main__":
    unittest.main()
