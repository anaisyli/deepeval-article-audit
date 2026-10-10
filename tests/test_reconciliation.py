from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from reconcile_article_audit import reconcile_payloads  # noqa: E402


def check(status: str, claim_fragment: str = "", evidence_fragment: str = "") -> dict:
    return {
        "status": status,
        "claim_fragment": claim_fragment,
        "evidence_fragment": evidence_fragment,
        "reason": "逐项核对主张和证据中的对应语义成分。",
    }


def base_claim(claim_id: str, claim: str, evidence: str) -> dict:
    return {
        "claim_id": claim_id,
        "unit_id": "U001",
        "article_line": 1,
        "article_quote": claim,
        "claim": claim,
        "deepeval_verdict": "yes",
        "semantic_status": "entailed",
        "verdict": "supported",
        "reason": "第一轮认为证据支持该主张。",
        "evidence": [{"quote": evidence}],
    }


def claim_identity(claim: dict) -> tuple[str, str, str]:
    return (
        str(claim.get("unit_id", "")),
        str(claim.get("article_quote", "")),
        str(claim.get("claim", "")),
    )


def review_row(
    claim_id: str,
    claim: str,
    evidence: str,
    status: str = "entailed",
    *,
    unit_id: str = "U001",
    article_quote: str | None = None,
) -> dict:
    return {
        "claim_id": claim_id,
        "unit_id": unit_id,
        "article_line": 1,
        "article_quote": article_quote or claim,
        "claim": claim,
        "claim_fragment": claim,
        "evidence_fragment": evidence,
        "independent_semantic_status": status,
        "semantic_status": status,
        "atomicity_review": {
            "status": "atomic",
            "independent_proposition_count": 1,
            "reason": "该文本只包含一个可独立判断的核心命题。",
        },
        "entailment_checks": {
            "subject_object": check("covered", "product", "product"),
            "predicate_relation": check("covered", "is IP67 certified", "is IP67 certified"),
            "scope_condition": check("not_applicable"),
            "quantity_time_version": check("covered", "IP67", "IP67"),
            "causal_effect": check("not_applicable"),
        },
        "challenge_reason": "证据明确陈述同一产品具备IP67认证，未增加对象、范围或效果。",
    }


class ReconciliationTests(unittest.TestCase):
    def causal_review(self, text: str, *, causal: str = "not_applicable", scope: str = "covered") -> dict:
        claim = base_claim("C001", text, text)
        claim["atomicity_note"] = "本例仅测试词法门禁，其他独立命题在真实审核中仍需拆分并分别判断。"
        row = review_row("C001", text, text)
        row["entailment_checks"] = {
            "subject_object": check("covered", text, text),
            "predicate_relation": check("covered", text, text),
            "scope_condition": check(scope, text if scope == "covered" else "", text if scope == "covered" else ""),
            "quantity_time_version": check("covered", text, text),
            "causal_effect": check(causal, text if causal == "covered" else "", text if causal == "covered" else ""),
        }
        judgments, review = self.payloads(claim, row)
        reconcile_payloads(judgments, review)
        return judgments["claims"][0]

    def test_availability_and_manufacturing_are_not_causal_effects(self) -> None:
        for text in (
            "Physical samples may be sent when time allows.",
            "WHEN THE SCHEDULE ALLOWS, physical samples may be sent.",
            "Physical samples were sent if time allowed.",
            "The third step is to make prototypes for approval.",
            "The team makes physical samples before production.",
            "The team is making a prototype from paper.",
            "The team made prototypes for approval.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.causal_review(text)["semantic_status"], "entailed")

    def test_real_effects_still_require_causal_evidence(self) -> None:
        for text in (
            "The layout allows customers to see the label.",
            "Time allows paint to dry.",
            "When time allows paint to dry, inspect the sample.",
            "The team makes prototypes easier to inspect.",
            "The team made prototypes easier to inspect.",
            "The design is making samples easier to inspect.",
            "The layout allowed customers to see the label.",
            "The process makes samples more useful.",
            "When time allows, the layout helps customers see the label.",
            "Make prototypes for approval because this prevents errors.",
            "Make prototypes for approval so that customers can inspect them.",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.causal_review(text)["semantic_status"], "unknown")
                self.assertEqual(self.causal_review(text, causal="covered")["semantic_status"], "entailed")

    def test_noncausal_exemption_does_not_skip_condition_evidence(self) -> None:
        self.assertEqual(
            self.causal_review("Physical samples may be sent when time allows.", scope="not_applicable")["semantic_status"],
            "unknown",
        )

    def payloads(self, claim: dict, row: dict) -> tuple[dict, dict]:
        return (
            {
                "schema_version": "1.1",
                "evaluation_protocol_version": "2.0",
                "article_id": "ART-001",
                "claims": [claim],
            },
            {
                "schema_version": "1.0",
                "evaluation_protocol_version": "2.0",
                "article_id": "ART-001",
                "review_mode": "independent_adversarial",
                "claim_reviews": [row],
            },
        )

    def test_direct_entailment_survives_two_pass_gate(self) -> None:
        claim_text = "The product is IP67 certified."
        evidence = "The product is IP67 certified."
        judgments, review = self.payloads(
            base_claim("C001", claim_text, evidence),
            review_row("C001", claim_text, evidence),
        )
        metadata = reconcile_payloads(judgments, review)
        self.assertEqual(judgments["claims"][0]["semantic_status"], "entailed")
        self.assertEqual(metadata["retained_entailed_claim_ids"], ["C001"])

    def test_homogeneous_enumeration_can_remain_one_set_valued_claim(self) -> None:
        claim_text = "JERL offers paper, wood, velvet and leather."
        evidence = "JERL offers paper, wood, velvet and leather."
        claim = base_claim("C001", claim_text, evidence)
        claim["atomicity_note"] = (
            "同一供应能力关系下的四种材料构成一个集合值，成员共享对象、谓词、范围和时态。"
        )
        row = review_row("C001", claim_text, evidence)
        row["atomicity_review"]["reason"] = (
            "四种材料共同构成同一供应能力的集合值，只接受一个整体判断。"
        )
        row["entailment_checks"] = {
            "subject_object": check("covered", "JERL", "JERL"),
            "predicate_relation": check("covered", "offers", "offers"),
            "scope_condition": check("not_applicable"),
            "quantity_time_version": check("not_applicable"),
            "causal_effect": check("not_applicable"),
        }
        row["challenge_reason"] = (
            "证据以同一对象和同一供应关系完整列出四种材料，集合成员没有独立范围或条件。"
        )
        judgments, review = self.payloads(claim, row)

        metadata = reconcile_payloads(judgments, review)

        self.assertEqual(len(judgments["claims"]), 1)
        self.assertEqual(judgments["claims"][0]["semantic_status"], "entailed")
        self.assertEqual(metadata["retained_entailed_claim_ids"], ["C001"])

    def test_structured_heading_is_removed_before_denominator_is_frozen(self) -> None:
        heading_claim = base_claim("C001", "Choose box architecture", "Choose box architecture")
        row = review_row("C001", heading_claim["claim"], heading_claim["evidence"][0]["quote"])
        judgments, review = self.payloads(heading_claim, row)
        judgments["coverage_review"] = {
            "reviewed_unit_count": 1,
            "factual_unit_count": 1,
            "non_factual_unit_count": 0,
            "unit_classifications": [
                {"unit_id": "U001", "classification": "factual", "reason": "被第一轮误识别为建议。"}
            ],
            "reconsidered_candidate_unit_ids": [],
        }
        review["coverage_review"] = copy.deepcopy(judgments["coverage_review"])
        review["claim_inventory"] = [
            {
                "inventory_id": "P2-C001",
                "unit_id": "U001",
                "article_line": 1,
                "article_quote": "Choose box architecture",
                "claim": "Choose box architecture",
                "matched_claim_id": "C001",
            }
        ]
        prepared = {
            "article_units": [
                {
                    "unit_id": "U001",
                    "line": 1,
                    "text": "Choose box architecture",
                    "unit_type": "heading",
                    "source_kind": "heading",
                    "claim_review_signals": [],
                }
            ]
        }

        metadata = reconcile_payloads(judgments, review, prepared)

        self.assertEqual(judgments["claims"], [])
        self.assertEqual(metadata["structurally_excluded_claim_ids"], ["C001"])
        self.assertEqual(
            judgments["coverage_review"]["unit_classifications"][0]["category"],
            "heading",
        )

    def test_table_header_is_removed_before_denominator_is_frozen(self) -> None:
        header_text = "Specification area | Base question | Controlled alternative"
        header_claim = base_claim("C001", header_text, header_text)
        row = review_row("C001", header_text, header_text)
        judgments, review = self.payloads(header_claim, row)
        judgments["coverage_review"] = {
            "reviewed_unit_count": 1,
            "factual_unit_count": 1,
            "non_factual_unit_count": 0,
            "unit_classifications": [
                {"unit_id": "U001", "classification": "factual", "reason": "误把表头当成事实。"}
            ],
            "reconsidered_candidate_unit_ids": [],
        }
        review["coverage_review"] = copy.deepcopy(judgments["coverage_review"])
        review["claim_inventory"] = [
            {
                "inventory_id": "P2-C001",
                "unit_id": "U001",
                "article_line": 1,
                "article_quote": header_text,
                "claim": header_text,
                "matched_claim_id": "C001",
            }
        ]
        prepared = {
            "article_units": [
                {
                    "unit_id": "U001",
                    "line": 1,
                    "text": header_text,
                    "unit_type": "table_header",
                    "source_kind": "table_header",
                    "claim_review_signals": [],
                }
            ]
        }

        metadata = reconcile_payloads(judgments, review, prepared)

        self.assertEqual(judgments["claims"], [])
        self.assertEqual(metadata["structurally_excluded_claim_ids"], ["C001"])
        self.assertEqual(
            judgments["coverage_review"]["unit_classifications"][0]["category"],
            "table_header",
        )

    def test_same_topic_process_evidence_becomes_unknown(self) -> None:
        claim_text = "A sample should show where the product sits and how the lid is opened."
        evidence = "Create prototypes for your approval."
        claim = base_claim("C001", claim_text, evidence)
        claim["atomicity_note"] = "该主张作为样品展示要求的单一集合接受整体核验。"
        row = review_row("C001", claim_text, evidence)
        row["independent_semantic_status"] = "unknown"
        row["semantic_status"] = "unknown"
        row["challenge_reason"] = "证据只说明会制作样品，没有陈述产品位置或开盖方式。"
        judgments, review = self.payloads(claim, row)
        reconcile_payloads(judgments, review)
        result = judgments["claims"][0]
        self.assertEqual(result["semantic_status"], "unknown")
        self.assertEqual(result["deepeval_verdict"], "idk")
        self.assertEqual(result["verdict"], "unsupported")

    def test_missing_causal_effect_downgrades_agreed_entailed(self) -> None:
        claim_text = "The approval record helps later replenishment discussions."
        evidence = "Create prototypes for your approval."
        claim = base_claim("C001", claim_text, evidence)
        row = review_row("C001", claim_text, evidence)
        row["entailment_checks"]["causal_effect"] = check(
            "missing", "helps later replenishment discussions", ""
        )
        row["challenge_reason"] = "证据提到样品批准，但没有陈述对后续补货讨论的效果。"
        judgments, review = self.payloads(claim, row)
        reconcile_payloads(judgments, review)
        self.assertEqual(judgments["claims"][0]["semantic_status"], "unknown")

    def test_unresolved_compound_claim_becomes_unknown(self) -> None:
        claim_text = "Record dimensions and artwork, but exclude unverified MOQ and lead time."
        evidence = "External dimensions can be customized."
        claim = base_claim("C001", claim_text, evidence)
        row = review_row("C001", claim_text, evidence)
        row["atomicity_review"] = {
            "status": "split_required",
            "independent_proposition_count": 4,
            "reason": "尺寸、图稿、MOQ和交期可以分别获得不同判断。",
        }
        judgments, review = self.payloads(claim, row)
        metadata = reconcile_payloads(judgments, review)
        self.assertEqual(judgments["claims"][0]["semantic_status"], "unknown")
        self.assertEqual(metadata["unresolved_atomicity_claim_ids"], ["C001"])

    def test_reconciliation_never_upgrades_unknown(self) -> None:
        claim_text = "The product is IP67 certified."
        evidence = "The product is IP67 certified."
        claim = base_claim("C001", claim_text, evidence)
        claim.update(
            deepeval_verdict="idk", semantic_status="unknown", verdict="unsupported"
        )
        judgments, review = self.payloads(
            claim, review_row("C001", claim_text, evidence, "entailed")
        )
        reconcile_payloads(judgments, review)
        self.assertEqual(judgments["claims"][0]["semantic_status"], "unknown")

    def test_duplicate_supported_challenge_templates_are_downgraded(self) -> None:
        claim1 = base_claim("C001", "Product A is IP67 certified.", "Product A is IP67 certified.")
        claim2 = base_claim("C002", "Product B is IP67 certified.", "Product B is IP67 certified.")
        row1 = review_row("C001", claim1["claim"], claim1["evidence"][0]["quote"])
        row2 = review_row("C002", claim2["claim"], claim2["evidence"][0]["quote"])
        row2["challenge_reason"] = row1["challenge_reason"]
        judgments = {
            "schema_version": "1.1",
            "evaluation_protocol_version": "2.0",
            "article_id": "ART-001",
            "claims": [claim1, claim2],
        }
        review = {
            "schema_version": "1.0",
            "evaluation_protocol_version": "2.0",
            "article_id": "ART-001",
            "review_mode": "independent_adversarial",
            "claim_reviews": [row1, row2],
        }
        reconcile_payloads(judgments, review)
        self.assertEqual(
            [claim["semantic_status"] for claim in judgments["claims"]],
            ["unknown", "unknown"],
        )

    def test_signaled_excluded_unit_is_added_to_denominator_as_unknown(self) -> None:
        claim_text = "JERL offers paper packaging."
        evidence = "JERL offers paper packaging."
        judgments, review = self.payloads(
            base_claim("C001", claim_text, evidence),
            review_row("C001", claim_text, evidence),
        )
        judgments["coverage_review"] = {
            "reviewed_unit_count": 2,
            "factual_unit_count": 1,
            "non_factual_unit_count": 1,
            "unit_classifications": [
                {"unit_id": "U001", "classification": "factual", "reason": "包含客户能力主张。"},
                {
                    "unit_id": "U002",
                    "classification": "non_factual",
                    "category": "pure_recommendation",
                    "reason": "这是通用方法建议。",
                },
            ],
            "reconsidered_candidate_unit_ids": [],
        }
        prepared = {
            "article_units": [
                {"unit_id": "U001", "line": 1, "text": claim_text, "claim_review_signals": []},
                {
                    "unit_id": "U002",
                    "line": 2,
                    "text": "This approach makes range expansion easier to manage.",
                    "claim_review_signals": ["effect_or_causal_relation"],
                },
            ]
        }

        metadata = reconcile_payloads(judgments, review, prepared)

        self.assertEqual(len(judgments["claims"]), 2)
        self.assertEqual(judgments["claims"][1]["semantic_status"], "unknown")
        self.assertTrue(judgments["claims"][1]["coverage_fallback"])
        self.assertEqual(metadata["coverage_fallback_unit_ids"], ["U002"])
        self.assertEqual(judgments["coverage_review"]["factual_unit_count"], 2)
        self.assertEqual(judgments["coverage_review"]["non_factual_unit_count"], 0)

    def test_seven_supported_plus_fifty_seven_signaled_claims_cannot_be_100_percent(self) -> None:
        claims = []
        rows = []
        classifications = []
        units = []
        for index in range(1, 65):
            unit_id = f"U{index:03d}"
            if index <= 7:
                text = f"The product {index} is IP67 certified."
                claim_id = f"C{index:03d}"
                claim = base_claim(claim_id, text, text)
                claim["unit_id"] = unit_id
                claim["article_line"] = index
                row = review_row(claim_id, text, text, unit_id=unit_id)
                row["article_line"] = index
                row["challenge_reason"] = f"独立复核第{index}条产品认证主张，证据直接覆盖同一对象和等级。"
                claims.append(claim)
                rows.append(row)
                classifications.append({"unit_id": unit_id, "classification": "factual", "reason": "包含明确的认证关系主张。"})
                signals = []
            else:
                text = f"Use procedure {index} because it improves handling consistency."
                classifications.append({"unit_id": unit_id, "classification": "non_factual", "category": "pure_recommendation", "reason": "第一轮误判为纯建议。"})
                signals = ["operational_method", "effect_or_causal_relation"]
            units.append({"unit_id": unit_id, "line": index, "text": text, "claim_review_signals": signals})
        judgments = {
            "schema_version": "1.1",
            "evaluation_protocol_version": "2.0",
            "article_id": "ART-001",
            "claims": claims,
            "coverage_review": {
                "reviewed_unit_count": 64,
                "factual_unit_count": 7,
                "non_factual_unit_count": 57,
                "unit_classifications": classifications,
                "reconsidered_candidate_unit_ids": [],
            },
        }
        review = {
            "schema_version": "1.0",
            "evaluation_protocol_version": "2.0",
            "article_id": "ART-001",
            "review_mode": "independent_adversarial",
            "claim_reviews": rows,
        }
        metadata = reconcile_payloads(judgments, review, {"article_units": units})
        self.assertEqual(len(judgments["claims"]), 64)
        self.assertEqual(sum(c["semantic_status"] == "entailed" for c in judgments["claims"]), 7)
        self.assertEqual(len(metadata["coverage_warning_unit_ids"]), 57)

    def test_second_pass_factual_unit_without_rule_signal_enters_denominator(self) -> None:
        claim_text = "The product is IP67 certified."
        evidence = claim_text
        judgments, review = self.payloads(
            base_claim("C001", claim_text, evidence),
            review_row("C001", claim_text, evidence),
        )
        judgments["coverage_review"] = {
            "reviewed_unit_count": 2,
            "factual_unit_count": 1,
            "non_factual_unit_count": 1,
            "unit_classifications": [
                {"unit_id": "U001", "classification": "factual", "reason": "包含认证关系。"},
                {"unit_id": "U002", "classification": "non_factual", "category": "subjective", "reason": "第一轮误判为主观表达。"},
            ],
            "reconsidered_candidate_unit_ids": [],
        }
        review["coverage_review"] = {
            "reviewed_unit_count": 2,
            "unit_classifications": [
                {"unit_id": "U001", "classification": "factual", "reason": "包含认证关系。"},
                {"unit_id": "U002", "classification": "factual", "reason": "包含可以验证的适用关系。"},
            ],
        }
        prepared = {"article_units": [
            {"unit_id": "U001", "line": 1, "text": claim_text, "claim_review_signals": []},
            {"unit_id": "U002", "line": 2, "text": "This layout suits small-batch packing.", "claim_review_signals": []},
        ]}
        reconcile_payloads(judgments, review, prepared)
        self.assertEqual(len(judgments["claims"]), 2)
        self.assertEqual(judgments["claims"][1]["semantic_status"], "unknown")

    def test_second_pass_pure_question_inventory_is_excluded(self) -> None:
        claim_text = "The product is IP67 certified."
        judgments, review = self.payloads(
            base_claim("C001", claim_text, claim_text),
            review_row("C001", claim_text, claim_text),
        )
        judgments["coverage_review"] = {
            "unit_classifications": [
                {"unit_id": "U001", "classification": "factual", "reason": "认证关系。"},
                {"unit_id": "U002", "classification": "non_factual", "category": "question", "reason": "FAQ纯问题。"},
            ]
        }
        review["coverage_review"] = {
            "unit_classifications": [
                {"unit_id": "U001", "classification": "factual", "reason": "认证关系。"},
                {"unit_id": "U002", "classification": "non_factual", "category": "question", "reason": "FAQ纯问题。"},
            ]
        }
        question = "What is the warranty period?"
        review["claim_inventory"] = [{
            "inventory_id": "P2-Q001", "unit_id": "U002", "article_line": 2,
            "article_quote": question, "claim": question, "matched_claim_id": "",
        }]
        prepared = {"article_units": [
            {"unit_id": "U001", "line": 1, "text": claim_text, "unit_type": "content", "claim_review_signals": []},
            {"unit_id": "U002", "line": 2, "text": question, "unit_type": "content", "claim_review_signals": []},
        ]}

        metadata = reconcile_payloads(judgments, review, prepared)

        self.assertEqual(len(judgments["claims"]), 1)
        self.assertEqual(metadata["second_pass_inventory_ids_added"], [])
        self.assertEqual(metadata["second_pass_inventory_ids_excluded"], ["P2-Q001"])
        self.assertNotIn("P2-Q001", [claim.get("second_pass_inventory_id") for claim in judgments["claims"]])

    def test_first_pass_pure_question_claim_is_removed_before_render(self) -> None:
        question = "What is the warranty period?"
        judgments, review = self.payloads(
            base_claim("C001", question, question),
            review_row("C001", question, question),
        )
        classifications = [{
            "unit_id": "U001", "classification": "factual", "reason": "初轮误将FAQ问题写入主张。"
        }]
        judgments["coverage_review"] = {"unit_classifications": copy.deepcopy(classifications)}
        review["coverage_review"] = {"unit_classifications": copy.deepcopy(classifications)}
        review["claim_inventory"] = [{
            "inventory_id": "P2-Q001", "unit_id": "U001", "article_line": 1,
            "article_quote": question, "claim": question, "matched_claim_id": "C001",
        }]
        prepared = {"article_units": [{
            "unit_id": "U001", "line": 1, "text": question,
            "unit_type": "content", "claim_review_signals": [],
        }]}

        metadata = reconcile_payloads(judgments, review, prepared)

        self.assertEqual(judgments["claims"], [])
        self.assertEqual(metadata["pure_question_claim_ids_excluded"], ["C001"])
        self.assertEqual(metadata["pure_question_inventory_ids_excluded"], ["P2-Q001"])
        self.assertEqual(review["claim_reviews"], [])
        self.assertEqual(review["claim_inventory"][0]["matched_claim_id"], "")

    def test_first_pass_question_removal_does_not_remove_factual_faq_answer(self) -> None:
        question = "What is the warranty period?"
        answer = "The warranty is one year."
        claim = base_claim("C001", question, question)
        question_row = review_row("C001", question, question)
        judgments, review = self.payloads(claim, question_row)
        judgments["coverage_review"] = {"unit_classifications": [
            {"unit_id": "U001", "classification": "factual", "reason": "FAQ问题。"},
            {"unit_id": "U002", "classification": "factual", "reason": "FAQ回答事实。"},
        ]}
        review["coverage_review"] = copy.deepcopy(judgments["coverage_review"])
        review["claim_inventory"] = [{
            "inventory_id": "P2-A001", "unit_id": "U002", "article_line": 2,
            "article_quote": answer, "claim": answer, "matched_claim_id": "",
        }]
        prepared = {"article_units": [
            {"unit_id": "U001", "line": 1, "text": question, "unit_type": "content", "claim_review_signals": []},
            {"unit_id": "U002", "line": 2, "text": answer, "unit_type": "content", "claim_review_signals": []},
        ]}

        reconcile_payloads(judgments, review, prepared)

        self.assertEqual(len(judgments["claims"]), 1)
        self.assertEqual(judgments["claims"][0]["claim"], answer)

    def test_structured_question_heading_keeps_structural_category(self) -> None:
        question = "Should every cosmetic SKU use the same box size?"
        claim = base_claim("C001", question, question)
        row = review_row("C001", question, question)
        judgments, review = self.payloads(claim, row)
        classifications = [{
            "unit_id": "U001",
            "classification": "non_factual",
            "category": "heading",
            "reason": "结构化FAQ问题标题不进入事实主张分母。",
        }]
        judgments["coverage_review"] = {
            "reviewed_unit_count": 1,
            "factual_unit_count": 0,
            "non_factual_unit_count": 1,
            "unit_classifications": copy.deepcopy(classifications),
        }
        review["coverage_review"] = copy.deepcopy(judgments["coverage_review"])
        prepared = {"article_units": [{
            "unit_id": "U001",
            "line": 1,
            "text": question,
            "unit_type": "heading",
            "source_kind": "heading",
            "claim_review_signals": [],
        }]}

        reconcile_payloads(judgments, review, prepared)

        for payload in (judgments, review):
            row = payload["coverage_review"]["unit_classifications"][0]
            self.assertEqual(row["classification"], "non_factual")
            self.assertEqual(row["category"], "heading")

    def test_faq_answer_inventory_is_kept_while_question_is_excluded(self) -> None:
        claim_text = "The product is IP67 certified."
        judgments, review = self.payloads(
            base_claim("C001", claim_text, claim_text),
            review_row("C001", claim_text, claim_text),
        )
        first_classifications = [
            {"unit_id": "U001", "classification": "factual", "reason": "认证关系。"},
            {"unit_id": "U002", "classification": "non_factual", "category": "question", "reason": "FAQ纯问题。"},
            {"unit_id": "U003", "classification": "factual", "reason": "FAQ回答事实。"},
        ]
        second_classifications = copy.deepcopy(first_classifications)
        second_classifications[2]["reason"] = "独立复核确认回答包含可验证事实。"
        judgments["coverage_review"] = {"unit_classifications": first_classifications}
        review["coverage_review"] = {"unit_classifications": second_classifications}
        question = "What is the warranty period?"
        answer = "The warranty is one year."
        review["claim_inventory"] = [
            {"inventory_id": "P2-Q001", "unit_id": "U002", "article_line": 2,
             "article_quote": question, "claim": question, "matched_claim_id": ""},
            {"inventory_id": "P2-A001", "unit_id": "U003", "article_line": 3,
             "article_quote": answer, "claim": answer, "matched_claim_id": ""},
        ]
        prepared = {"article_units": [
            {"unit_id": "U001", "line": 1, "text": claim_text, "unit_type": "content", "claim_review_signals": []},
            {"unit_id": "U002", "line": 2, "text": question, "unit_type": "content", "claim_review_signals": []},
            {"unit_id": "U003", "line": 3, "text": answer, "unit_type": "content", "claim_review_signals": []},
        ]}

        metadata = reconcile_payloads(judgments, review, prepared)

        self.assertEqual(len(judgments["claims"]), 2)
        self.assertEqual(metadata["second_pass_inventory_ids_added"], ["P2-A001"])
        self.assertEqual(metadata["second_pass_inventory_ids_excluded"], ["P2-Q001"])
        self.assertEqual(judgments["claims"][1]["claim"], answer)
        self.assertEqual(judgments["claims"][1]["semantic_status"], "unknown")

    def test_reconciliation_is_idempotent_for_second_pass_inventory(self) -> None:
        claim_text = "The product is IP67 certified."
        judgments, review = self.payloads(
            base_claim("C001", claim_text, claim_text),
            review_row("C001", claim_text, claim_text),
        )
        classifications = [
            {"unit_id": "U001", "classification": "factual", "reason": "认证关系。"},
            {"unit_id": "U002", "classification": "non_factual", "category": "question", "reason": "FAQ纯问题。"},
            {"unit_id": "U003", "classification": "factual", "reason": "FAQ回答事实。"},
        ]
        judgments["coverage_review"] = {"unit_classifications": copy.deepcopy(classifications)}
        review["coverage_review"] = {"unit_classifications": copy.deepcopy(classifications)}
        question = "What is the warranty period?"
        answer = "The warranty is one year."
        review["claim_inventory"] = [
            {"inventory_id": "P2-Q001", "unit_id": "U002", "article_line": 2,
             "article_quote": question, "claim": question, "matched_claim_id": ""},
            {"inventory_id": "P2-A001", "unit_id": "U003", "article_line": 3,
             "article_quote": answer, "claim": answer, "matched_claim_id": ""},
        ]
        prepared = {"article_units": [
            {"unit_id": "U001", "line": 1, "text": claim_text, "unit_type": "content", "claim_review_signals": []},
            {"unit_id": "U002", "line": 2, "text": question, "unit_type": "content", "claim_review_signals": []},
            {"unit_id": "U003", "line": 3, "text": answer, "unit_type": "content", "claim_review_signals": []},
        ]}

        reconcile_payloads(judgments, review, prepared)
        first_identities = [claim_identity(claim) for claim in judgments["claims"]]
        second_metadata = reconcile_payloads(judgments, review, prepared)

        self.assertEqual([claim_identity(claim) for claim in judgments["claims"]], first_identities)
        self.assertEqual(len(judgments["claims"]), 2)
        self.assertEqual(second_metadata["second_pass_inventory_ids_added"], [])


    def test_second_pass_atomic_inventory_preserves_multiple_claims_in_one_unit(self) -> None:
        text = "The tray is sealed, reduces exposure, and supports cold storage."
        claim = base_claim("C001", "The tray is sealed.", "The tray is sealed.")
        claim.update(unit_id="U001", article_quote=text)
        first_row = review_row("C001", claim["claim"], claim["evidence"][0]["quote"])
        first_row["article_quote"] = text
        first_row["entailment_checks"]["subject_object"] = check("covered", "tray", "tray")
        first_row["entailment_checks"]["predicate_relation"] = check("covered", "is sealed", "is sealed")
        first_row["entailment_checks"]["quantity_time_version"] = check("not_applicable")
        first_row["challenge_reason"] = "独立复核托盘对象与密封关系，证据直接覆盖同一对象和同一断言。"
        judgments, review = self.payloads(claim, first_row)
        judgments["coverage_review"] = {
            "reviewed_unit_count": 1,
            "factual_unit_count": 1,
            "non_factual_unit_count": 0,
            "unit_classifications": [{"unit_id": "U001", "classification": "factual", "reason": "包含多个可独立核验的关系。"}],
            "reconsidered_candidate_unit_ids": [],
        }
        review["coverage_review"] = {
            "reviewed_unit_count": 1,
            "unit_classifications": [{"unit_id": "U001", "classification": "factual", "reason": "包含三个可独立核验的关系。"}],
        }
        review["claim_inventory"] = [
            {"inventory_id": "P2-C001", "unit_id": "U001", "article_line": 1, "article_quote": text, "claim": "The tray is sealed.", "matched_claim_id": "C001"},
            {"inventory_id": "P2-C002", "unit_id": "U001", "article_line": 1, "article_quote": text, "claim": "The tray reduces exposure.", "matched_claim_id": ""},
            {"inventory_id": "P2-C003", "unit_id": "U001", "article_line": 1, "article_quote": text, "claim": "The tray supports cold storage.", "matched_claim_id": ""},
        ]
        metadata = reconcile_payloads(judgments, review, {"article_units": [{"unit_id": "U001", "line": 1, "text": text, "claim_review_signals": []}]})
        self.assertEqual(len(judgments["claims"]), 3)
        self.assertEqual(metadata["second_pass_inventory_ids_added"], ["P2-C002", "P2-C003"])
        self.assertEqual([c["semantic_status"] for c in judgments["claims"]], ["entailed", "unknown", "unknown"])

    def test_duplicate_first_pass_propositions_are_deduplicated(self) -> None:
        text = "JERL lists food packaging among its applications."
        claim1 = base_claim("C001", text, text)
        claim2 = copy.deepcopy(claim1)
        claim2["claim_id"] = "C002"
        row1 = review_row("C001", text, text)
        row2 = review_row("C002", text, text)
        judgments = {
            "schema_version": "1.1", "evaluation_protocol_version": "2.0",
            "article_id": "ART-001", "claims": [claim1, claim2],
        }
        review = {
            "schema_version": "1.0", "evaluation_protocol_version": "2.0",
            "article_id": "ART-001", "review_mode": "independent_adversarial",
            "claim_reviews": [row1, row2],
            "claim_inventory": [
                {"inventory_id": "P2-C001", "unit_id": "U001", "article_line": 1,
                 "article_quote": text, "claim": text, "matched_claim_id": "C002"},
            ],
        }
        metadata = reconcile_payloads(
            judgments, review,
            {"article_units": [{"unit_id": "U001", "line": 1, "text": text,
                                "unit_type": "content", "claim_review_signals": []}]},
        )
        self.assertEqual(len(judgments["claims"]), 1)
        self.assertEqual(len(review["claim_reviews"]), 1)
        self.assertEqual(review["claim_inventory"][0]["matched_claim_id"], "C001")
        self.assertTrue(any("duplicate" in warning for warning in metadata["alignment_warnings"]))

    def test_misnumbered_adversarial_rows_are_rebound_by_proposition(self) -> None:
        first = base_claim("C001", "JERL lists watch packaging.", "Watch Packaging")
        second = base_claim("C002", "The sales context changes the hierarchy.", "Sales context")
        row_for_second = review_row("C001", second["claim"], second["evidence"][0]["quote"])
        row_for_first = review_row("C002", first["claim"], first["evidence"][0]["quote"])
        judgments = {
            "schema_version": "1.1", "evaluation_protocol_version": "2.0",
            "article_id": "ART-001", "claims": [first, second],
        }
        review = {
            "schema_version": "1.0", "evaluation_protocol_version": "2.0",
            "article_id": "ART-001", "review_mode": "independent_adversarial",
            "claim_reviews": [row_for_second, row_for_first],
            "claim_inventory": [
                {"inventory_id": "P2-C001", "unit_id": "U001", "article_line": 1,
                 "article_quote": first["claim"], "claim": first["claim"], "matched_claim_id": "C002"},
                {"inventory_id": "P2-C002", "unit_id": "U001", "article_line": 1,
                 "article_quote": second["claim"], "claim": second["claim"], "matched_claim_id": "C001"},
            ],
        }
        prepared = {"article_units": [{
            "unit_id": "U001", "line": 1,
            "text": first["claim"] + " " + second["claim"],
            "unit_type": "content", "claim_review_signals": [],
        }]}
        first["article_quote"] = first["claim"]
        second["article_quote"] = second["claim"]
        metadata = reconcile_payloads(judgments, review, prepared)
        self.assertEqual([row["claim_fragment"] for row in review["claim_reviews"]],
                         [first["claim"], second["claim"]])
        self.assertEqual([item["matched_claim_id"] for item in review["claim_inventory"]],
                         ["C001", "C002"])
        self.assertGreaterEqual(len(metadata["alignment_warnings"]), 2)

    def test_unalignable_adversarial_row_is_quarantined_as_unknown(self) -> None:
        claim = base_claim("C001", "JERL lists watch packaging.", "Watch Packaging")
        wrong = review_row("C001", "The sales context changes the hierarchy.", "Sales context")
        judgments, review = self.payloads(claim, wrong)
        review["claim_inventory"] = []
        metadata = reconcile_payloads(judgments, review, {
            "article_units": [{"unit_id": "U001", "line": 1, "text": claim["claim"],
                               "unit_type": "content", "claim_review_signals": []}],
        })
        self.assertEqual(judgments["claims"][0]["semantic_status"], "unknown")
        self.assertEqual(review["claim_reviews"][0]["independent_semantic_status"], "unknown")
        self.assertTrue(any("treated as unknown" in warning for warning in metadata["alignment_warnings"]))

    def test_shared_short_fragment_does_not_control_review_identity(self) -> None:
        evidence = "JERL offers paper packaging and tests it before shipment."
        first = base_claim("C001", "JERL offers paper packaging.", evidence)
        second = base_claim("C002", "JERL tests paper packaging before shipment.", evidence)
        first["article_quote"] = first["claim"]
        second.update(unit_id="U002", article_line=2, article_quote=second["claim"])
        first_row = review_row("C001", first["claim"], evidence)
        second_row = review_row(
            "C002", second["claim"], evidence, unit_id="U002", article_quote=second["article_quote"]
        )
        for row, predicate in ((first_row, "offers"), (second_row, "tests")):
            row["claim_fragment"] = "paper packaging"
            row["entailment_checks"] = {
                "subject_object": check("covered", "JERL", "JERL"),
                "predicate_relation": check("covered", predicate, predicate),
                "scope_condition": (
                    check("covered", "before shipment", "before shipment")
                    if predicate == "tests" else check("not_applicable")
                ),
                "quantity_time_version": check("not_applicable"),
                "causal_effect": check("not_applicable"),
            }
            row["challenge_reason"] = (
                f"独立复核完整主张身份后，证据明确覆盖JERL对象及{predicate}关系，短片段只用于说明检查位置。"
            )
        judgments = {
            "schema_version": "1.1", "evaluation_protocol_version": "2.0",
            "article_id": "ART-001", "claims": [first, second],
        }
        review = {
            "schema_version": "1.0", "evaluation_protocol_version": "2.0",
            "article_id": "ART-001", "review_mode": "independent_adversarial",
            "claim_reviews": [first_row, second_row], "claim_inventory": [],
        }
        prepared = {"article_units": [
            {"unit_id": "U001", "line": 1, "text": first["claim"],
             "unit_type": "content", "claim_review_signals": []},
            {"unit_id": "U002", "line": 2, "text": second["claim"],
             "unit_type": "content", "claim_review_signals": []},
        ]}
        metadata = reconcile_payloads(judgments, review, prepared)
        self.assertEqual([claim["semantic_status"] for claim in judgments["claims"]],
                         ["entailed", "entailed"])
        self.assertFalse(any(row.get("alignment_quarantined") for row in review["claim_reviews"]))
        self.assertFalse(any("treated as unknown" in item for item in metadata["alignment_warnings"]))

    def test_legacy_short_fragment_cannot_bind_without_full_identity(self) -> None:
        claim = base_claim("C001", "JERL offers paper packaging.", "JERL offers paper packaging.")
        row = review_row("C001", claim["claim"], claim["evidence"][0]["quote"])
        for field in ("unit_id", "article_line", "article_quote", "claim"):
            row.pop(field, None)
        row["claim_fragment"] = "paper packaging"
        judgments, review = self.payloads(claim, row)
        metadata = reconcile_payloads(judgments, review)
        self.assertEqual(judgments["claims"][0]["semantic_status"], "unknown")
        self.assertTrue(review["claim_reviews"][0]["alignment_quarantined"])
        self.assertTrue(any("treated as unknown" in item for item in metadata["alignment_warnings"]))

    def test_invalid_explanatory_fragment_quarantines_only_its_review(self) -> None:
        claim = base_claim("C001", "JERL offers paper packaging.", "JERL offers paper packaging.")
        row = review_row("C001", claim["claim"], claim["evidence"][0]["quote"])
        row["claim_fragment"] = "tests leather packaging"
        judgments, review = self.payloads(claim, row)
        metadata = reconcile_payloads(judgments, review)
        self.assertEqual(judgments["claims"][0]["semantic_status"], "unknown")
        self.assertTrue(review["claim_reviews"][0]["alignment_quarantined"])
        self.assertTrue(any("explanatory claim_fragment is invalid" in item for item in metadata["alignment_warnings"]))

    def test_legacy_complete_unique_claim_can_receive_identity_fields(self) -> None:
        claim = base_claim("C001", "JERL offers paper packaging.", "JERL offers paper packaging.")
        row = review_row("OLD-7", claim["claim"], claim["evidence"][0]["quote"])
        for field in ("unit_id", "article_line", "article_quote", "claim"):
            row.pop(field, None)
        judgments, review = self.payloads(claim, row)
        metadata = reconcile_payloads(judgments, review)
        migrated = review["claim_reviews"][0]
        self.assertEqual(migrated["claim_id"], "C001")
        self.assertEqual(migrated["unit_id"], claim["unit_id"])
        self.assertEqual(migrated["article_quote"], claim["article_quote"])
        self.assertEqual(migrated["claim"], claim["claim"])
        self.assertTrue(any("legacy adversarial review" in item for item in metadata["alignment_warnings"]))

    def test_contradicted_requires_explicit_aligned_conflict(self) -> None:
        claim_text = "The product warranty is two years."
        evidence = "The product warranty is one year."
        claim = base_claim("C001", claim_text, evidence)
        claim.update(deepeval_verdict="no", semantic_status="contradicted", verdict="unsupported")
        row = review_row("C001", claim_text, evidence, "contradicted")
        row["contradiction_evidence_fragment"] = evidence
        judgments, review = self.payloads(claim, row)
        reconcile_payloads(judgments, review)
        self.assertEqual(judgments["claims"][0]["semantic_status"], "unknown")

        claim = base_claim("C001", claim_text, evidence)
        claim.update(deepeval_verdict="no", semantic_status="contradicted", verdict="unsupported")
        row = review_row("C001", claim_text, evidence, "contradicted")
        row["contradiction_evidence_fragment"] = evidence
        row["contradiction_alignment"] = {
            "subject_object": "aligned", "scope_condition": "aligned", "time_version": "aligned"
        }
        judgments, review = self.payloads(claim, row)
        reconcile_payloads(judgments, review)
        self.assertEqual(judgments["claims"][0]["semantic_status"], "contradicted")

    def test_zero_supported_claims_is_a_normal_result(self) -> None:
        claim = base_claim("C001", "The product has a two-year warranty.", "")
        claim.update(deepeval_verdict="idk", semantic_status="unknown", verdict="unsupported", evidence=[])
        row = review_row("C001", claim["claim"], "", "unknown")
        judgments, review = self.payloads(claim, row)
        reconcile_payloads(judgments, review)
        self.assertEqual(sum(c["semantic_status"] == "entailed" for c in judgments["claims"]), 0)
        self.assertTrue(review["reconciliation"]["completed"])


if __name__ == "__main__":
    unittest.main()

