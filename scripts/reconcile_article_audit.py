"""Conservatively reconcile first-pass and blind adversarial Faithfulness judgments.

This gate never upgrades a claim to entailed. It preserves entailed only when
both passes agree and the adversarial pass proves coverage of every material
semantic component. Unresolved cases become unknown so batch runs can finish
without creating a human-review queue.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from collections import Counter
from pathlib import Path

from audit_common import STRUCTURAL_UNIT_TYPES


GATE_VERSION = "1.0"
COVERAGE_GATE_VERSION = "1.0"
FORCED_REVIEW_SIGNALS = {
    "table_data_row",
    "effect_or_causal_relation",
    "operational_method",
    "comparison",
    "condition_or_scope",
}
PERMITTED_NONCLAIM_CATEGORIES = {
    "title",
    "heading",
    "table_header",
    "question",
    "transition",
    "cta",
}
CHECK_KEYS = (
    "subject_object",
    "predicate_relation",
    "scope_condition",
    "quantity_time_version",
    "causal_effect",
)
VALID_STATUSES = {"entailed", "unknown", "contradicted"}
STATUS_FIELDS = {
    "entailed": ("yes", "supported"),
    "unknown": ("idk", "unsupported"),
    "contradicted": ("no", "unsupported"),
}
COMPOUND_RE = re.compile(
    r"[;；]|\b(?:and|or|but|while|whereas|because|therefore|however|although|though)\b|"
    r"(?:和|或|但是|但|同时|以及|并且|而且|因为|所以|因此|然而|尽管)",
    re.IGNORECASE,
)
MULTI_AND_RE = re.compile(r"(?:\band\b.*){2,}|(?:、.*){2,}", re.IGNORECASE)
SCOPE_RE = re.compile(
    r"\b(?:if|when|whenever|unless|only|under|within|after|before|during|subject to|"
    r"except|for each|per)\b|(?:如果|当|仅|只有|在.+条件下|除非|之后|之前|期间|每)",
    re.IGNORECASE,
)
QUANTITY_RE = re.compile(
    r"\b(?:\d+(?:\.\d+)?%?|one|two|three|four|five|all|every|each|none|single|"
    r"multiple|many|few|minimum|maximum|at least|at most|more than|less than|"
    r"day|days|week|weeks|month|months|year|years|version|versions)\b|"
    r"(?:\d+(?:\.\d+)?%?|全部|所有|每个|至少|至多|超过|少于|天|周|月|年|版本)",
    re.IGNORECASE,
)
CAUSAL_RE = re.compile(
    r"\b(?:because|therefore|so that|prevents?|helps?|improves?|ensures?|allows?|"
    r"enables?|leads? to|results? in|causes?|changes?|makes?|keeps?|guides?|matches?|"
    r"supports?|benefits?|more useful|focus on)\b|"
    r"(?:因为|所以|因此|防止|避免|帮助|改善|确保|使得|导致|造成|改变|有利于|支持|指导|匹配)",
    re.IGNORECASE,
)


def normalized(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def is_pure_question(value: object) -> bool:
    """Return whether a proposed claim is only a question, not an assertion."""
    text = str(value or "").strip()
    return bool(text) and text.rstrip().endswith(("?", "？"))


def proposition_key(value: object) -> str:
    return " ".join(re.findall(r"\w+", normalized(value)))


def claim_key(claim: dict) -> tuple[str, str]:
    return str(claim.get("unit_id", "")), proposition_key(claim.get("claim"))


def claim_identity_key(claim: dict) -> tuple[str, str, str]:
    """Stable proposition identity shared by both passes; display IDs are excluded."""
    return (
        str(claim.get("unit_id", "")),
        str(claim.get("article_quote", "")),
        proposition_key(claim.get("claim")),
    )


def has_claim_identity(row: dict) -> bool:
    return bool(
        str(row.get("unit_id", "")).strip()
        and str(row.get("article_quote", "")).strip()
        and str(row.get("claim", "")).strip()
    )


def stamp_claim_identity(row: dict, claim: dict) -> None:
    row.update({
        "unit_id": claim.get("unit_id", ""),
        "article_line": claim.get("article_line"),
        "article_quote": claim.get("article_quote", ""),
        "claim": claim.get("claim", ""),
    })


def traceable_to_quote(claim: dict) -> bool:
    quote = proposition_key(claim.get("article_quote"))
    proposition = proposition_key(claim.get("claim"))
    if not quote or not proposition:
        return False
    if quote in proposition or proposition in quote:
        return True
    quote_terms = {word for word in quote.split() if len(word) >= 3}
    claim_terms = {word for word in proposition.split() if len(word) >= 3}
    shared = quote_terms & claim_terms
    # A single topical word such as "packaging" cannot establish extraction.
    if len(shared) >= 2 and len(shared) >= min(2, len(claim_terms)):
        return True
    quote_han = re.findall(r"[\u3400-\u9fff]{2,}", quote)
    claim_han = re.findall(r"[\u3400-\u9fff]{2,}", proposition)
    return bool(quote_han and claim_han and any(
        part in other or other in part for part in quote_han for other in claim_han
    ))


def align_claim_inventories(judgments: dict, adversarial: dict, prepared: dict | None) -> list[str]:
    """Match propositions, not row numbers; quarantine ambiguous second-pass rows."""
    claims = judgments.get("claims")
    rows = adversarial.get("claim_reviews")
    inventory = adversarial.get("claim_inventory")
    if not isinstance(claims, list) or not isinstance(rows, list):
        raise ValueError("Claims and adversarial claim_reviews must be arrays")
    warnings: list[str] = []
    units = {
        str(unit.get("unit_id")): unit
        for unit in (prepared or {}).get("article_units", [])
        if isinstance(unit, dict)
    }
    first_by_id = {str(claim.get("claim_id", "")): claim for claim in claims}
    if len(first_by_id) != len(claims) or "" in first_by_id:
        raise ValueError("First-pass claim IDs must be unique and nonempty")
    canonical: dict[tuple[str, str], dict] = {}
    canonical_identities: dict[tuple[str, str, str], dict] = {}
    aliases: dict[str, str] = {}
    kept: list[dict] = []
    for claim in claims:
        claim_id = str(claim["claim_id"])
        unit = units.get(str(claim.get("unit_id", ""))) if units else None
        if unit and (claim.get("article_line") != unit.get("line")
                     or proposition_key(claim.get("article_quote")) not in proposition_key(unit.get("text"))):
            claim.update(article_line=unit["line"], article_quote=unit["text"],
                         claim=unit["text"], semantic_status="unknown",
                         deepeval_verdict="idk", verdict="unsupported", evidence=[],
                         coverage_fallback=True)
            warnings.append(f"{claim_id}: source mismatch; original unit retained as unknown")
        if not traceable_to_quote(claim) and not str(claim.get("derivation_note", "")).strip():
            # Preserve the source unit in the denominator, never the invented proposition.
            claim.update(claim=str(claim["article_quote"]), semantic_status="unknown",
                         deepeval_verdict="idk", verdict="unsupported", evidence=[],
                         coverage_fallback=True)
            warnings.append(f"{claim_id}: first-pass proposition did not match its quote; source text retained as unknown")
        key = claim_key(claim)
        if key in canonical:
            survivor = canonical[key]
            aliases[claim_id] = str(survivor["claim_id"])
            if claim.get("semantic_status") != survivor.get("semantic_status"):
                survivor.update(semantic_status="unknown", deepeval_verdict="idk",
                                verdict="unsupported", evidence=[])
            warnings.append(f"{claim_id}: duplicate of {survivor['claim_id']} in the same article unit")
            continue
        canonical[key] = claim
        canonical_identities[claim_identity_key(claim)] = claim
        kept.append(claim)
    judgments["claims"] = kept
    by_id = {str(claim["claim_id"]): claim for claim in kept}

    source_rows = [row for row in rows if isinstance(row, dict)]
    used_rows: set[int] = set()
    aligned_rows: list[dict] = []
    for claim in kept:
        claim_id = str(claim["claim_id"])
        candidates = [
            candidate for candidate in source_rows
            if id(candidate) not in used_rows
            and has_claim_identity(candidate)
            and claim_identity_key(candidate) == claim_identity_key(claim)
        ]
        legacy_migration = False
        if not candidates:
            # Protocol-2 artifacts created before full review identity fields existed
            # may be migrated only when claim_fragment is the complete proposition,
            # never when it is merely a shared substring.
            candidates = [
                candidate for candidate in source_rows
                if id(candidate) not in used_rows
                and not has_claim_identity(candidate)
                and proposition_key(candidate.get("claim_fragment")) == proposition_key(claim.get("claim"))
                and sum(
                    proposition_key(candidate.get("claim_fragment")) == proposition_key(other.get("claim"))
                    for other in kept
                ) == 1
            ]
            legacy_migration = len(candidates) == 1
        row = candidates[0] if len(candidates) == 1 else None
        if row is not None and not exact_fragment(
            row.get("claim_fragment"), [str(claim.get("claim", ""))]
        ):
            warnings.append(f"{claim_id}: explanatory claim_fragment is invalid; review treated as unknown")
            row = None
        if row is not None:
            old_id = str(row.get("claim_id", ""))
            stamp_claim_identity(row, claim)
            row["claim_id"] = claim_id
            if old_id != claim_id:
                warnings.append(f"{claim_id}: adversarial review realigned from {old_id}")
            if legacy_migration:
                warnings.append(f"{claim_id}: legacy adversarial review received full proposition identity")
        if not isinstance(row, dict):
            row = unknown_review_row(
                claim, reason="第二轮复核行无法与当前正文命题唯一对齐；原结论已隔离并保守计为尚未确认。"
            )
            warnings.append(f"{claim_id}: adversarial review did not match the proposition; treated as unknown")
        used_rows.add(id(row))
        aligned_rows.append(row)
    adversarial["claim_reviews"] = aligned_rows

    if not isinstance(inventory, list):
        warnings.append("Second-pass claim_inventory is missing; source-unit coverage fallback remains active")
        return warnings
    unique_inventory: set[tuple[str, str]] = set()
    clean_inventory: list[dict] = []
    for item in inventory:
        if not isinstance(item, dict):
            warnings.append("Invalid second-pass inventory row was ignored")
            continue
        inventory_id = str(item.get("inventory_id", ""))
        unit = units.get(str(item.get("unit_id", ""))) if units else None
        if unit and unit.get("unit_type") in STRUCTURAL_UNIT_TYPES:
            continue
        if unit and (item.get("article_line") != unit.get("line")
                     or proposition_key(item.get("article_quote")) not in proposition_key(unit.get("text"))):
            warnings.append(f"{inventory_id}: quote/unit mismatch; inventory row was quarantined")
            continue
        if not traceable_to_quote(item) and not str(item.get("derivation_note", "")).strip():
            warnings.append(f"{inventory_id}: proposition/quote mismatch; inventory row was quarantined")
            continue
        key = claim_key(item)
        if key in unique_inventory:
            warnings.append(f"{inventory_id}: repeated second-pass proposition was deduplicated")
            continue
        unique_inventory.add(key)
        matched = str(item.get("matched_claim_id", ""))
        matched = aliases.get(matched, matched)
        target = canonical_identities.get(claim_identity_key(item))
        if target:
            actual = str(target["claim_id"])
            if matched != actual:
                warnings.append(f"{inventory_id}: matched_claim_id realigned from {matched or 'empty'} to {actual}")
            item["matched_claim_id"] = actual
        elif matched:
            warnings.append(f"{inventory_id}: unrelated matched_claim_id cleared; proposition kept as second-pass-only unknown")
            item["matched_claim_id"] = ""
        clean_inventory.append(item)
    adversarial["claim_inventory"] = clean_inventory
    return warnings


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp"
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def exact_fragment(fragment: object, containers: list[str]) -> bool:
    value = str(fragment or "").strip()
    return bool(value) and any(value in container for container in containers)


def claim_looks_compound(claim: dict) -> bool:
    text = str(claim.get("claim", ""))
    return bool(COMPOUND_RE.search(text) or MULTI_AND_RE.search(text))


def review_reason_duplicates(rows: list[dict]) -> set[str]:
    candidate_reasons = [
        normalized(row.get("challenge_reason"))
        for row in rows
        if row.get("independent_semantic_status", row.get("semantic_status")) == "entailed"
    ]
    counts = Counter(reason for reason in candidate_reasons if reason)
    return {reason for reason, count in counts.items() if count > 1}


def validate_entailed_review(claim: dict, row: dict, duplicated_reasons: set[str]) -> list[str]:
    problems: list[str] = []
    reason = normalized(row.get("challenge_reason"))
    if len(str(row.get("challenge_reason", "")).strip()) < 20:
        problems.append("第二轮理由过短")
    if not reason or reason in duplicated_reasons:
        problems.append("第二轮理由为重复模板")

    atomicity = row.get("atomicity_review")
    if not isinstance(atomicity, dict):
        problems.append("缺少原子性复核")
    else:
        count = atomicity.get("independent_proposition_count")
        if atomicity.get("status") != "atomic" or count != 1:
            problems.append("第二轮认为主张仍包含多个独立命题")
        if len(str(atomicity.get("reason", "")).strip()) < 12:
            problems.append("原子性复核理由不具体")
    if claim_looks_compound(claim) and len(str(claim.get("atomicity_note", "")).strip()) < 20:
        problems.append("复合信号未说明为何仍属一个原子命题")

    checks = row.get("entailment_checks")
    if not isinstance(checks, dict):
        return problems + ["缺少结构化语义覆盖检查"]
    if set(checks) != set(CHECK_KEYS):
        problems.append("语义覆盖检查字段不完整")

    claim_text = str(claim.get("claim", ""))
    evidence_quotes = [str(item.get("quote", "")) for item in claim.get("evidence", [])]
    required_covered = {"subject_object", "predicate_relation"}
    if SCOPE_RE.search(claim_text):
        required_covered.add("scope_condition")
    if QUANTITY_RE.search(claim_text):
        required_covered.add("quantity_time_version")
    if CAUSAL_RE.search(claim_text):
        required_covered.add("causal_effect")

    for key in CHECK_KEYS:
        check = checks.get(key)
        if not isinstance(check, dict):
            problems.append(f"{key}检查缺失")
            continue
        status = check.get("status")
        if status not in {"covered", "missing", "not_applicable"}:
            problems.append(f"{key}状态无效")
            continue
        if key in required_covered and status != "covered":
            problems.append(f"{key}未被证据覆盖")
        if status == "missing":
            problems.append(f"{key}存在未覆盖信息")
        if status == "covered":
            if not exact_fragment(check.get("claim_fragment"), [claim_text]):
                problems.append(f"{key}没有对应主张原文片段")
            if not exact_fragment(check.get("evidence_fragment"), evidence_quotes):
                problems.append(f"{key}没有对应证据原文片段")
        if len(str(check.get("reason", "")).strip()) < 8:
            problems.append(f"{key}缺少具体核对说明")
    return problems


def validate_contradicted_review(claim: dict, row: dict) -> list[str]:
    fragment = str(row.get("contradiction_evidence_fragment", "")).strip()
    evidence_quotes = [str(item.get("quote", "")) for item in claim.get("evidence", [])]
    if not fragment or not any(fragment in quote for quote in evidence_quotes):
        return ["第二轮未提供明确冲突的证据原文"]
    alignment = row.get("contradiction_alignment")
    if not isinstance(alignment, dict):
        return ["第二轮未说明冲突对象、范围、条件和时间版本是否对齐"]
    required = {"subject_object", "scope_condition", "time_version"}
    if set(alignment) != required or any(alignment.get(key) != "aligned" for key in required):
        return ["明确冲突的对象、范围、条件或时间版本未完全对齐"]
    return []


def unknown_claim_from_unit(unit: dict, *, source: str, claim_text: str | None = None) -> dict:
    text = str(unit.get("text", "")).strip()
    return {
        "claim_id": f"__{source}_{unit.get('unit_id', '')}",
        "unit_id": str(unit.get("unit_id", "")),
        "article_line": unit.get("line"),
        "article_quote": text,
        "claim": str(claim_text or text).strip(),
        "deepeval_verdict": "idk",
        "semantic_status": "unknown",
        "verdict": "unsupported",
        "reason": "双轮主张清单取并集后，该命题尚未获得两轮一致的明确支持或明确冲突证据。",
        "evidence": [],
        "coverage_fallback": True,
        "atomicity_note": "该项用于防止分母漏项；若由整单元兜底，不能据此断言其只有一个原子命题。",
    }


def unknown_review_row(claim: dict, *, reason: str) -> dict:
    text = str(claim.get("claim", ""))
    row = {
        "claim_id": claim["claim_id"],
        "claim_fragment": text,
        "evidence_fragment": "",
        "independent_semantic_status": "unknown",
        "semantic_status": "unknown",
        "atomicity_review": {
            "status": "split_required" if claim.get("coverage_fallback") else "atomic",
            "independent_proposition_count": 1,
            "reason": "该命题进入双轮并集，但尚未获得可签发绿色或红色结论的双轮证据判断。",
        },
        "entailment_checks": {
            key: {
                "status": "missing" if key in {"subject_object", "predicate_relation"} else "not_applicable",
                "claim_fragment": text if key in {"subject_object", "predicate_relation"} else "",
                "evidence_fragment": "",
                "reason": "分母并集项不自动签发支持结论，当前证据覆盖尚未得到双轮确认。",
            }
            for key in CHECK_KEYS
        },
        "challenge_reason": reason,
        "coverage_fallback": True,
        "alignment_quarantined": True,
    }
    stamp_claim_identity(row, claim)
    return row


def permitted_nonclaim_unit(
    unit: dict,
    first_classification: dict | None,
    second_classification: dict | None,
    *,
    require_second_pass: bool = True,
) -> bool:
    """Return whether both passes explicitly exclude this unit from the claim denominator."""
    if unit.get("unit_type") in STRUCTURAL_UNIT_TYPES:
        return True
    signals = set(unit.get("claim_review_signals") or []) & FORCED_REVIEW_SIGNALS
    second_is_non_factual = (
        isinstance(second_classification, dict)
        and second_classification.get("classification") == "non_factual"
    )
    second_says_factual = (
        isinstance(second_classification, dict)
        and second_classification.get("classification") == "factual"
    )
    return bool(
        isinstance(first_classification, dict)
        and first_classification.get("classification") == "non_factual"
        and first_classification.get("category") in PERMITTED_NONCLAIM_CATEGORIES
        and (second_is_non_factual if require_second_pass else not second_says_factual)
        and not signals
    )


def apply_second_pass_inventory_union(
    judgments: dict, adversarial: dict, prepared: dict | None
) -> tuple[list[str], list[str]]:
    """Add second-pass-only atomic claims before any evidence verdict is finalized."""
    if not isinstance(prepared, dict):
        return [], []
    units = {
        str(unit.get("unit_id")): unit
        for unit in prepared.get("article_units", [])
        if isinstance(unit, dict) and unit.get("unit_id")
    }
    claims = judgments.get("claims")
    rows = adversarial.get("claim_reviews")
    inventory = adversarial.get("claim_inventory")
    if not isinstance(claims, list) or not isinstance(rows, list) or not isinstance(inventory, list):
        return [], []
    first_coverage = judgments.get("coverage_review")
    first_classifications = (
        first_coverage.get("unit_classifications")
        if isinstance(first_coverage, dict) else None
    )
    first_by_id = {
        str(row.get("unit_id")): row
        for row in first_classifications or []
        if isinstance(row, dict) and row.get("unit_id")
    }
    second_coverage = adversarial.get("coverage_review")
    second_classifications = (
        second_coverage.get("unit_classifications")
        if isinstance(second_coverage, dict) else None
    )
    second_by_id = {
        str(row.get("unit_id")): row
        for row in second_classifications or []
        if isinstance(row, dict) and row.get("unit_id")
    }
    existing_ids = {str(claim.get("claim_id")) for claim in claims if isinstance(claim, dict)}
    existing_inventory_claims = {
        str(claim.get("second_pass_inventory_id")): claim
        for claim in claims
        if isinstance(claim, dict) and claim.get("second_pass_inventory_id")
    }
    added_inventory_ids: list[str] = []
    excluded_inventory_ids: list[str] = []
    seen_inventory_ids: set[str] = set()
    for item in inventory:
        if not isinstance(item, dict):
            continue
        inventory_id = str(item.get("inventory_id", "")).strip()
        unit_id = str(item.get("unit_id", "")).strip()
        matched = str(item.get("matched_claim_id", "")).strip()
        if not inventory_id or inventory_id in seen_inventory_ids or unit_id not in units:
            continue
        seen_inventory_ids.add(inventory_id)
        if inventory_id in existing_inventory_claims:
            item["matched_claim_id"] = str(existing_inventory_claims[inventory_id].get("claim_id", ""))
            continue
        if matched:
            if matched not in existing_ids:
                raise ValueError(f"Second-pass inventory {inventory_id} matches unknown claim {matched}")
            continue
        unit = units[unit_id]
        if permitted_nonclaim_unit(unit, first_by_id.get(unit_id), second_by_id.get(unit_id)):
            excluded_inventory_ids.append(inventory_id)
            continue
        quote = str(item.get("article_quote", "")).strip()
        atomic_claim = str(item.get("claim", "")).strip()
        if not quote or quote not in str(unit.get("text", "")) or not atomic_claim:
            raise ValueError(f"Second-pass inventory {inventory_id} is not traceable to {unit_id}")
        claim = unknown_claim_from_unit(unit, source=f"inventory_{inventory_id}", claim_text=atomic_claim)
        claim["article_quote"] = quote
        claim["second_pass_inventory_id"] = inventory_id
        claim["coverage_fallback"] = False
        item["matched_claim_id"] = claim["claim_id"]
        claims.append(claim)
        rows.append(
            unknown_review_row(
                claim,
                reason="第二轮独立阅读全文识别到第一轮遗漏的原子主张；已按并集规则纳入分母并标为尚未确认。",
            )
        )
        added_inventory_ids.append(inventory_id)
    return added_inventory_ids, excluded_inventory_ids


def exclude_pure_question_claims(
    judgments: dict, adversarial: dict, prepared: dict | None
) -> tuple[list[str], list[str]]:
    """Remove pure-question artifacts from every reconciliation input.

    A previous guard only excluded second-pass-only question inventory rows.  A
    first-pass claim (or a matched second-pass row) could therefore survive and
    fail later in the renderer.  This gate is deliberately structural: a claim
    whose own proposition ends in a question mark is never a factual claim.  A
    factual premise must be emitted by the claim extractor as a declarative
    proposition; FAQ answer units remain untouched.
    """
    claims = judgments.get("claims")
    rows = adversarial.get("claim_reviews")
    inventory = adversarial.get("claim_inventory")
    if not isinstance(claims, list):
        return [], []

    removed_claim_ids: list[str] = []
    kept_claims: list[dict] = []
    for claim in claims:
        if isinstance(claim, dict) and is_pure_question(claim.get("claim")):
            claim_id = str(claim.get("claim_id", "")).strip()
            if claim_id:
                removed_claim_ids.append(claim_id)
            continue
        kept_claims.append(claim)
    judgments["claims"] = kept_claims
    removed = set(removed_claim_ids)

    if isinstance(rows, list):
        # A pure question is not a final proposition, even when its row was
        # not linked to a first-pass claim.  Keep the final blind-review list
        # one-to-one with the surviving claims; the inventory keeps its
        # excluded row below as an audit breadcrumb.
        adversarial["claim_reviews"] = [
            row for row in rows
            if not isinstance(row, dict)
            or (
                str(row.get("claim_id", "")).strip() not in removed
                and not is_pure_question(row.get("claim"))
            )
        ]

    excluded_inventory_ids: list[str] = []
    if isinstance(inventory, list):
        for item in inventory:
            if not isinstance(item, dict):
                continue
            inventory_id = str(item.get("inventory_id", "")).strip()
            matched = str(item.get("matched_claim_id", "")).strip()
            if matched in removed or is_pure_question(item.get("claim")):
                if inventory_id:
                    excluded_inventory_ids.append(inventory_id)
                item["matched_claim_id"] = ""
                item["excluded_reason"] = "pure_question"

    # Keep the content-unit coverage layer consistent with the claim layer.
    # Only a unit that has no remaining claim and is itself a pure question is
    # reclassified; an FAQ answer with factual claims is never changed.
    remaining_units = {
        str(claim.get("unit_id"))
        for claim in kept_claims
        if isinstance(claim, dict) and claim.get("unit_id")
    }
    if isinstance(prepared, dict):
        unit_text = {
            str(unit.get("unit_id")): str(unit.get("text", ""))
            for unit in prepared.get("article_units", [])
            if isinstance(unit, dict) and unit.get("unit_id")
        }
        for payload in (judgments, adversarial):
            coverage = payload.get("coverage_review")
            classifications = coverage.get("unit_classifications") if isinstance(coverage, dict) else None
            if not isinstance(classifications, list):
                continue
            changed = False
            for row in classifications:
                if not isinstance(row, dict):
                    continue
                unit_id = str(row.get("unit_id", ""))
                if unit_id in remaining_units or not is_pure_question(unit_text.get(unit_id, "")):
                    continue
                row.update({
                    "classification": "non_factual",
                    "category": "question",
                    "reason": "正文单元是纯问题；问题本身不构成事实主张，答案中的事实主张仍需单独提取。",
                })
                changed = True
            if changed:
                coverage["factual_unit_count"] = sum(
                    isinstance(row, dict) and row.get("classification") == "factual"
                    for row in classifications
                )
                coverage["non_factual_unit_count"] = len(classifications) - coverage["factual_unit_count"]

    return removed_claim_ids, list(dict.fromkeys(excluded_inventory_ids))


def apply_coverage_fallback(judgments: dict, adversarial: dict, prepared: dict | None) -> tuple[list[str], list[str]]:
    """Require two-pass exclusion agreement and retain risky omissions as unknown."""
    if not isinstance(prepared, dict):
        return [], []
    units = prepared.get("article_units")
    coverage = judgments.get("coverage_review")
    classifications = coverage.get("unit_classifications") if isinstance(coverage, dict) else None
    claims = judgments.get("claims")
    rows = adversarial.get("claim_reviews")
    if not isinstance(units, list) or not isinstance(classifications, list):
        return [], []
    if not isinstance(claims, list) or not isinstance(rows, list):
        return [], []

    classification_by_id = {
        str(row.get("unit_id")): row
        for row in classifications
        if isinstance(row, dict) and row.get("unit_id")
    }
    second_coverage = adversarial.get("coverage_review")
    second_classifications = (
        second_coverage.get("unit_classifications") if isinstance(second_coverage, dict) else None
    )
    second_by_id = {
        str(row.get("unit_id")): row
        for row in second_classifications or []
        if isinstance(row, dict) and row.get("unit_id")
    }
    claimed_units = {str(claim.get("unit_id")) for claim in claims if isinstance(claim, dict)}
    added_unit_ids: list[str] = []
    coverage_warning_unit_ids: list[str] = []

    for unit in units:
        if not isinstance(unit, dict):
            continue
        unit_id = str(unit.get("unit_id", ""))
        if unit.get("unit_type") in STRUCTURAL_UNIT_TYPES:
            continue
        classification = classification_by_id.get(unit_id)
        second_classification = second_by_id.get(unit_id)
        signals = set(unit.get("claim_review_signals") or []) & FORCED_REVIEW_SIGNALS
        first_excluded = isinstance(classification, dict) and classification.get("classification") == "non_factual"
        second_excluded = (
            isinstance(second_classification, dict)
            and second_classification.get("classification") == "non_factual"
        )
        two_pass_exclusion = first_excluded and second_excluded
        second_says_factual = (
            isinstance(second_classification, dict)
            and second_classification.get("classification") == "factual"
        )
        missing_second_pass = not isinstance(second_classification, dict)
        permitted_nonclaim = permitted_nonclaim_unit(
            unit, classification, second_classification, require_second_pass=False
        )
        needs_fallback = first_excluded and (
            bool(signals) or second_says_factual or missing_second_pass or not two_pass_exclusion
        ) and not permitted_nonclaim
        if (
            not unit_id
            or unit_id in claimed_units
            or not needs_fallback
        ):
            continue
        text = str(unit.get("text", "")).strip()
        if not text:
            continue
        claim = unknown_claim_from_unit(unit, source="coverage")
        claims.append(claim)
        reason = (
            "两轮对该单元是否包含可验证主张存在分歧，已按并集规则纳入分母并标为尚未确认。"
            if second_says_factual
            else "第二轮全文分类缺失或覆盖信号提示可能漏项，已保守纳入分母并标为尚未确认。"
        )
        rows.append(unknown_review_row(claim, reason=reason))
        classification["classification"] = "factual"
        classification.pop("category", None)
        classification["reason"] = "覆盖门禁识别到可验证关系信号，已自动纳入分母并保守计为unknown。"
        claimed_units.add(unit_id)
        added_unit_ids.append(unit_id)
        coverage_warning_unit_ids.append(unit_id)

    if not added_unit_ids:
        return [], []

    coverage["factual_unit_count"] = sum(
        row.get("classification") == "factual" for row in classifications if isinstance(row, dict)
    )
    coverage["non_factual_unit_count"] = len(classifications) - coverage["factual_unit_count"]
    reconsidered = list(dict.fromkeys(list(coverage.get("reconsidered_candidate_unit_ids") or []) + added_unit_ids))
    coverage["reconsidered_candidate_unit_ids"] = reconsidered
    coverage["coverage_fallback_unit_ids"] = added_unit_ids
    coverage["coverage_warning_unit_ids"] = coverage_warning_unit_ids
    return added_unit_ids, coverage_warning_unit_ids


def enforce_structural_exclusions(
    judgments: dict, adversarial: dict, prepared: dict | None
) -> list[str]:
    """Remove source-structured titles, headings and table headers before denominator freeze."""
    if not isinstance(prepared, dict):
        return []
    structural = {
        str(unit.get("unit_id")): str(unit.get("unit_type"))
        for unit in prepared.get("article_units", [])
        if isinstance(unit, dict) and unit.get("unit_type") in STRUCTURAL_UNIT_TYPES
    }
    if not structural:
        return []
    claims = judgments.get("claims")
    removed_ids: list[str] = []
    if isinstance(claims, list):
        kept = []
        for claim in claims:
            if isinstance(claim, dict) and str(claim.get("unit_id")) in structural:
                removed_ids.append(str(claim.get("claim_id", "")))
            else:
                kept.append(claim)
        judgments["claims"] = kept
    removed = set(removed_ids)
    reviews = adversarial.get("claim_reviews")
    if isinstance(reviews, list):
        adversarial["claim_reviews"] = [
            row for row in reviews
            if not isinstance(row, dict) or str(row.get("claim_id", "")) not in removed
        ]
    inventory = adversarial.get("claim_inventory")
    if isinstance(inventory, list):
        adversarial["claim_inventory"] = [
            item for item in inventory
            if not isinstance(item, dict) or str(item.get("unit_id", "")) not in structural
        ]
    for payload in (judgments, adversarial):
        coverage = payload.get("coverage_review")
        rows = coverage.get("unit_classifications") if isinstance(coverage, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            unit_type = structural.get(str(row.get("unit_id", "")))
            if unit_type:
                row.update({
                    "classification": "non_factual",
                    "category": unit_type,
                    "reason": "原始Markdown或DOCX结构已将该单元标记为标题、小标题或表格表头，确定性排除且不进入主张分母。",
                })
        coverage["factual_unit_count"] = sum(
            isinstance(row, dict) and row.get("classification") == "factual" for row in rows
        )
        coverage["non_factual_unit_count"] = len(rows) - coverage["factual_unit_count"]
    return [claim_id for claim_id in removed_ids if claim_id]


def renumber_claims(judgments: dict, adversarial: dict, prepared: dict | None) -> None:
    claims = judgments.get("claims")
    rows = adversarial.get("claim_reviews")
    if not isinstance(claims, list) or not isinstance(rows, list):
        return
    unit_order = {
        str(unit.get("unit_id")): index
        for index, unit in enumerate((prepared or {}).get("article_units", []))
        if isinstance(unit, dict)
    }
    old_claim_ids = {id(claim): str(claim.get("claim_id", "")) for claim in claims}
    row_by_old_id = {
        str(row.get("claim_id")): row for row in rows if isinstance(row, dict) and row.get("claim_id")
    }
    claims.sort(key=lambda claim: (unit_order.get(str(claim.get("unit_id")), len(unit_order)), old_claim_ids[id(claim)]))
    reordered_rows: list[dict] = []
    id_map: dict[str, str] = {}
    for index, claim in enumerate(claims, 1):
        old_id = old_claim_ids[id(claim)]
        new_id = f"C{index:03d}"
        id_map[old_id] = new_id
        claim["claim_id"] = new_id
        row = row_by_old_id.get(old_id)
        if row is not None:
            row["claim_id"] = new_id
            reordered_rows.append(row)
    adversarial["claim_reviews"] = reordered_rows
    for item in adversarial.get("claim_inventory") or []:
        matched = str(item.get("matched_claim_id", ""))
        if matched in id_map:
            item["matched_claim_id"] = id_map[matched]


def reconcile_payloads(judgments: dict, adversarial: dict, prepared: dict | None = None) -> dict:
    if str(judgments.get("evaluation_protocol_version")) != "2.0":
        raise ValueError("Reconciliation is only defined for evaluation protocol 2.0")
    if judgments.get("article_id") != adversarial.get("article_id"):
        raise ValueError("Judgments and adversarial review article_id do not match")
    structurally_excluded_claim_ids = enforce_structural_exclusions(judgments, adversarial, prepared)
    alignment_warnings = align_claim_inventories(judgments, adversarial, prepared)
    (
        second_pass_inventory_ids,
        second_pass_inventory_ids_excluded,
    ) = apply_second_pass_inventory_union(judgments, adversarial, prepared)
    (
        pure_question_claim_ids_excluded,
        pure_question_inventory_ids_excluded,
    ) = exclude_pure_question_claims(judgments, adversarial, prepared)
    coverage_fallback_unit_ids, coverage_warning_unit_ids = apply_coverage_fallback(
        judgments, adversarial, prepared
    )
    renumber_claims(judgments, adversarial, prepared)
    claims = judgments.get("claims")
    rows = adversarial.get("claim_reviews")
    if not isinstance(claims, list):
        raise ValueError("Judgments claims must be an array")
    if not isinstance(rows, list):
        rows = []
        adversarial["claim_reviews"] = rows

    row_by_id = {
        str(row.get("claim_id")): row
        for row in rows
        if isinstance(row, dict) and row.get("claim_id")
    }
    duplicated_reasons = review_reason_duplicates(list(row_by_id.values()))
    downgraded: list[str] = []
    retained: list[str] = []
    unresolved_atomicity: list[str] = []

    for claim in claims:
        claim_id = str(claim.get("claim_id", ""))
        reconciliation = claim.get("reconciliation")
        recorded_first_status = (
            reconciliation.get("first_pass_semantic_status")
            if isinstance(reconciliation, dict)
            else None
        )
        first_status = str(
            recorded_first_status
            if recorded_first_status in VALID_STATUSES
            else claim.get("semantic_status", "unknown")
        )
        if first_status not in VALID_STATUSES:
            first_status = "unknown"
        row = row_by_id.get(claim_id)
        independent_status = "unknown"
        problems: list[str] = []
        if row is None:
            problems.append("缺少第二轮逐条复核")
        else:
            independent_status = str(
                row.get("independent_semantic_status", row.get("semantic_status", "unknown"))
            )
            if independent_status not in VALID_STATUSES:
                independent_status = "unknown"
                problems.append("第二轮语义状态无效")

        if first_status == independent_status == "entailed" and row is not None:
            problems.extend(validate_entailed_review(claim, row, duplicated_reasons))
            final_status = "entailed" if not problems else "unknown"
        elif first_status == independent_status == "contradicted" and row is not None:
            problems.extend(validate_contradicted_review(claim, row))
            final_status = "contradicted" if not problems else "unknown"
        elif first_status == independent_status == "unknown":
            final_status = "unknown"
        else:
            final_status = "unknown"
            problems.append(f"双轮结论不一致：第一轮{first_status}，第二轮{independent_status}")

        if final_status != first_status:
            downgraded.append(claim_id)
        elif final_status == "entailed":
            retained.append(claim_id)
        if any("原子" in problem or "多个独立命题" in problem for problem in problems):
            unresolved_atomicity.append(claim_id)

        deepeval_verdict, verdict = STATUS_FIELDS[final_status]
        claim["reconciliation"] = {
            "gate_version": GATE_VERSION,
            "first_pass_semantic_status": first_status,
            "independent_semantic_status": independent_status,
            "final_semantic_status": final_status,
            "action": "retained" if final_status == first_status else "downgraded_to_unknown",
            "reasons": problems,
        }
        claim["semantic_status"] = final_status
        claim["deepeval_verdict"] = deepeval_verdict
        claim["verdict"] = verdict
        if final_status != first_status:
            claim["reason"] = "双轮收敛未确认完整语义覆盖：" + "；".join(problems)

        if row is not None:
            row["independent_semantic_status"] = independent_status
            row["semantic_status"] = final_status
            row["reconciled_semantic_status"] = final_status
            row["reconciliation_action"] = claim["reconciliation"]["action"]
            row["reconciliation_reasons"] = problems

    metadata = {
        "gate_version": GATE_VERSION,
        "coverage_gate_version": COVERAGE_GATE_VERSION,
        "policy": "two_pass_consensus",
        "completed": True,
        "no_human_queue": True,
        "retained_entailed_claim_ids": retained,
        "downgraded_claim_ids": downgraded,
        "unresolved_atomicity_claim_ids": unresolved_atomicity,
        "coverage_fallback_unit_ids": coverage_fallback_unit_ids,
        "coverage_warning_unit_ids": coverage_warning_unit_ids,
        "second_pass_inventory_ids_added": second_pass_inventory_ids,
        "second_pass_inventory_ids_excluded": list(dict.fromkeys(
            second_pass_inventory_ids_excluded + pure_question_inventory_ids_excluded
        )),
        "pure_question_claim_ids_excluded": pure_question_claim_ids_excluded,
        "pure_question_inventory_ids_excluded": pure_question_inventory_ids_excluded,
        "structurally_excluded_claim_ids": structurally_excluded_claim_ids,
        "alignment_warnings": alignment_warnings,
    }
    judgments["reconciliation"] = metadata
    adversarial["reconciliation"] = metadata.copy()
    return metadata


def reconcile_files(
    judgments_path: Path,
    adversarial_path: Path,
    dry_run: bool = False,
    prepared_path: Path | None = None,
) -> dict:
    judgments = json.loads(judgments_path.read_text(encoding="utf-8-sig"))
    adversarial = json.loads(adversarial_path.read_text(encoding="utf-8-sig"))
    prepared = (
        json.loads(prepared_path.read_text(encoding="utf-8-sig"))
        if prepared_path is not None and prepared_path.is_file()
        else None
    )
    metadata = reconcile_payloads(judgments, adversarial, prepared)
    if not dry_run:
        atomic_write_json(judgments_path, judgments)
        atomic_write_json(adversarial_path, adversarial)
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-dir", type=Path)
    parser.add_argument("--article-id")
    parser.add_argument("--judgments", type=Path)
    parser.add_argument("--adversarial-review", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.result_dir:
        if args.judgments or args.adversarial_review:
            raise SystemExit("Do not combine --result-dir with explicit artifact paths")
        if not args.article_id:
            raise SystemExit("--article-id is required with --result-dir")
        judgments_path = args.result_dir / f"{args.article_id}-judgments.json"
        adversarial_path = args.result_dir / f"{args.article_id}-adversarial-review.json"
        prepared_path = args.result_dir / f"{args.article_id}-prepared.json"
    else:
        if not args.judgments or not args.adversarial_review:
            raise SystemExit("Provide --result-dir/--article-id or both explicit artifact paths")
        judgments_path = args.judgments
        adversarial_path = args.adversarial_review
        prepared_path = None
    if not judgments_path.is_file() or not adversarial_path.is_file():
        raise SystemExit("Judgments or adversarial review artifact not found")

    try:
        metadata = reconcile_files(judgments_path, adversarial_path, args.dry_run, prepared_path)
    except (ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
