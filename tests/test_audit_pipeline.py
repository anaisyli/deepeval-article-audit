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
from render_article_audit import (  # noqa: E402
    MODE,
    article_section,
    discover_cases,
    protocol2_handoff_error,
    reconciliation_needs_refresh,
    validate_render_invariants,
)


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
    def test_supported_filter_keeps_partially_supported_unit_visible(self) -> None:
        claim_base = {
            "unit_id": "U001",
            "article_line": 1,
            "article_quote": "A claim.",
            "claim": "A claim.",
            "reason": "逐项核对。",
            "evidence": [],
        }
        supported = {**claim_base, "claim_id": "C001", "verdict": "supported", "semantic_status": "entailed"}
        unknown = {**claim_base, "claim_id": "C002", "verdict": "unsupported", "semantic_status": "unknown"}
        case = {
            "prepared": {
                "article_id": "ART-001",
                "article_title": "Test",
                "article_units": [{"unit_id": "U001", "line": 1, "text": "A mixed unit."}],
                "article_lines": [{"line": 1, "text": "A mixed unit."}],
            },
            "judgments": {"penalize_ambiguous_claims": False},
            "claims": [supported, unknown],
        }
        validate_render_invariants(case)
        rendered = article_section(case, 0)
        self.assertIn('class="article-line article-unit partially-supported mixed_unknown contains-supported"', rendered)

    def test_renderer_refreshes_completed_reconciliation_with_stale_question(self) -> None:
        judgments = {
            "claims": [{"claim_id": "C040", "claim": "What is the warranty period?"}],
            "reconciliation": {
                "gate_version": "1.0",
                "coverage_gate_version": "1.0",
                "completed": True,
            },
        }
        adversarial = {
            "claim_reviews": [{"claim": "What is the warranty period?"}],
            "claim_inventory": [],
        }
        self.assertTrue(reconciliation_needs_refresh(judgments, adversarial))

        judgments["claims"][0]["claim"] = "The warranty period is seven days."
        adversarial["claim_reviews"][0]["claim"] = judgments["claims"][0]["claim"]
        self.assertFalse(reconciliation_needs_refresh(judgments, adversarial))

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
            renamed = nested_root.with_name("manage-article-knowledge-v0.6.1")
            nested_root.rename(renamed)
            self.assertEqual(locate_contract(nested), (renamed / "references" / "handoff-contract.json").resolve())
            with self.assertRaisesRegex(ValueError, "not found"):
                locate_contract(base / "missing-explicit-root")
            import shutil
            shutil.copytree(renamed, nested / "another-version")
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                locate_contract(nested)

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
        payload = {
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
        if prepared.get("integration_mode") == "manage-article-knowledge-v0.6":
            claim_units = {claim["unit_id"] for claim in payload["claims"]}
            classifications = []
            for unit in prepared["article_units"]:
                if unit.get("unit_type") in {"title", "heading", "table_header"}:
                    classifications.append(
                        {
                            "unit_id": unit["unit_id"],
                            "classification": "non_factual",
                            "category": unit["unit_type"],
                            "reason": f"{unit['unit_id']}由源文档结构明确标记为标题，不进入事实主张分母。",
                        }
                    )
                elif unit["unit_id"] in claim_units:
                    classifications.append(
                        {
                            "unit_id": unit["unit_id"],
                            "classification": "factual",
                            "reason": f"{unit['unit_id']}包含已拆分并进入判断的具体产品事实。",
                        }
                    )
                else:
                    category = "question" if unit["text"].rstrip().endswith(("?", "？")) else "transition"
                    classifications.append(
                        {
                            "unit_id": unit["unit_id"],
                            "classification": "non_factual",
                            "category": category,
                            "reason": (
                                f"{unit['unit_id']}是只提出信息需求、不陈述答案的纯问题。"
                                if category == "question"
                                else f"{unit['unit_id']}只承担段落衔接，不包含可独立判断真假的陈述。"
                            ),
                        }
                    )
            payload["coverage_review"] = {
                "reviewed_unit_count": len(prepared["article_units"]),
                "factual_unit_count": len(claim_units),
                "non_factual_unit_count": len(prepared["article_units"]) - len(claim_units),
                "unit_classifications": classifications,
                "reconsidered_candidate_unit_ids": [
                    unit["unit_id"] for unit in prepared["article_units"]
                    if unit["unit_id"] not in claim_units and unit.get("candidate_evidence")
                ],
            }
            payload["quality_review"] = {
                "gate_version": "1.0",
                "review_mode": "independent_adversarial",
                "reviewed_claim_count": len(payload["claims"]),
                "claim_reviews": [
                    {
                        "claim_id": claim["claim_id"],
                        "confirmed_verdict": claim["verdict"],
                        "claim_fragment": claim["claim"],
                        "evidence_fragment": (
                            claim["evidence"][0]["quote"] if claim["verdict"] == "supported" else ""
                        ),
                        "challenge_reason": (
                            f"反向复核{claim['claim_id']}后，逐字证据仍完整覆盖该主张且未增加范围。"
                            if claim["verdict"] == "supported"
                            else f"反向复核{claim['claim_id']}后，允许证据区仍未提供该质保期限。"
                        ),
                    }
                    for claim in payload["claims"]
                ],
            }
        return payload

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
        self.assertIn("可验证内容占比", summary)
        detail = (self.output / "ART-V05-001_faithfulness_details.md").read_text(encoding="utf-8")
        self.assertIn("## 内容单元识别", detail)
        self.assertIn("非主张内容，不计入", detail)
        highlight = (self.output / "faithfulness_highlight.html").read_text(encoding="utf-8")
        self.assertIn("只看非主张内容", highlight)
        self.assertIn("data-unit-id=\"U003\"", highlight)
        self.assertIn("class=\"article-line article-unit fully-supported entailed\"", highlight)
        self.assertIn("class=\"article-line article-unit unsupported legacy_unsupported\"", highlight)

    def test_table_effect_and_method_rows_expose_claim_review_signals(self) -> None:
        self.article.write_text(
            ARTICLE_TEXT.replace(
                "Which option fits your project?",
                "| Area | Decision | Effect |\n|---|---|---|\n| Repeatability | approved sample and checks | guides replenishment |\n| Sampling | actual product | Use the actual jewellery during sampling |",
            ),
            encoding="utf-8",
        )
        prepared = self.run_prepare()
        by_text = {unit["text"]: unit for unit in prepared["article_units"]}
        effect = by_text["Repeatability | approved sample and checks | guides replenishment"]
        method = by_text["Sampling | actual product | Use the actual jewellery during sampling"]
        header = by_text["Area | Decision | Effect"]
        self.assertEqual(effect["source_kind"], "table_data_row")
        self.assertIn("effect_or_causal_relation", effect["claim_review_signals"])
        self.assertEqual(method["source_kind"], "table_data_row")
        self.assertIn("table_data_row", method["claim_review_signals"])
        self.assertIn("operational_method", method["claim_review_signals"])
        self.assertEqual(header["source_kind"], "table_header")
        self.assertEqual(header["unit_type"], "table_header")
        self.assertEqual(header["candidate_evidence"], [])

    def test_legacy_pipe_table_header_is_structural_without_separator_row(self) -> None:
        self.article.write_text(
            ARTICLE_TEXT.replace(
                "Which option fits your project?",
                "| Program situation | Structural priority | Sample question |\n"
                "| Single-watch retail gift | Direct reveal | Does the watch stay centered? |",
            ),
            encoding="utf-8",
        )
        prepared = self.run_prepare()
        by_text = {unit["text"]: unit for unit in prepared["article_units"]}
        header = by_text["Program situation | Structural priority | Sample question"]
        data = by_text["Single-watch retail gift | Direct reveal | Does the watch stay centered?"]
        self.assertEqual(header["source_kind"], "table_header")
        self.assertEqual(header["unit_type"], "table_header")
        self.assertEqual(header["candidate_evidence"], [])
        self.assertEqual(data["source_kind"], "table_data_row")
        self.assertEqual(data["unit_type"], "content")

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

    def test_protocol_2_separates_deepeval_idk_from_strict_support(self) -> None:
        prepared = self.run_prepare_managed()
        judgments = self.valid_judgments(prepared)
        judgments["schema_version"] = "1.1"
        judgments["evaluation_protocol_version"] = "2.0"
        judgments["primary_metric"] = "strict_support"
        judgments["penalize_ambiguous_claims"] = False
        judgments.pop("quality_review", None)
        for claim in judgments["claims"]:
            if claim["claim_id"] == "C003":
                claim["deepeval_verdict"] = "idk"
                claim["semantic_status"] = "unknown"
                claim["verdict"] = "unsupported"
            elif claim["claim_id"] == "C002":
                claim["deepeval_verdict"] = "no"
                claim["semantic_status"] = "contradicted"
                claim["verdict"] = "unsupported"
            else:
                claim["deepeval_verdict"] = "yes"
                claim["semantic_status"] = "entailed"
        review = {
            "schema_version": "1.0",
            "evaluation_protocol_version": "2.0",
            "article_id": "ART-V05-001",
            "review_mode": "independent_adversarial",
            "claim_reviews": [],
        }
        for claim in judgments["claims"]:
            row = {
                "claim_id": claim["claim_id"],
                "unit_id": claim["unit_id"],
                "article_line": claim["article_line"],
                "article_quote": claim["article_quote"],
                "claim": claim["claim"],
                "independent_semantic_status": claim["semantic_status"],
                "semantic_status": claim["semantic_status"],
                "claim_fragment": claim["claim"],
                "evidence_fragment": claim["evidence"][0]["quote"] if claim["evidence"] else "",
                "atomicity_review": {
                    "status": "atomic",
                    "independent_proposition_count": 1,
                    "reason": "该条只包含一个可以独立判断的产品命题。",
                },
                "entailment_checks": {
                    "subject_object": {"status": "covered", "claim_fragment": "IP67", "evidence_fragment": "IP67", "reason": "主张和证据指向同一认证对象。"},
                    "predicate_relation": {"status": "covered", "claim_fragment": "certified", "evidence_fragment": "certified", "reason": "证据直接陈述认证关系。"},
                    "scope_condition": {"status": "not_applicable", "claim_fragment": "", "evidence_fragment": "", "reason": "主张没有额外适用条件。"},
                    "quantity_time_version": {"status": "covered", "claim_fragment": "IP67", "evidence_fragment": "IP67", "reason": "证据覆盖主张中的IP67等级限定。"},
                    "causal_effect": {"status": "not_applicable", "claim_fragment": "", "evidence_fragment": "", "reason": "主张没有因果效果断言。"},
                },
                "challenge_reason": (
                    "独立复核对象、断言、范围和条件后，证据直接陈述同一认证事实。"
                    if claim["semantic_status"] == "entailed"
                    else (
                        "独立复核后发现允许证据未陈述质保期限，因此只能判为unknown。"
                        if claim["semantic_status"] == "unknown"
                        else "独立复核后发现允许证据与该主张明确冲突，因此判为contradicted。"
                    )
                ),
            }
            if claim["semantic_status"] == "contradicted":
                row["contradiction_evidence_fragment"] = claim["evidence"][0]["quote"] if claim["evidence"] else ""
                row["contradiction_alignment"] = {
                    "subject_object": "aligned",
                    "scope_condition": "aligned",
                    "time_version": "aligned",
                }
            review["claim_reviews"].append(
                row
            )
        (self.managed / "ART-V05-001-judgments.json").write_text(
            json.dumps(judgments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.managed / "ART-V05-001-adversarial-review.json").write_text(
            json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # Preserve the discovered artifact stems when persisting reconciliation.
        for path in self.managed.glob('*.json'):
            path.rename(path.with_name(path.name.replace('ART-V05-001-', 'audit-case-')))
        source_paths = list(self.managed.glob("*.json"))
        original_sources = {path: path.read_bytes() for path in source_paths}
        for argument, value, error in (("--article-id", "WRONG-ARTICLE", "article_id"),
                                       ("--article-version", "v999", "article_version")):
            with self.subTest(argument=argument):
                rejected = subprocess.run(
                    [sys.executable, str(SCRIPTS / "render_article_audit.py"),
                     "--result-dir", str(self.managed), argument, value],
                    capture_output=True, text=True,
                )
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn(error, rejected.stderr)
                self.assertEqual({path: path.read_bytes() for path in source_paths}, original_sources,
                                 "Rejected identity must not persist reconciliation")
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
        summary = (self.managed / "faithfulness_summary.md").read_text(encoding="utf-8")
        self.assertIn("1 | 3 | 2 | 33.33%", summary)
        self.assertIn("1 | 3 | 2 | 33.33%", summary)
        self.assertIn("| 1 | 1 |", summary)
        self.assertNotIn("DeepEval兼容诊断", summary)
        self.assertEqual(set(self.managed.glob('*.json')), set(source_paths))
        persisted = json.loads(
            (self.managed / "audit-case-judgments.json").read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["reconciliation"]["gate_version"], "1.0")
        self.assertTrue(persisted["reconciliation"]["completed"])
        highlight = (self.managed / "faithfulness_highlight.html").read_text(encoding="utf-8")
        self.assertNotIn("DeepEval: idk", highlight)
        self.assertNotIn("DeepEval规则复现", highlight)
        self.assertIn("只看尚未确认", highlight)
        self.assertIn("只看明确冲突", highlight)
        self.assertIn("claim unknown unsupported", highlight)
        self.assertIn("claim contradicted unsupported", highlight)

    def test_managed_protocol_2_rejects_missing_adversarial_review(self) -> None:
        prepared = self.run_prepare_managed()
        judgments = self.valid_judgments(prepared)
        judgments["schema_version"] = "1.1"
        judgments["evaluation_protocol_version"] = "2.0"
        judgments["primary_metric"] = "strict_support"
        judgments["penalize_ambiguous_claims"] = False
        judgments.pop("quality_review", None)
        judgment_path = self.managed / "ART-V05-001-judgments.json"
        judgment_path.write_text(
            json.dumps(judgments, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        result = subprocess.run(
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
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires ART-V05-001-adversarial-review.json", result.stderr + result.stdout)
        self.assertFalse(
            (self.managed / "ART-V05-001-adversarial-review.json").exists()
        )
        self.assertFalse((self.managed / "faithfulness_summary.md").exists())

        incomplete_review = {
            "schema_version": "1.0",
            "evaluation_protocol_version": "2.0",
            "article_id": "ART-V05-001",
            "review_mode": "independent_adversarial",
            "claim_reviews": [],
        }
        (self.managed / "ART-V05-001-adversarial-review.json").write_text(
            json.dumps(incomplete_review, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        incomplete = subprocess.run(
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
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(incomplete.returncode, 0)
        self.assertIn(
            "exactly one row for every claim", incomplete.stderr + incomplete.stdout
        )
        self.assertFalse((self.managed / "faithfulness_summary.md").exists())

    def test_protocol_2_handoff_rejects_cross_proposition_review(self) -> None:
        judgments = {
            "article_id": "ART-001",
            "claims": [{
                "claim_id": "C001", "unit_id": "U001",
                "article_line": 1, "article_quote": "JERL lists watch packaging.",
                "claim": "JERL lists watch packaging.", "semantic_status": "unknown",
            }],
        }
        review = {
            "schema_version": "1.0", "evaluation_protocol_version": "2.0",
            "article_id": "ART-001", "review_mode": "independent_adversarial",
            "reconciliation": {
                "gate_version": "1.0", "policy": "two_pass_consensus",
                "completed": True, "no_human_queue": True,
            },
            "claim_reviews": [{
                "claim_id": "C001", "unit_id": "U002", "article_line": 2,
                "article_quote": "The sales context changes the hierarchy.",
                "claim": "The sales context changes the hierarchy.",
                "claim_fragment": "changes the hierarchy",
                "semantic_status": "unknown", "reconciled_semantic_status": "unknown",
                "challenge_reason": "该复核内容来自另一条正文命题，不能用于当前主张。",
            }],
        }
        self.assertIn("another proposition", protocol2_handoff_error(judgments, review))

    def test_managed_preparer_excludes_plain_title_inside_body_markers(self) -> None:
        self.article.write_text(
            ARTICLE_TEXT.replace(
                "<!-- ARTICLE_BODY_START -->\n",
                "<!-- ARTICLE_BODY_START -->\nProduct Guide\n\n",
            ),
            encoding="utf-8",
        )
        prepared = self.run_prepare_managed()
        self.assertNotIn("Product Guide", [unit["text"] for unit in prepared["article_units"]])

    def test_preparer_preserves_structured_heading_without_evidence_candidates(self) -> None:
        self.article.write_text(
            ARTICLE_TEXT.replace(
                "**IP67** certified.",
                "## Protection plan <!-- MAK_DOCX_HEADING_STYLE:Heading2 -->\n\n**IP67** certified.",
            ),
            encoding="utf-8",
        )
        prepared = self.run_prepare_managed()
        heading = next(unit for unit in prepared["article_units"] if unit["text"] == "Protection plan")
        self.assertEqual(heading["unit_type"], "heading")
        self.assertEqual(heading["heading_level"], 2)
        self.assertEqual(heading["heading_source"], "docx_style")
        self.assertEqual(heading["heading_style"], "Heading2")
        self.assertEqual(heading["candidate_evidence"], [])

    def test_preparer_excludes_markdown_image_markers_from_article_units(self) -> None:
        self.article.write_text(
            ARTICLE_TEXT.replace(
                "**IP67** certified.",
                "![Product packaging reference](images/product.jpg)\n\n**IP67** certified.",
            ),
            encoding="utf-8",
        )
        prepared = self.run_prepare_managed()
        texts = [unit["text"] for unit in prepared["article_units"]]
        self.assertNotIn("Product packaging reference", texts)
        self.assertFalse(any(text.startswith("![") for text in texts))

    def test_managed_renderer_rejects_structured_heading_claim_even_with_override(self) -> None:
        self.article.write_text(
            ARTICLE_TEXT.replace(
                "**IP67** certified.",
                "## Protection plan <!-- MAK_DOCX_HEADING_STYLE:Heading2 -->\n\n**IP67** certified.",
            ),
            encoding="utf-8",
        )
        prepared = self.run_prepare_managed()
        judgments = self.valid_judgments(prepared)
        heading = next(unit for unit in prepared["article_units"] if unit["text"] == "Protection plan")
        judgments["claims"].append(
            {
                "claim_id": "C004",
                "unit_id": heading["unit_id"],
                "article_line": heading["line"],
                "article_quote": heading["text"],
                "claim": "Use a protection plan.",
                "heading_override_reason": "该标题也可以被解释成操作建议，但结构标记应优先。",
                "verdict": "unsupported",
                "reason": "附件未确认该建议。",
                "evidence": [],
            }
        )
        (self.managed / "ART-V05-001-judgments.json").write_text(
            json.dumps(judgments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "structured heading unit"):
            discover_cases(self.managed)

    def test_managed_renderer_rejects_pure_question_claim(self) -> None:
        prepared = self.run_prepare_managed()
        judgments = self.valid_judgments(prepared)
        question = next(unit for unit in prepared["article_units"] if unit["text"].endswith("?"))
        evidence = judgments["claims"][0]["evidence"]
        judgments["claims"].append(
            {
                "claim_id": "C004",
                "unit_id": question["unit_id"],
                "article_line": question["line"],
                "article_quote": question["text"],
                "claim": question["text"],
                "verdict": "supported",
                "reason": "知识资料直接回答了这个问题。",
                "evidence": evidence,
            }
        )
        (self.managed / "ART-V05-001-judgments.json").write_text(
            json.dumps(judgments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "pure question"):
            discover_cases(self.managed)

    def test_structured_question_heading_uses_heading_category(self) -> None:
        self.article.write_text(
            ARTICLE_TEXT.replace(
                "**IP67** certified.",
                "## Which protection plan fits? <!-- MAK_DOCX_HEADING_STYLE:Heading2 -->\n\n**IP67** certified.",
            ),
            encoding="utf-8",
        )
        prepared = self.run_prepare_managed()
        judgments = self.valid_judgments(prepared)
        heading = next(unit for unit in prepared["article_units"] if unit["text"] == "Which protection plan fits?")
        (self.managed / "ART-V05-001-judgments.json").write_text(
            json.dumps(judgments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        cases = discover_cases(self.managed)
        row = next(
            row for row in cases[0]["judgments"]["coverage_review"]["unit_classifications"]
            if row["unit_id"] == heading["unit_id"]
        )
        self.assertEqual(row["category"], "heading")

    def test_managed_renderer_rejects_plain_heading_claim_without_override(self) -> None:
        self.article.write_text(
            ARTICLE_TEXT.replace(
                "# Product | Guide\n\n",
                "# Product | Guide\n\nProtection and presentation share one decision\n\n",
            ),
            encoding="utf-8",
        )
        prepared = self.run_prepare_managed()
        judgments = self.valid_judgments(prepared)
        heading = next(unit for unit in prepared["article_units"] if unit["text"].startswith("Protection"))
        judgments["claims"].append(
            {
                "claim_id": "C004",
                "unit_id": heading["unit_id"],
                "article_line": heading["line"],
                "article_quote": heading["text"],
                "claim": heading["text"],
                "verdict": "supported",
                "reason": "知识资料直接支持该包装决定。",
                "evidence": judgments["claims"][0]["evidence"],
            }
        )
        (self.managed / "ART-V05-001-judgments.json").write_text(
            json.dumps(judgments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "plain heading candidate"):
            discover_cases(self.managed)

    def test_managed_renderer_records_missing_quality_review_warning(self) -> None:
        prepared = self.run_prepare_managed()
        judgments = self.valid_judgments(prepared)
        judgments.pop("quality_review")
        (self.managed / "ART-V05-001-judgments.json").write_text(
            json.dumps(judgments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        cases = discover_cases(self.managed)
        self.assertEqual(cases[0]["judgments"]["audit_quality"], "warning")
        self.assertTrue(cases[0]["judgments"]["audit_warnings"])

    def test_managed_renderer_rejects_evidence_absence_as_non_factual_reason(self) -> None:
        prepared = self.run_prepare_managed()
        judgments = self.valid_judgments(prepared)
        row = next(
            row for row in judgments["coverage_review"]["unit_classifications"]
            if row["classification"] == "non_factual"
        )
        row["reason"] = "当前知识附件没有提供支持证据，因此不作为事实。"
        (self.managed / "ART-V05-001-judgments.json").write_text(
            json.dumps(judgments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "justified by missing evidence"):
            discover_cases(self.managed)

    def test_managed_renderer_records_template_verdict_warning(self) -> None:
        prepared = self.run_prepare_managed()
        judgments = self.valid_judgments(prepared)
        for claim in judgments["claims"]:
            claim["reason"] = "当前知识附件提供了与该文章单元对应的产品事实。"
        (self.managed / "ART-V05-001-judgments.json").write_text(
            json.dumps(judgments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        cases = discover_cases(self.managed)
        self.assertEqual(cases[0]["judgments"]["audit_quality"], "warning")
        self.assertTrue(any(w["code"] == "templated_verdict_reason" for w in cases[0]["judgments"]["audit_warnings"]))

    def test_managed_renderer_records_template_non_factual_warning(self) -> None:
        self.article.write_text(
            ARTICLE_TEXT.replace(
                "Which option fits your project?\n",
                "Which option fits your project?\nWhich option fits your shipment?\nWhich option fits your factory?\n",
            ),
            encoding="utf-8",
        )
        prepared = self.run_prepare_managed()
        judgments = self.valid_judgments(prepared)
        non_factual_rows = [
            row for row in judgments["coverage_review"]["unit_classifications"]
            if row["classification"] == "non_factual"
        ]
        self.assertGreaterEqual(len(non_factual_rows), 3)
        for row in non_factual_rows:
            row["reason"] = "该单元只是提问或衔接，不包含需要核验的事实。"
        (self.managed / "ART-V05-001-judgments.json").write_text(
            json.dumps(judgments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        cases = discover_cases(self.managed)
        self.assertEqual(cases[0]["judgments"]["audit_quality"], "warning")
        self.assertTrue(any(w["code"] == "templated_coverage_reason" for w in cases[0]["judgments"]["audit_warnings"]))

    def test_managed_renderer_records_missing_all_supported_challenge_warning(self) -> None:
        prepared = self.run_prepare_managed()
        judgments = self.valid_judgments(prepared)
        claim = judgments["claims"][2]
        claim["verdict"] = "supported"
        claim["reason"] = "知识附件的证据正文被用来支持当前质保陈述。"
        claim["evidence"] = judgments["claims"][0]["evidence"]
        review = judgments["quality_review"]["claim_reviews"][2]
        review["confirmed_verdict"] = "supported"
        review["evidence_fragment"] = claim["evidence"][0]["quote"]
        review["challenge_reason"] = "反向核对C003后，仍确认所引证据覆盖当前陈述。"
        (self.managed / "ART-V05-001-judgments.json").write_text(
            json.dumps(judgments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        cases = discover_cases(self.managed)
        self.assertEqual(cases[0]["judgments"]["audit_quality"], "warning")
        self.assertTrue(any(w["code"] == "missing_all_supported_challenge" for w in cases[0]["judgments"]["audit_warnings"]))

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

    def test_v06_managed_run_records_unreviewed_low_factual_coverage_warning(self) -> None:
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
        cases = discover_cases(self.managed)
        self.assertEqual(cases[0]["judgments"]["audit_quality"], "warning")
        self.assertTrue(any(w["code"] == "missing_coverage_review" for w in cases[0]["judgments"]["audit_warnings"]))

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
        manage_root = Path(os.environ["MANAGE_ARTICLE_KNOWLEDGE_V05"])
        project = self.root / "TEST-PROJECT_测试知识库"
        source_root = self.root / "source"
        source_root.mkdir()
        subprocess.run(
            [
                sys.executable,
                str(manage_root / "scripts" / "initialize_project.py"),
                "--project",
                str(project),
                "--project-name",
                "测试项目",
                "--source-root",
                str(source_root),
                "--website",
                "https://example.com/",
                "--content-owner",
                "测试负责人",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        task = (
            project
            / "04_文章任务"
            / "30_等待Faithfulness"
            / "ART-V05-001_Product_Guide"
        )
        task.mkdir(parents=True)
        self.article.replace(task / self.article.name)
        self.knowledge.replace(task / self.knowledge.name)
        (self.root / "35_写作素材来源索引.md").replace(
            task / "35_写作素材来源索引.md"
        )
        self.article = task / "40_最终文章.md"
        self.knowledge = task / "30_本篇知识库资料.md"
        formal = project / "03_正式知识/10_客户知识/20_产品介绍/product.md"
        formal.write_text(
            "# 测试产品\n\n"
            "## IP67认证\n\n- Claim ID：CLM-TEST-PRODUCT-001\n\n"
            "## 双协议支持\n\n- Claim ID：CLM-TEST-PRODUCT-002\n\n"
            "## 测试产品范围\n\n- Claim ID：CLM-TEST-PRODUCT-003\n",
            encoding="utf-8",
        )
        prepared = self.run_prepare()
        judgments = self.valid_judgments(prepared)
        # This compatibility test isolates artifact import and Claim mapping.
        # Unsupported governance requires a separate project disposition file.
        judgments["claims"] = [
            claim for claim in judgments["claims"] if claim["claim_id"] != "C003"
        ]
        self.judgments.write_text(
            json.dumps(judgments, ensure_ascii=False, indent=2),
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

        metrics = project / "05_数据与审核/10_Faithfulness/10_文章Faithfulness明细.csv"
        support = project / "05_数据与审核/10_Faithfulness/20_Claim文章支撑记录.csv"
        receipt = task / "50_文章知识使用与Faithfulness记录.md"
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
        self.assertIn("faithfulness=100.00%", result.stdout)
        self.assertTrue(metrics.is_file())
        self.assertTrue(support.is_file())
        completed_task = project / "04_文章任务/40_已完成" / task.name
        self.assertFalse(task.exists())
        self.assertTrue(
            (completed_task / "50_文章知识使用与Faithfulness记录.md").is_file()
        )
        support_text = support.read_text(encoding="utf-8-sig")
        self.assertIn("CLM-TEST-PRODUCT-001", support_text)
        self.assertIn("CLM-TEST-PRODUCT-003", support_text)


if __name__ == "__main__":
    unittest.main()
