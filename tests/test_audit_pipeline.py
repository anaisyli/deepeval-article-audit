from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from manage_handoff_contract import load_contract, locate_contract  # noqa: E402
from render_article_audit import MODE, discover_cases  # noqa: E402


_, _HANDOFF_CONTRACT = load_contract()
HANDOFF_VERSION = str(_HANDOFF_CONTRACT["handoff_contract_version"])


ARTICLE_TEXT = """# 最终文章

- 文章ID：ART-V05-001
- 文章版本：v1
- 模板版本：v0.6-20260907.1
- 文章标题：Product Guide
- 完成日期：2026-08-19

## 最终正文

<!-- ARTICLE_BODY_START -->
# Product | Guide

**IP67** certified.
产品支持双协议。另有三年质保。
Which option fits your project?
<!-- ARTICLE_BODY_END -->
"""

KNOWLEDGE_TEXT = """# 本篇写作素材包

- 文章ID：ART-V05-001
- 文章版本：v1
- 资料视图：写作素材包
- 资料版本：v1
- 生成日期：2026-08-19
- 目标语言：English
- 对应大纲：[[10_文章知识需求.md#大纲]]
- 使用对象：写作流程；本文件为唯一写作事实输入。

## 一、可直接用于正文的事实

### Product facts

#### 事实素材：IP rating

- 可直接采用的事实：The product is IP67 certified.
- 使用条件：测试型号。

##### 证据正文（供Faithfulness核验）

> **IP67** certified.

#### 事实素材：Protocol support

- 可直接采用的事实：产品支持双协议。
- 使用条件：测试型号。

##### 证据正文（供Faithfulness核验）

> 产品支持双协议。

## 二、可直接采用的英文表达

无

## 三、可使用的数据表

无

## 四、按大纲使用

- Use the verified product facts where relevant.

## 五、仅供生成控制（不得写入正文）

- 产品提供三年质保。

## 六、缺少资料的章节及建议处理方式

- 质保期限仍待确认。
"""


def line_number(text: str, exact_line: str) -> int:
    return text.splitlines().index(exact_line) + 1


class AuditPipelineTests(unittest.TestCase):
    def test_managed_skill_contract_accepts_direct_and_nested_install_layouts(self) -> None:
        contract_text = json.dumps(_HANDOFF_CONTRACT, ensure_ascii=False, indent=2)
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            direct = base / "direct-skill"
            (direct / "references").mkdir(parents=True)
            (direct / "SKILL.md").write_text(
                "---\nname: manage-article-knowledge\n---\n", encoding="utf-8"
            )
            (direct / "references" / "handoff-contract.json").write_text(
                contract_text, encoding="utf-8"
            )
            nested = base / "repository"
            nested_root = nested / "manage-article-knowledge-v0.6"
            (nested_root / "references").mkdir(parents=True)
            (nested_root / "SKILL.md").write_text(
                "---\nname: manage-article-knowledge\n---\n", encoding="utf-8"
            )
            (nested_root / "references" / "handoff-contract.json").write_text(
                contract_text, encoding="utf-8"
            )

            self.assertEqual(
                locate_contract(direct),
                (direct / "references" / "handoff-contract.json").resolve(),
            )
            self.assertEqual(
                locate_contract(direct / "SKILL.md"),
                (direct / "references" / "handoff-contract.json").resolve(),
            )
            self.assertEqual(
                locate_contract(nested),
                (nested_root / "references" / "handoff-contract.json").resolve(),
            )

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.article = self.root / "40_最终文章.md"
        self.knowledge = self.root / "30_本篇知识库资料.md"
        self.work = self.root / "work"
        self.output = self.root / "output"
        self.managed = self.root / "managed-result" / "TEST-PROJECT" / "ART-V05-001" / "v1"
        self.article.write_text(ARTICLE_TEXT, encoding="utf-8")
        self.knowledge.write_text(KNOWLEDGE_TEXT, encoding="utf-8")
        checksum = hashlib.sha256(self.knowledge.read_bytes()).hexdigest()
        ip_line = line_number(KNOWLEDGE_TEXT, "> **IP67** certified.")
        protocol_line = line_number(KNOWLEDGE_TEXT, "> 产品支持双协议。")
        (self.root / "35_写作素材来源索引.md").write_text(
            "\n".join(
                [
                    "# 写作素材来源索引",
                    "",
                    "- 文章ID：ART-V05-001",
                    "- 文章版本：v1",
                    "- 索引版本：v1",
                    "- 生成日期：2026-08-19",
                    "- 对应写作素材：[[30_本篇知识库资料.md]]",
                    f"- 写作素材SHA-256：{checksum}",
                    "- 用途：仅供内部追溯与Faithfulness映射；不得交给写作模型。",
                    "",
                    "## 写作素材到正式知识映射",
                    "",
                    "| 证据正文行开始 | 证据正文行结束 | 素材主题 | 正式Claim ID | Claim通俗标题 | 正式知识文件 | 原始来源与精确位置 |",
                    "|---:|---:|---|---|---|---|---|",
                    f"| {ip_line} | {ip_line} | IP rating | CLM-TEST-PRODUCT-001 | IP67认证 | [[product.md]] | product.pdf p.1 |",
                    f"| {ip_line} | {ip_line} | Product scope | CLM-TEST-PRODUCT-003 | 测试产品范围 | [[product.md]] | product.pdf p.1 |",
                    f"| {protocol_line} | {protocol_line} | Protocol | CLM-TEST-PRODUCT-002 | 双协议支持 | [[product.md]] | product.pdf p.2 |",
                    "",
                    "## 写作事实输入确认",
                    "",
                    "- 唯一事实输入：[[30_本篇知识库资料.md]]",
                    "- 其他事实附件：无；随文事实文件必须先进入来源层、Formal Claim和当前30/35。",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.work.mkdir()
        self.prepared = self.work / "ART-V05-001-prepared.json"
        self.judgments = self.work / "ART-V05-001-judgments.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_prepare(self) -> dict:
        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "prepare_article_audit.py"),
                "--article",
                str(self.article),
                "--knowledge",
                str(self.knowledge),
                "--output",
                str(self.prepared),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(self.prepared.read_text(encoding="utf-8"))

    def run_prepare_managed(self) -> dict:
        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "prepare_article_audit.py"),
                "--article",
                str(self.article),
                "--knowledge",
                str(self.knowledge),
                "--article-id",
                "ART-V05-001",
                "--article-version",
                "v1",
                "--project-id",
                "TEST-PROJECT",
                "--handoff-contract-version",
                HANDOFF_VERSION,
                "--result-dir",
                str(self.managed),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(
            (self.managed / "ART-V05-001-prepared.json").read_text(encoding="utf-8")
        )

    def valid_judgments(self, prepared: dict) -> dict:
        units = {item["text"]: item for item in prepared["article_units"]}
        return {
            "schema_version": "1.0",
            "evaluation_mode": MODE,
            "article_id": "ART-V05-001",
            "claims": [
                {
                    "claim_id": "C001",
                    "unit_id": units["IP67 certified."]["unit_id"],
                    "article_line": units["IP67 certified."]["line"],
                    "article_quote": "**IP67** certified.",
                    "claim": "The product is IP67 certified.",
                    "verdict": "supported",
                    "reason": "知识资料中的Claim和最小原文证据直接支持该事实。",
                    "evidence": [
                        {
                            "source_file": str(self.knowledge.resolve()),
                            "line_start": line_number(KNOWLEDGE_TEXT, "> **IP67** certified."),
                            "line_end": line_number(KNOWLEDGE_TEXT, "> **IP67** certified."),
                            "quote": "> **IP67** certified.",
                        }
                    ],
                },
                {
                    "claim_id": "C002",
                    "unit_id": units["产品支持双协议。"]["unit_id"],
                    "article_line": units["产品支持双协议。"]["line"],
                    "article_quote": "产品支持双协议。",
                    "claim": "产品支持双协议。",
                    "verdict": "supported",
                    "reason": "知识资料直接陈述相同事实。",
                    "evidence": [
                        {
                            "source_file": str(self.knowledge.resolve()),
                            "line_start": line_number(KNOWLEDGE_TEXT, "> 产品支持双协议。"),
                            "line_end": line_number(KNOWLEDGE_TEXT, "> 产品支持双协议。"),
                            "quote": "> 产品支持双协议。",
                        }
                    ],
                },
                {
                    "claim_id": "C003",
                    "unit_id": units["另有三年质保。"]["unit_id"],
                    "article_line": units["另有三年质保。"]["line"],
                    "article_quote": "另有三年质保。",
                    "claim": "产品提供三年质保。",
                    "verdict": "unsupported",
                    "reason": "该内容位于不应写入区段，不能作为正向支持证据。",
                    "evidence": [],
                },
            ],
        }

    def test_v05_pipeline_preserves_short_and_chinese_units(self) -> None:
        prepared = self.run_prepare()
        self.assertEqual(prepared["article_id"], "ART-V05-001")
        unit_texts = [item["text"] for item in prepared["article_units"]]
        self.assertIn("IP67 certified.", unit_texts)
        self.assertIn("产品支持双协议。", unit_texts)
        self.assertIn("另有三年质保。", unit_texts)

        blocked_line = line_number(KNOWLEDGE_TEXT, "- 产品提供三年质保。")
        candidate_lines = {
            candidate["line_start"]
            for unit in prepared["article_units"]
            for candidate in unit.get("candidate_evidence", [])
        }
        self.assertNotIn(blocked_line, candidate_lines)
        self.assertFalse(
            any(chunk["line_start"] == blocked_line for chunk in prepared["knowledge_chunks"]),
            "Control sections must not enter the prepared evidence context.",
        )

        self.judgments.write_text(
            json.dumps(self.valid_judgments(prepared), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "render_article_audit.py"),
                "--input-dir",
                str(self.work),
                "--output-dir",
                str(self.output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        summary = (self.output / "faithfulness_summary.md").read_text(encoding="utf-8")
        self.assertIn("2 | 3 | 1 | 66.67%", summary)
        self.assertIn("Product Guide", summary)
        self.assertIn("内容单元诊断（不改变上方主指标）", summary)
        self.assertIn("事实内容占比", summary)
        detail = (self.output / "ART-V05-001_faithfulness_details.md").read_text(encoding="utf-8")
        self.assertIn("## 内容单元识别", detail)
        self.assertIn("非事实，不计入", detail)
        highlight = (self.output / "faithfulness_highlight.html").read_text(encoding="utf-8")
        self.assertIn("只看非事实", highlight)
        self.assertIn("data-unit-id=\"U003\"", highlight)
        self.assertIn("class=\"article-line article-unit fully-supported\"", highlight)
        self.assertIn("class=\"article-line article-unit unsupported\"", highlight)

    def test_v06_managed_run_uses_one_version_directory(self) -> None:
        prepared = self.run_prepare_managed()
        self.assertEqual(prepared["integration_mode"], "manage-article-knowledge-v0.6")
        self.assertEqual(prepared["project_id"], "TEST-PROJECT")
        self.assertEqual(prepared["handoff_contract_version"], HANDOFF_VERSION)
        self.assertEqual(prepared["article_version"], "v1")
        self.assertEqual(len(prepared["knowledge_files"]), 1)
        managed_judgments = self.managed / "ART-V05-001-judgments.json"
        managed_judgments.write_text(
            json.dumps(self.valid_judgments(prepared), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "render_article_audit.py"),
                "--result-dir",
                str(self.managed),
                "--article-id",
                "ART-V05-001",
                "--article-version",
                "v1",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertTrue((self.managed / "faithfulness_summary.md").is_file())
        self.assertTrue((self.managed / "faithfulness_highlight.html").is_file())

    def test_v06_managed_run_rejects_version_mismatch(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "prepare_article_audit.py"),
                "--article",
                str(self.article),
                "--knowledge",
                str(self.knowledge),
                "--article-version",
                "v2",
                "--project-id",
                "TEST-PROJECT",
                "--handoff-contract-version",
                HANDOFF_VERSION,
                "--result-dir",
                str(self.managed),
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicts with article metadata version", result.stderr + result.stdout)

    def test_v06_managed_run_rejects_unreviewed_low_factual_coverage(self) -> None:
        body = "\n".join(f"Feature {index} has a documented operating condition." for index in range(1, 26))
        self.article.write_text(
            "# 最终文章\n\n"
            "- 文章ID：ART-V05-001\n"
            "- 文章版本：v1\n"
            "- 文章标题：Coverage Test\n\n"
            "<!-- ARTICLE_BODY_START -->\n"
            f"{body}\n"
            "<!-- ARTICLE_BODY_END -->\n",
            encoding="utf-8",
        )
        prepared = self.run_prepare_managed()
        self.assertGreaterEqual(len(prepared["article_units"]), 20)
        managed_judgments = self.managed / "ART-V05-001-judgments.json"
        managed_judgments.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "evaluation_mode": MODE,
                    "article_id": "ART-V05-001",
                    "claims": [],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "complete coverage_review second pass is required"):
            discover_cases(self.managed)

    def test_v06_managed_run_rejects_incompatible_handoff_contract(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "prepare_article_audit.py"),
                "--article",
                str(self.article),
                "--knowledge",
                str(self.knowledge),
                "--article-id",
                "ART-V05-001",
                "--article-version",
                "v1",
                "--project-id",
                "TEST-PROJECT",
                "--handoff-contract-version",
                "MAK-HANDOFF-99.0",
                "--result-dir",
                str(self.managed),
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported_contract_version", result.stderr + result.stdout)

    def test_v06_managed_renderer_rejects_existing_summary(self) -> None:
        prepared = self.run_prepare_managed()
        (self.managed / "ART-V05-001-judgments.json").write_text(
            json.dumps(self.valid_judgments(prepared), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.managed / "faithfulness_summary.md").write_text("existing\n", encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "render_article_audit.py"),
                "--result-dir",
                str(self.managed),
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to overwrite existing managed summary", result.stderr + result.stdout)

    def test_renderer_rejects_normalized_only_article_quote(self) -> None:
        prepared = self.run_prepare()
        judgments = self.valid_judgments(prepared)
        judgments["claims"][0]["article_quote"] = "IP67 certified."
        self.judgments.write_text(
            json.dumps(judgments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "exact substring"):
            discover_cases(self.work)

    def test_legacy_body_heading_excludes_managed_metadata(self) -> None:
        legacy = ARTICLE_TEXT.replace("- 模板版本：v0.6-20260907.1\n", "")
        legacy = legacy.replace("<!-- ARTICLE_BODY_START -->\n", "").replace("<!-- ARTICLE_BODY_END -->\n", "")
        self.article.write_text(legacy.replace("## 最终正文", "## 正文"), encoding="utf-8")
        prepared = self.run_prepare()
        texts = [unit["text"] for unit in prepared["article_units"]]
        self.assertFalse(any(text.startswith("文章ID") for text in texts))
        self.assertFalse(any(text.startswith("文章版本") for text in texts))
        self.assertIn("IP67 certified.", texts)

    def test_managed_body_markers_exclude_nested_delivery_metadata(self) -> None:
        nested = ARTICLE_TEXT.replace(
            "<!-- ARTICLE_BODY_START -->\n",
            "# Final Article\n\n"
            "- Article ID: SHOULD-NOT-BE-A-BODY-UNIT\n"
            "- Article Version: v9\n"
            "- Target Language: English\n"
            "- Keywords: hidden metadata\n\n"
            "<!-- ARTICLE_BODY_START -->\n",
        )
        self.article.write_text(nested, encoding="utf-8")
        prepared = self.run_prepare()
        texts = [unit["text"] for unit in prepared["article_units"]]
        self.assertNotIn("Article ID: SHOULD-NOT-BE-A-BODY-UNIT", texts)
        self.assertNotIn("Target Language: English", texts)

    def test_managed_40_without_body_markers_is_rejected(self) -> None:
        without_markers = ARTICLE_TEXT.replace("<!-- ARTICLE_BODY_START -->\n", "").replace(
            "<!-- ARTICLE_BODY_END -->\n", ""
        )
        self.article.write_text(without_markers, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "prepare_article_audit.py"),
                "--article",
                str(self.article),
                "--knowledge",
                str(self.knowledge),
                "--output",
                str(self.prepared),
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing explicit body markers", result.stderr + result.stdout)

    def test_renderer_rejects_claim_attached_to_unrelated_quote(self) -> None:
        prepared = self.run_prepare()
        judgments = self.valid_judgments(prepared)
        question = next(unit for unit in prepared["article_units"] if unit["text"].startswith("Which option"))
        judgments["claims"][0]["unit_id"] = question["unit_id"]
        judgments["claims"][0]["article_line"] = question["line"]
        judgments["claims"][0]["article_quote"] = question["text"]
        self.judgments.write_text(json.dumps(judgments, ensure_ascii=False, indent=2), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not traceable"):
            discover_cases(self.work)

    def test_renderer_rejects_supported_evidence_from_blocked_section(self) -> None:
        prepared = self.run_prepare()
        judgments = self.valid_judgments(prepared)
        blocked_line = line_number(KNOWLEDGE_TEXT, "- 产品提供三年质保。")
        judgments["claims"][2]["verdict"] = "supported"
        judgments["claims"][2]["evidence"] = [
            {
                "source_file": str(self.knowledge.resolve()),
                "line_start": blocked_line,
                "line_end": blocked_line,
                "quote": "- 产品提供三年质保。",
            }
        ]
        self.judgments.write_text(
            json.dumps(judgments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "non-support section"):
            discover_cases(self.work)

    def test_renderer_rejects_evidence_range_crossing_outside_body(self) -> None:
        prepared = self.run_prepare()
        judgments = self.valid_judgments(prepared)
        judgments["claims"][0]["evidence"][0]["line_end"] = line_number(
            KNOWLEDGE_TEXT, "#### 事实素材：Protocol support"
        )
        self.judgments.write_text(
            json.dumps(judgments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "non-support section"):
            discover_cases(self.work)

    @unittest.skipUnless(
        os.environ.get("MANAGE_ARTICLE_KNOWLEDGE_V05"),
        "Set MANAGE_ARTICLE_KNOWLEDGE_V05 to run the external import contract test.",
    )
    def test_manage_v05_importer_accepts_artifacts(self) -> None:
        prepared = self.run_prepare()
        self.judgments.write_text(
            json.dumps(self.valid_judgments(prepared), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "render_article_audit.py"),
                "--input-dir",
                str(self.work),
                "--output-dir",
                str(self.output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        manage_root = Path(os.environ["MANAGE_ARTICLE_KNOWLEDGE_V05"])
        metrics = self.root / "10_文章Faithfulness明细.csv"
        support = self.root / "20_Claim文章支撑记录.csv"
        receipt = self.root / "50_Faithfulness结果.md"
        result = subprocess.run(
            [
                sys.executable,
                str(manage_root / "scripts" / "import_faithfulness.py"),
                "--prepared",
                str(self.prepared),
                "--judgments",
                str(self.judgments),
                "--summary",
                str(self.output / "faithfulness_summary.md"),
                "--article",
                str(self.article),
                "--knowledge",
                str(self.knowledge),
                "--article-version",
                "v1",
                "--article-date",
                "2026-08-19",
                "--metrics-csv",
                str(metrics),
                "--support-csv",
                str(support),
                "--receipt",
                str(receipt),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("faithfulness=66.67%", result.stdout)
        self.assertTrue(metrics.is_file())
        self.assertTrue(support.is_file())
        self.assertTrue(receipt.is_file())
        support_text = support.read_text(encoding="utf-8-sig")
        self.assertIn("CLM-TEST-PRODUCT-001", support_text)
        self.assertIn("CLM-TEST-PRODUCT-003", support_text)


if __name__ == "__main__":
    unittest.main()
