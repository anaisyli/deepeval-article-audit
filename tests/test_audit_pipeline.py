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

from render_article_audit import MODE, discover_cases  # noqa: E402


ARTICLE_TEXT = """# 最终文章

- 文章ID：ART-V05-001
- 文章版本：v1
- 完成日期：2026-08-19

## 最终正文

# Product | Guide

**IP67** certified.
产品支持双协议。另有三年质保。
Which option fits your project?
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
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.article = self.root / "40_最终文章.md"
        self.knowledge = self.root / "30_本篇知识库资料.md"
        self.work = self.root / "work"
        self.output = self.root / "output"
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
        self.assertIn("Product &#124; Guide", summary)
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

    def test_renderer_rejects_normalized_only_article_quote(self) -> None:
        prepared = self.run_prepare()
        judgments = self.valid_judgments(prepared)
        judgments["claims"][0]["article_quote"] = "IP67 certified."
        self.judgments.write_text(
            json.dumps(judgments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "exact substring"):
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
