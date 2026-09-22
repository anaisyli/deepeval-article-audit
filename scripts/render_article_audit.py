"""Validate Codex judgments and render Markdown plus interactive HTML reports."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from pathlib import Path

from audit_common import (
    STRUCTURAL_UNIT_TYPES,
    extract_article,
    article_content_sha256,
    extract_knowledge_chunks,
    normalize_inline_markdown,
    normalized_path,
    public_chunk,
    read_text,
)
from manage_handoff_contract import ManagedHandoffError, load_contract, validate_event, validate_version
from reconcile_article_audit import GATE_VERSION as RECONCILIATION_GATE_VERSION
from reconcile_article_audit import (
    atomic_write_json,
    claim_identity_key,
    claim_key,
    exact_fragment,
    has_claim_identity,
    is_pure_question,
    reconcile_payloads,
)


MODE = "DeepEval Faithfulness 规则复现（Codex 评审，非 DeepEval 官方运行）"
VALID_VERDICTS = {"supported", "unsupported"}
VALID_DEEPEVAL_VERDICTS = {"yes", "no", "idk"}
VALID_SEMANTIC_STATUSES = {"entailed", "contradicted", "unknown"}
LOW_COVERAGE_MIN_UNITS = 20
LOW_COVERAGE_MAX_SHARE = 0.20
MANAGED_QUALITY_GATE_VERSION = "1.0"
NON_FACTUAL_CATEGORIES = {
    "title", "heading", "table_header", "question", "transition", "cta", "subjective",
    "hypothetical", "pure_recommendation",
}
EVIDENCE_ABSENCE_REASON = re.compile(
    r"(?:未找到|没有|未提供|缺少).{0,12}(?:证据|依据|知识)|"
    r"(?:知识|附件).{0,12}(?:未找到|没有|未提供|缺少)|"
    r"(?:no|missing|without).{0,12}(?:evidence|support|context)",
    re.I,
)


def reconciliation_needs_refresh(judgments: dict, adversarial_review: object) -> bool:
    """Detect protocol-2 artifacts whose prior reconciliation is stale.

    Older runs could persist a completed reconciliation while leaving a pure
    FAQ question in the first-pass claims or the blind-pass artifacts.  The
    normal ``reconciliation`` metadata then made the renderer skip the gate
    and the later validator raised a hard error.  Refresh only when a known
    structural residue is present; clean reconciled artifacts remain
    idempotent and are not rewritten.
    """
    if not isinstance(adversarial_review, dict):
        return False
    claims = judgments.get("claims")
    if isinstance(claims, list) and any(
        isinstance(claim, dict) and is_pure_question(claim.get("claim"))
        for claim in claims
    ):
        return True
    for field in ("claim_reviews", "claim_inventory"):
        rows = adversarial_review.get(field)
        if isinstance(rows, list) and any(
            isinstance(row, dict)
            and row.get("excluded_reason") != "pure_question"
            and is_pure_question(row.get("claim"))
            for row in rows
        ):
            return True
    return False


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(read_text(path))


def protocol2_handoff_error(judgments: dict, adversarial_review: object) -> str | None:
    """Return a structural error that would make the managed importer reject the result."""
    article_id = str(judgments.get("article_id", "")).strip()
    claims = judgments.get("claims")
    if not isinstance(adversarial_review, dict):
        return "protocol 2.0 requires an adversarial review artifact"
    if adversarial_review.get("schema_version") != "1.0":
        return "protocol 2.0 adversarial review must use schema_version 1.0"
    if adversarial_review.get("evaluation_protocol_version") != "2.0":
        return "protocol 2.0 adversarial review has an incompatible protocol version"
    if adversarial_review.get("article_id") != article_id:
        return "protocol 2.0 adversarial review article_id does not match judgments"
    if adversarial_review.get("review_mode") != "independent_adversarial":
        return "protocol 2.0 adversarial review must declare independent_adversarial mode"
    reconciliation = adversarial_review.get("reconciliation")
    if (
        not isinstance(reconciliation, dict)
        or reconciliation.get("gate_version") != "1.0"
        or reconciliation.get("policy") != "two_pass_consensus"
        or reconciliation.get("completed") is not True
        or reconciliation.get("no_human_queue") is not True
    ):
        return "protocol 2.0 adversarial review must complete reconciliation gate 1.0"
    if not isinstance(claims, list):
        return "protocol 2.0 judgments claims must be an array"
    rows = adversarial_review.get("claim_reviews")
    if not isinstance(rows, list):
        return "protocol 2.0 adversarial review has no claim_reviews array"
    claim_ids = [str(claim.get("claim_id", "")).strip() for claim in claims]
    row_ids = [str(row.get("claim_id", "")).strip() for row in rows if isinstance(row, dict)]
    if len(row_ids) != len(rows) or any(not claim_id for claim_id in row_ids):
        return "protocol 2.0 adversarial review contains an invalid claim_id row"
    if len(row_ids) != len(set(row_ids)) or set(row_ids) != set(claim_ids):
        return "protocol 2.0 adversarial review must contain exactly one row for every claim"
    by_id = {str(row["claim_id"]): row for row in rows}
    proposition_keys: set[tuple[str, str]] = set()
    for claim in claims:
        claim_id = str(claim.get("claim_id", "")).strip()
        key = claim_key(claim)
        if key in proposition_keys:
            return f"protocol 2.0 contains duplicate proposition {claim_id} in one article unit"
        proposition_keys.add(key)
        row = by_id[claim_id]
        if not has_claim_identity(row):
            return f"protocol 2.0 adversarial review {claim_id} lacks full proposition identity"
        if claim_identity_key(row) != claim_identity_key(claim):
            return f"protocol 2.0 adversarial review {claim_id} belongs to another proposition"
        if not exact_fragment(row.get("claim_fragment"), [str(claim.get("claim", ""))]):
            return f"protocol 2.0 adversarial review {claim_id} has an invalid explanatory claim_fragment"
        final_status = claim.get("semantic_status")
        if row.get("semantic_status") != final_status:
            return f"protocol 2.0 adversarial review disagrees with {claim_id}"
        if row.get("reconciled_semantic_status") != final_status:
            return f"protocol 2.0 adversarial reconciliation disagrees with {claim_id}"
        if (
            final_status in {"entailed", "contradicted"}
            and len(str(row.get("challenge_reason", "")).strip()) < 20
        ):
            return f"protocol 2.0 adversarial review for {claim_id} lacks a concrete reason"
        if final_status == "entailed" and not str(row.get("evidence_fragment", "")).strip():
            return f"protocol 2.0 adversarial review for {claim_id} lacks evidence"
    inventory = adversarial_review.get("claim_inventory")
    if isinstance(inventory, list):
        by_claim_id = {str(claim["claim_id"]): claim for claim in claims}
        for item in inventory:
            if not isinstance(item, dict):
                return "protocol 2.0 claim_inventory contains an invalid row"
            matched = str(item.get("matched_claim_id", ""))
            if matched and (
                matched not in by_claim_id
                or claim_identity_key(item) != claim_identity_key(by_claim_id[matched])
            ):
                return "protocol 2.0 claim_inventory matched_claim_id belongs to another proposition"
    return None


def result_scores(claims: list[dict], penalize_ambiguous_claims: bool = False) -> dict:
    """Return the strict import score and the DeepEval-compatible reproduction score.

    Legacy judgments have no three-state verdict and therefore expose only the
    strict score. New protocol-2 judgments must record the original DeepEval
    yes/no/idk verdict for comparison, while the knowledge-base import uses the
    stricter entailed-only verdict.
    """
    total = len(claims)
    strict_supported = sum(claim.get("verdict") == "supported" for claim in claims)
    strict_rate = strict_supported / total * 100 if total else None
    deepeval_verdicts = [claim.get("deepeval_verdict") for claim in claims]
    semantic_statuses = [claim.get("semantic_status") for claim in claims]
    if all(verdict in VALID_DEEPEVAL_VERDICTS for verdict in deepeval_verdicts) and total:
        deepeval_supported = sum(
            verdict == "yes" if penalize_ambiguous_claims else verdict != "no"
            for verdict in deepeval_verdicts
        )
        deepeval_rate = deepeval_supported / total * 100
        unknown = sum(status == "unknown" for status in semantic_statuses)
        contradicted = sum(status == "contradicted" for status in semantic_statuses)
    else:
        deepeval_supported = None
        deepeval_rate = None
        unknown = sum(claim.get("semantic_status") == "unknown" for claim in claims)
        contradicted = sum(claim.get("semantic_status") == "contradicted" for claim in claims)
    return {
        "strict_supported": strict_supported,
        "strict_rate": strict_rate,
        "deepeval_supported": deepeval_supported,
        "deepeval_rate": deepeval_rate,
        "unknown": unknown,
        "contradicted": contradicted,
    }


def normalized(text: str) -> str:
    return normalize_inline_markdown(text)


def read_source_lines(path: Path) -> list[str]:
    return read_text(path).splitlines()


def is_question(text: str) -> bool:
    return normalized(text).rstrip().endswith(("?", "？"))


def looks_like_plain_heading(unit: dict, source_lines: list[str]) -> bool:
    """Flag isolated, unmarked heading-like lines for explicit reviewer handling."""
    index = int(unit["line"]) - 1
    if index < 0 or index >= len(source_lines):
        return False
    raw = source_lines[index].strip()
    if not raw or raw.startswith(("#", "|", "- ", "* ", "+ ", ">")):
        return False
    if raw.endswith((".", "!", "?", "。", "！", "？", ":", "：", ";", "；")):
        return False
    previous_blank = index == 0 or not source_lines[index - 1].strip() or source_lines[index - 1].strip() == "<!-- ARTICLE_BODY_START -->"
    next_blank = index + 1 >= len(source_lines) or not source_lines[index + 1].strip() or source_lines[index + 1].strip() == "<!-- ARTICLE_BODY_END -->"
    words = normalized(raw).split()
    return previous_blank and next_blank and 1 <= len(words) <= 16 and len(normalized(raw)) <= 120


def validate_reason_diversity(rows: list[dict], field: str, label: str) -> str | None:
    """Return a warning for templated review reasons instead of blocking reports."""
    if len(rows) < 3:
        return None
    reasons = [re.sub(r"\s+", " ", str(row.get(field, "")).strip()).casefold() for row in rows]
    unique = set(reasons)
    most_common = max(reasons.count(reason) for reason in unique) if unique else 0
    minimum_unique = 2 if len(rows) < 8 else max(3, len(rows) // 5)
    if len(unique) < minimum_unique or (len(rows) >= 8 and most_common > len(rows) / 2):
        return (
            f"{label}: review reasons are excessively templated; "
            f"got {len(unique)} distinct reasons for {len(rows)} rows"
        )
    return None


def md_cell(value: object) -> str:
    return str(value).replace("|", "&#124;").replace("\r", " ").replace("\n", "<br>")


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "-", value).strip(" .-")
    return cleaned or "article"


def validate_prepared(
    prepared: dict,
    prepared_path: Path,
    article_override: Path | None = None,
    knowledge_override: list[Path] | None = None,
) -> tuple[Path, list[Path], dict[str, list[dict]]]:
    if str(prepared.get("schema_version")) != "1.0":
        raise ValueError(f"{prepared_path.name}: unsupported prepared schema_version")

    original_article_path = Path(str(prepared.get("article_file", "")))
    article_path = article_override or original_article_path
    if not article_path.is_file():
        raise ValueError(f"{prepared_path.name}: current article file is missing")
    original_knowledge_paths = [Path(str(path)) for path in prepared.get("knowledge_files", [])]
    knowledge_paths = knowledge_override or original_knowledge_paths
    if not knowledge_paths or any(not path.is_file() for path in knowledge_paths):
        raise ValueError(f"{prepared_path.name}: one or more current knowledge files are missing")
    if knowledge_override and len(knowledge_paths) != len(original_knowledge_paths):
        raise ValueError(f"{prepared_path.name}: knowledge override count does not match prepared files")

    current_article = extract_article(article_path)
    if prepared.get("article_lines") != current_article["article_lines"]:
        raise ValueError(f"{prepared_path.name}: prepared article content no longer matches the current article")
    prepared_units = [
        {"unit_id": item.get("unit_id"), "line": item.get("line"), "text": item.get("text")}
        for item in prepared.get("article_units", [])
    ]
    current_units = [
        {"unit_id": item.get("unit_id"), "line": item.get("line"), "text": item.get("text")}
        for item in current_article["article_units"]
    ]
    if prepared_units != current_units:
        raise ValueError(f"{prepared_path.name}: prepared article units no longer match the current article")
    for recorded, current in zip(prepared.get("article_units", []), current_article["article_units"]):
        for field in ("source_kind", "unit_type", "heading_level", "heading_source", "heading_style"):
            if field in recorded and recorded.get(field) != current.get(field):
                raise ValueError(f"{prepared_path.name}: prepared unit {field} no longer matches the current article")
        if "claim_review_signals" in recorded and recorded.get("claim_review_signals") != current.get("claim_review_signals"):
            raise ValueError(f"{prepared_path.name}: prepared unit review signals no longer match the current article")
    metadata_id = current_article.get("metadata_article_id")
    if metadata_id and prepared.get("article_id") != metadata_id:
        raise ValueError(f"{prepared_path.name}: article_id does not match current article metadata")
    metadata_version = current_article.get("metadata_article_version")
    prepared_version = str(prepared.get("article_version", "")).strip()
    if metadata_version and prepared_version and prepared_version != metadata_version:
        raise ValueError(f"{prepared_path.name}: article_version does not match current article metadata")
    recorded_article_hash = str(prepared.get("article_sha256", "")).strip().lower()
    if recorded_article_hash and recorded_article_hash != sha256_file(article_path):
        semantic_hash = prepared.get("article_content_sha256")
        semantic_match = (
            semantic_hash == article_content_sha256(current_article["article_lines"])
            if semantic_hash
            else prepared.get("article_lines") == current_article["article_lines"]
        )
        if not semantic_match:
            raise ValueError(f"{prepared_path.name}: article content no longer matches the current article")

    expected_chunks: list[dict] = []
    eligible_by_file: dict[str, list[dict]] = {}
    for path in knowledge_paths:
        current_chunks = extract_knowledge_chunks(path)
        eligible_by_file[normalized_path(path)] = [
            chunk for chunk in current_chunks if chunk.get("_evidence_eligible", True)
        ]
        expected_chunks.extend(public_chunk(chunk) for chunk in current_chunks)

    prepared_chunks = []
    source_aliases = {
        normalized_path(old): current.resolve()
        for old, current in zip(original_knowledge_paths, knowledge_paths)
    }
    for item in prepared.get("knowledge_chunks", []):
        source_file = normalized_path(item.get("source_file", ""))
        if source_file in source_aliases:
            source_file = normalized_path(source_aliases[source_file])
        prepared_chunks.append(
            {
                "source_file": source_file,
                "section": item.get("section", ""),
                "line_start": item.get("line_start"),
                "line_end": item.get("line_end"),
                "text": item.get("text", ""),
            }
        )
    normalized_expected = [
        {**item, "source_file": normalized_path(item["source_file"])} for item in expected_chunks
    ]
    if prepared_chunks != normalized_expected:
        raise ValueError(f"{prepared_path.name}: prepared knowledge chunks no longer match current files")
    recorded_knowledge_hashes = prepared.get("knowledge_sha256")
    if recorded_knowledge_hashes:
        actual_knowledge_hashes = [sha256_file(path) for path in knowledge_paths]
        if recorded_knowledge_hashes != actual_knowledge_hashes:
            raise ValueError(f"{prepared_path.name}: knowledge SHA-256 no longer matches current files")
    return article_path.resolve(), [path.resolve() for path in knowledge_paths], eligible_by_file


def validate_case(
    prepared: dict,
    judgments: dict,
    prepared_path: Path,
    judgment_path: Path,
    adversarial_review: dict | None = None,
    article_override: Path | None = None,
    knowledge_override: list[Path] | None = None,
) -> list[dict]:
    audit_warnings: list[dict] = []

    def record_warning(code: str, scope: str, message: str) -> None:
        audit_warnings.append({"code": code, "scope": scope, "message": message})

    article_path, knowledge_paths, eligible_by_file = validate_prepared(
        prepared, prepared_path, article_override, knowledge_override
    )
    judgment_schema = str(judgments.get("schema_version"))
    if judgment_schema not in {"1.0", "1.1"}:
        raise ValueError(f"{judgment_path.name}: unsupported judgment schema_version")
    protocol_version = str(judgments.get("evaluation_protocol_version", "1.0")).strip()
    if protocol_version not in {"1.0", "2.0"}:
        raise ValueError(f"{judgment_path.name}: unsupported evaluation_protocol_version")
    if protocol_version == "2.0" and judgment_schema != "1.1":
        raise ValueError(f"{judgment_path.name}: protocol 2.0 judgments must use schema_version 1.1")
    if protocol_version == "2.0" and not isinstance(judgments.get("penalize_ambiguous_claims"), bool):
        raise ValueError(f"{judgment_path.name}: protocol 2.0 must record penalize_ambiguous_claims")
    article_id = prepared["article_id"]
    if judgments.get("article_id") != article_id:
        raise ValueError(f"{judgment_path.name}: article_id does not match prepared JSON")
    if judgments.get("evaluation_mode") != MODE:
        raise ValueError(f"{judgment_path.name}: evaluation_mode must be the required non-official label")

    units = {unit["unit_id"]: unit for unit in prepared["article_units"]}
    source_paths = {normalized_path(path) for path in knowledge_paths}
    original_source_paths = [Path(str(path)).resolve() for path in prepared.get("knowledge_files", [])]
    source_aliases = {
        normalized_path(old): current
        for old, current in zip(original_source_paths, knowledge_paths)
    }
    article_source_lines = read_source_lines(article_path)
    seen: set[str] = set()
    claims = judgments.get("claims")
    if not isinstance(claims, list):
        raise ValueError(f"{judgment_path.name}: claims must be an array")

    def claim_is_traceable_to_quote(quote: str, atomic_claim: str) -> bool:
        """Reject obvious quote/claim swaps while allowing concise normalization."""
        quote_plain = normalized(quote).lower()
        claim_plain = normalized(atomic_claim).lower()
        ascii_terms = lambda value: {
            term for term in re.findall(r"[a-z0-9]+", value)
            if len(term) >= 3 and term not in {
                "the", "and", "for", "with", "that", "this", "from", "are", "was",
                "were", "has", "have", "can", "will", "into", "its", "your", "our",
            }
        }
        quote_terms, claim_terms = ascii_terms(quote_plain), ascii_terms(claim_plain)
        if quote_terms and claim_terms and quote_terms & claim_terms:
            return True
        quote_cjk = "".join(re.findall(r"[\u3400-\u9fff]", quote_plain))
        claim_cjk = "".join(re.findall(r"[\u3400-\u9fff]", claim_plain))
        if len(quote_cjk) >= 2 and len(claim_cjk) >= 2:
            quote_bigrams = {quote_cjk[i:i + 2] for i in range(len(quote_cjk) - 1)}
            claim_bigrams = {claim_cjk[i:i + 2] for i in range(len(claim_cjk) - 1)}
            if quote_bigrams & claim_bigrams:
                return True
        return quote_plain in claim_plain or claim_plain in quote_plain

    for index, claim in enumerate(claims, 1):
        claim_id = claim.get("claim_id")
        if not claim_id or claim_id in seen:
            raise ValueError(f"{judgment_path.name}: missing or duplicate claim_id at row {index}")
        expected_claim_id = f"C{index:03d}"
        if claim_id != expected_claim_id:
            raise ValueError(f"{judgment_path.name}: expected claim_id {expected_claim_id}, got {claim_id!r}")
        seen.add(claim_id)
        unit = units.get(claim.get("unit_id"))
        if not unit:
            raise ValueError(f"{judgment_path.name}: {claim_id} has an invalid unit_id")
        if unit.get("unit_type") in STRUCTURAL_UNIT_TYPES:
            raise ValueError(
                f"{judgment_path.name}: {claim_id} is attached to a structured {unit.get('unit_type')} unit; "
                "structured titles, headings and table headers cannot enter the claim denominator"
            )
        if claim.get("article_line") != unit["line"]:
            raise ValueError(f"{judgment_path.name}: {claim_id} article_line does not match its unit")
        quote = claim.get("article_quote", "")
        if not quote or normalized(quote) not in normalized(unit["text"]):
            raise ValueError(f"{judgment_path.name}: {claim_id} article_quote is not verbatim in its unit")
        raw_article_line = article_source_lines[unit["line"] - 1]
        if quote not in raw_article_line:
            raise ValueError(
                f"{judgment_path.name}: {claim_id} article_quote is not an exact substring of the current article line"
            )
        atomic_claim = str(claim.get("claim", "")).strip()
        if not atomic_claim:
            raise ValueError(f"{judgment_path.name}: {claim_id} has an empty atomic claim")
        if normalized(quote).casefold() == normalized(str(prepared.get("article_title", ""))).casefold():
            raise ValueError(f"{judgment_path.name}: {claim_id} treats the article title as a factual claim")
        if is_question(atomic_claim):
            raise ValueError(f"{judgment_path.name}: {claim_id} is a pure question and cannot be a factual claim")
        if looks_like_plain_heading(unit, article_source_lines):
            heading_note = str(claim.get("heading_override_reason", "")).strip()
            if len(heading_note) < 20:
                raise ValueError(
                    f"{judgment_path.name}: {claim_id} is attached to an isolated plain heading candidate; "
                    "exclude it or provide a concrete heading_override_reason showing the line is factual prose"
                )
        if (
            normalized(atomic_claim).casefold() == normalized(quote).casefold()
            and re.search(r"[;；]|\b(?:while|whereas)\b", quote, re.I)
            and len(str(claim.get("atomicity_note", "")).strip()) < 20
        ):
            raise ValueError(
                f"{judgment_path.name}: {claim_id} copies a compound-looking unit without an atomicity_note"
            )
        if not claim_is_traceable_to_quote(quote, atomic_claim):
            note = str(claim.get("derivation_note", "")).strip()
            if len(note) < 12:
                raise ValueError(
                    f"{judgment_path.name}: {claim_id} atomic claim is not traceable to article_quote; "
                    "the quote may be attached to the wrong unit"
                )
        verdict = claim.get("verdict")
        if verdict not in VALID_VERDICTS:
            raise ValueError(f"{judgment_path.name}: {claim_id} has invalid verdict {verdict!r}")
        if protocol_version == "2.0":
            deepeval_verdict = claim.get("deepeval_verdict")
            semantic_status = claim.get("semantic_status")
            if deepeval_verdict not in VALID_DEEPEVAL_VERDICTS:
                raise ValueError(
                    f"{judgment_path.name}: {claim_id} needs deepeval_verdict yes/no/idk"
                )
            if semantic_status not in VALID_SEMANTIC_STATUSES:
                raise ValueError(
                    f"{judgment_path.name}: {claim_id} needs semantic_status entailed/contradicted/unknown"
                )
            expected_semantic_status = {
                "yes": "entailed",
                "no": "contradicted",
                "idk": "unknown",
            }[deepeval_verdict]
            if semantic_status != expected_semantic_status:
                raise ValueError(
                    f"{judgment_path.name}: {claim_id} semantic_status must match deepeval_verdict"
                )
            expected_verdict = "supported" if semantic_status == "entailed" else "unsupported"
            if verdict != expected_verdict:
                raise ValueError(
                    f"{judgment_path.name}: {claim_id} verdict must derive from semantic_status"
                )
        reason = str(claim.get("reason", "")).strip()
        if len(reason) < 12:
            raise ValueError(f"{judgment_path.name}: {claim_id} needs a concrete verdict reason")
        evidence = claim.get("evidence", [])
        if not isinstance(evidence, list):
            raise ValueError(f"{judgment_path.name}: {claim_id} evidence must be an array")
        if verdict == "supported" and not evidence:
            raise ValueError(f"{judgment_path.name}: supported {claim_id} must include evidence")
        for item in evidence:
            source_file = item.get("source_file", "")
            source_path = Path(source_file).resolve() if source_file else None
            if source_path and normalized_path(source_path) not in source_paths:
                aliased = source_aliases.get(normalized_path(source_path))
                if aliased:
                    source_path = aliased
                    item["source_file"] = str(aliased)
            if not source_path or normalized_path(source_path) not in source_paths:
                raise ValueError(f"{judgment_path.name}: {claim_id} cites a file outside supplied knowledge context")
            if not item.get("quote"):
                raise ValueError(f"{judgment_path.name}: {claim_id} contains empty evidence quote")
            if not isinstance(item.get("line_start"), int) or not isinstance(item.get("line_end"), int):
                raise ValueError(f"{judgment_path.name}: {claim_id} evidence line numbers must be integers")
            line_start, line_end = item["line_start"], item["line_end"]
            source_lines = read_source_lines(source_path)
            if line_start < 1 or line_end < line_start or line_end > len(source_lines):
                raise ValueError(f"{judgment_path.name}: {claim_id} evidence line range is invalid")
            source_excerpt = "\n".join(source_lines[line_start - 1 : line_end])
            if item["quote"] not in source_excerpt:
                raise ValueError(
                    f"{judgment_path.name}: {claim_id} evidence quote is not an exact substring at the stated lines"
                )
            if verdict == "supported":
                eligible = any(
                    chunk["line_start"] <= line_start
                    and line_end <= chunk["line_end"]
                    for chunk in eligible_by_file.get(normalized_path(source_path), [])
                )
                if not eligible:
                    raise ValueError(
                        f"{judgment_path.name}: supported {claim_id} cites metadata or a non-support section"
                    )
    if prepared.get("integration_mode") == "manage-article-knowledge-v0.6":
        warning = validate_reason_diversity(claims, "reason", f"{judgment_path.name} verdicts")
        if warning:
            record_warning("templated_verdict_reason", "judgments", warning)
        claim_by_id = {claim["claim_id"]: claim for claim in claims}
        claim_unit_ids = {claim["unit_id"] for claim in claims}
        total_units = len(units)
        factual_units = len(claim_unit_ids)
        share = factual_units / total_units if total_units else 0.0
        candidate_ids = {
            item["unit_id"] for item in prepared.get("article_units", []) if item.get("candidate_evidence")
        }

        # Every managed run needs the independent unit pass.  Limiting this
        # requirement to very low factual coverage allowed a model to inflate
        # the share with weak claims and bypass the gate.
        review = judgments.get("coverage_review")
        if not isinstance(review, dict):
            record_warning(
                "missing_coverage_review",
                "coverage_review",
                f"{judgment_path.name}: coverage_review is missing; generated a conservative unit classification from claim rows",
            )
            classifications = []
            for unit_id, unit in units.items():
                if unit.get("unit_type") in STRUCTURAL_UNIT_TYPES:
                    classifications.append({
                        "unit_id": unit_id,
                        "classification": "non_factual",
                        "category": unit.get("unit_type"),
                        "reason": f"{unit_id} is a source-structured {unit.get('unit_type')} and is excluded deterministically.",
                    })
                elif unit_id in claim_unit_ids:
                    classifications.append({"unit_id": unit_id, "classification": "factual", "reason": f"{unit_id} has an extracted factual claim."})
                else:
                    category = (
                        "question" if is_question(unit["text"])
                        else "heading" if looks_like_plain_heading(unit, article_source_lines)
                        else "transition"
                    )
                    classifications.append({"unit_id": unit_id, "classification": "non_factual", "category": category, "reason": f"{unit_id} has no extracted atomic factual claim."})
            review = {
                "reviewed_unit_count": total_units,
                "factual_unit_count": factual_units,
                "non_factual_unit_count": total_units - factual_units,
                "unit_classifications": classifications,
                "reconsidered_candidate_unit_ids": list(candidate_ids),
            }
        if review.get("reviewed_unit_count") != total_units:
            raise ValueError(
                f"{judgment_path.name}: coverage_review.reviewed_unit_count must equal {total_units}"
            )
        classifications = review.get("unit_classifications")
        if not isinstance(classifications, list) or len(classifications) != total_units:
            raise ValueError(f"{judgment_path.name}: coverage_review must classify every article unit")
        by_id: dict[str, dict] = {}
        for row in classifications:
            if not isinstance(row, dict) or not row.get("unit_id"):
                raise ValueError(f"{judgment_path.name}: invalid coverage_review unit row")
            unit_id = row["unit_id"]
            if unit_id in by_id or unit_id not in units:
                raise ValueError(
                    f"{judgment_path.name}: coverage_review has duplicate or unknown unit_id {unit_id!r}"
                )
            classification = row.get("classification")
            reason = str(row.get("reason", "")).strip()
            if classification not in {"factual", "non_factual"} or len(reason) < 12:
                raise ValueError(
                    f"{judgment_path.name}: each coverage_review row needs classification and a concrete reason"
                )
            structural_type = units[unit_id].get("unit_type")
            if structural_type in STRUCTURAL_UNIT_TYPES:
                if classification != "non_factual" or row.get("category") != structural_type:
                    raise ValueError(
                        f"{judgment_path.name}: structured {structural_type} unit {unit_id} must be "
                        f"non_factual with category '{structural_type}'"
                    )
            if classification == "non_factual":
                category = str(row.get("category", "")).strip()
                if category not in NON_FACTUAL_CATEGORIES:
                    raise ValueError(
                        f"{judgment_path.name}: non_factual {unit_id} needs a valid exclusion category"
                    )
                if EVIDENCE_ABSENCE_REASON.search(reason):
                    raise ValueError(
                        f"{judgment_path.name}: non_factual {unit_id} is justified by missing evidence; "
                        "factuality must be decided independently of support"
                    )
                if is_question(units[unit_id]["text"]) and category != "question":
                    raise ValueError(f"{judgment_path.name}: question unit {unit_id} must use category 'question'")
                if category == "question" and not is_question(units[unit_id]["text"]):
                    raise ValueError(f"{judgment_path.name}: non-question unit {unit_id} cannot use category 'question'")
                if looks_like_plain_heading(units[unit_id], article_source_lines) and category != "heading":
                    raise ValueError(
                        f"{judgment_path.name}: isolated plain heading candidate {unit_id} must use category 'heading'"
                    )
                signals = set(units[unit_id].get("claim_review_signals") or [])
                strong_signals = signals & {
                    "effect_or_causal_relation", "operational_method", "comparison", "condition_or_scope"
                }
                if strong_signals:
                    record_warning(
                        "signaled_unit_excluded",
                        "coverage_review",
                        f"{judgment_path.name}: {unit_id} was excluded despite verifiable-claim review signals: "
                        + ", ".join(sorted(strong_signals)),
                    )
            by_id[unit_id] = row
        if set(by_id) != set(units):
            raise ValueError(f"{judgment_path.name}: coverage_review unit IDs do not match prepared units")
        non_factual_rows = [row for row in by_id.values() if row["classification"] == "non_factual"]
        warning = validate_reason_diversity(
            non_factual_rows,
            "reason",
            f"{judgment_path.name} non_factual coverage_review",
        )
        if warning:
            record_warning("templated_coverage_reason", "coverage_review", warning)
        declared_factual = sum(row["classification"] == "factual" for row in by_id.values())
        declared_non_factual = total_units - declared_factual
        if review.get("factual_unit_count") != declared_factual or review.get("non_factual_unit_count") != declared_non_factual:
            raise ValueError(f"{judgment_path.name}: coverage_review counts do not match classifications")
        missing_claims = [unit_id for unit_id, row in by_id.items() if row["classification"] == "factual" and unit_id not in claim_unit_ids]
        unexpected_claims = [unit_id for unit_id, row in by_id.items() if row["classification"] == "non_factual" and unit_id in claim_unit_ids]
        if missing_claims:
            raise ValueError(
                f"{judgment_path.name}: factual coverage rows without claims: {', '.join(sorted(missing_claims))}"
            )
        if unexpected_claims:
            raise ValueError(
                f"{judgment_path.name}: non_factual coverage rows with claims: {', '.join(sorted(unexpected_claims))}"
            )
        excluded_candidates = candidate_ids - claim_unit_ids
        reconsidered = set(review.get("reconsidered_candidate_unit_ids") or [])
        if not reconsidered.issubset(set(units)):
            raise ValueError(f"{judgment_path.name}: coverage_review has unknown reconsidered unit IDs")
        if not excluded_candidates.issubset(reconsidered):
            missing = ", ".join(sorted(excluded_candidates - reconsidered))
            raise ValueError(
                f"{judgment_path.name}: candidate-bearing units excluded without explicit reconsideration: {missing}"
            )

        claim_by_id = {claim["claim_id"]: claim for claim in claims}
        if protocol_version == "2.0":
            if not isinstance(adversarial_review, dict):
                record_warning("missing_adversarial_review", "adversarial_review", f"{judgment_path.name}: protocol 2.0 adversarial review artifact is missing")
                adversarial_review = {}
            if adversarial_review.get("schema_version") != "1.0":
                record_warning("invalid_adversarial_schema", "adversarial_review", f"{judgment_path.name}: unsupported adversarial review schema_version")
            if adversarial_review.get("article_id") != article_id:
                record_warning("adversarial_identity_mismatch", "adversarial_review", f"{judgment_path.name}: adversarial review article_id does not match")
            if adversarial_review.get("evaluation_protocol_version") != "2.0":
                record_warning("adversarial_protocol_mismatch", "adversarial_review", f"{judgment_path.name}: adversarial review protocol does not match judgments")
            if adversarial_review.get("review_mode") != "independent_adversarial":
                record_warning("adversarial_mode_mismatch", "adversarial_review", f"{judgment_path.name}: adversarial review is not independent_adversarial")
            second_coverage = adversarial_review.get("coverage_review")
            second_rows = second_coverage.get("unit_classifications") if isinstance(second_coverage, dict) else None
            if not isinstance(second_rows, list) or {
                str(row.get("unit_id")) for row in second_rows if isinstance(row, dict)
            } != set(units):
                record_warning(
                    "incomplete_second_pass_coverage",
                    "adversarial_review",
                    f"{judgment_path.name}: second pass did not independently classify every article unit; conservative denominator fallback was applied",
                )
            if not isinstance(adversarial_review.get("claim_inventory"), list):
                record_warning(
                    "missing_second_pass_inventory",
                    "adversarial_review",
                    f"{judgment_path.name}: second pass claim_inventory is missing; rule-based fallback cannot prove atomic completeness",
                )
            reconciliation = judgments.get("reconciliation")
            alignment_warnings = (
                reconciliation.get("alignment_warnings") if isinstance(reconciliation, dict) else None
            ) or []
            if alignment_warnings:
                preview = "；".join(str(item) for item in alignment_warnings[:8])
                suffix = f"；另有{len(alignment_warnings) - 8}项" if len(alignment_warnings) > 8 else ""
                record_warning(
                    "claim_alignment_repaired",
                    "reconciliation",
                    f"{judgment_path.name}: 已按正文命题自动去重、重绑或隔离{len(alignment_warnings)}项：{preview}{suffix}",
                )
            coverage_warning_ids = (
                reconciliation.get("coverage_warning_unit_ids") if isinstance(reconciliation, dict) else None
            ) or []
            if coverage_warning_ids:
                record_warning(
                    "coverage_fallback_may_under_count",
                    "coverage_review",
                    f"{judgment_path.name}: whole-unit fallback preserved {', '.join(coverage_warning_ids)} as unknown; compound units may contain additional atomic claims",
                )
            review_rows = adversarial_review.get("claim_reviews")
            if not isinstance(review_rows, list) or len(review_rows) != len(claims):
                record_warning("incomplete_adversarial_review", "adversarial_review", f"{judgment_path.name}: adversarial review does not challenge every claim")
                review_rows = review_rows if isinstance(review_rows, list) else []
            review_by_id: dict[str, dict] = {}
            for row in review_rows:
                claim_id = row.get("claim_id") if isinstance(row, dict) else None
                if claim_id not in claim_by_id or claim_id in review_by_id:
                    record_warning("invalid_adversarial_claim_row", "adversarial_review", f"{judgment_path.name}: invalid or duplicate adversarial claim review")
                    continue
                claim = claim_by_id[claim_id]
                if not has_claim_identity(row) or claim_identity_key(row) != claim_identity_key(claim):
                    record_warning("invalid_adversarial_claim_identity", "adversarial_review", f"{judgment_path.name}: adversarial review {claim_id} does not carry the same full proposition identity")
                if row.get("semantic_status") != claim.get("semantic_status"):
                    record_warning("adversarial_verdict_disagreement", "adversarial_review", f"{judgment_path.name}: adversarial review disagrees with {claim_id}")
                if len(str(row.get("challenge_reason", "")).strip()) < 20:
                    record_warning("short_adversarial_reason", "adversarial_review", f"{judgment_path.name}: adversarial review {claim_id} needs a concrete reason")
                if not str(row.get("claim_fragment", "")).strip() or normalized(row["claim_fragment"]) not in normalized(claim["claim"]):
                    record_warning("invalid_adversarial_claim_fragment", "adversarial_review", f"{judgment_path.name}: adversarial review {claim_id} has invalid claim_fragment")
                if claim.get("semantic_status") == "entailed":
                    evidence_fragment = str(row.get("evidence_fragment", "")).strip()
                    evidence_quotes = [str(item.get("quote", "")) for item in claim.get("evidence", [])]
                    if not evidence_fragment or not any(evidence_fragment in quote for quote in evidence_quotes):
                        record_warning("missing_adversarial_evidence_fragment", "adversarial_review", f"{judgment_path.name}: adversarial review {claim_id} must cite evidence")
                review_by_id[claim_id] = row
            if set(review_by_id) != set(claim_by_id):
                record_warning("adversarial_claim_ids_mismatch", "adversarial_review", f"{judgment_path.name}: adversarial review claim IDs do not match judgments")
            warning = validate_reason_diversity(
                list(review_by_id.values()),
                "challenge_reason",
                f"{judgment_path.name} adversarial review",
            )
            if warning:
                record_warning("templated_challenge_reason", "adversarial_review", warning)
            if claims and all(claim.get("semantic_status") == "entailed" for claim in claims):
                perfect = adversarial_review.get("all_supported_challenge")
                if not isinstance(perfect, dict) or perfect.get("performed") is not True:
                    record_warning("missing_all_supported_challenge", "adversarial_review", f"{judgment_path.name}: 100% result has no complete all_supported_challenge")
                if not isinstance(perfect, dict) or perfect.get("challenged_claim_ids") != [claim["claim_id"] for claim in claims]:
                    record_warning("incomplete_all_supported_challenge", "adversarial_review", f"{judgment_path.name}: all_supported_challenge does not list every claim in order")
                if not isinstance(perfect, dict) or len(str(perfect.get("conclusion", "")).strip()) < 30:
                    record_warning("short_all_supported_conclusion", "adversarial_review", f"{judgment_path.name}: all_supported_challenge needs a concrete conclusion")
        quality = judgments.get("quality_review")
        if protocol_version != "2.0" and not isinstance(quality, dict):
            record_warning("missing_quality_review", "quality_review", f"{judgment_path.name}: legacy managed audit has no quality_review")
            quality = {}
        if protocol_version == "2.0":
            judgments["audit_quality"] = "warning" if audit_warnings else "passed"
            judgments["audit_warnings"] = audit_warnings
            return claims
        if quality.get("gate_version") != MANAGED_QUALITY_GATE_VERSION:
            record_warning("invalid_quality_gate", "quality_review", f"{judgment_path.name}: unsupported quality_review gate_version")
        if quality.get("review_mode") != "independent_adversarial":
            record_warning("invalid_quality_mode", "quality_review", f"{judgment_path.name}: quality_review is not independent_adversarial")
        if quality.get("reviewed_claim_count") != len(claims):
            record_warning("incomplete_quality_review", "quality_review", f"{judgment_path.name}: quality_review does not cover every claim")
        challenge_rows = quality.get("claim_reviews")
        if not isinstance(challenge_rows, list) or len(challenge_rows) != len(claims):
            record_warning("incomplete_quality_review", "quality_review", f"{judgment_path.name}: quality_review must challenge every claim")
            challenge_rows = challenge_rows if isinstance(challenge_rows, list) else []
        challenge_by_id: dict[str, dict] = {}
        for row in challenge_rows:
            if not isinstance(row, dict) or row.get("claim_id") not in claim_by_id:
                record_warning("invalid_quality_claim_row", "quality_review", f"{judgment_path.name}: quality_review contains an invalid claim review")
                continue
            claim_id = row["claim_id"]
            if claim_id in challenge_by_id:
                record_warning("duplicate_quality_claim_row", "quality_review", f"{judgment_path.name}: quality_review repeats {claim_id}")
                continue
            claim = claim_by_id[claim_id]
            if row.get("confirmed_verdict") != claim["verdict"]:
                record_warning("quality_verdict_disagreement", "quality_review", f"{judgment_path.name}: quality_review disagrees with {claim_id}")
            challenge_reason = str(row.get("challenge_reason", "")).strip()
            if len(challenge_reason) < 20 or normalized(challenge_reason) == normalized(str(claim.get("reason", ""))):
                record_warning("short_quality_reason", "quality_review", f"{judgment_path.name}: quality_review {claim_id} needs a distinct, concrete challenge reason")
            claim_fragment = str(row.get("claim_fragment", "")).strip()
            if not claim_fragment or normalized(claim_fragment) not in normalized(str(claim["claim"])):
                record_warning("invalid_quality_claim_fragment", "quality_review", f"{judgment_path.name}: quality_review {claim_id} claim_fragment is invalid")
            evidence_fragment = str(row.get("evidence_fragment", "")).strip()
            if claim["verdict"] == "supported":
                evidence_quotes = [str(item.get("quote", "")) for item in claim.get("evidence", [])]
                if not evidence_fragment or not any(evidence_fragment in quote for quote in evidence_quotes):
                    record_warning("missing_quality_evidence_fragment", "quality_review", f"{judgment_path.name}: quality_review {claim_id} must align a fragment from cited evidence")
            challenge_by_id[claim_id] = row
        if set(challenge_by_id) != set(claim_by_id):
            record_warning("quality_claim_ids_mismatch", "quality_review", f"{judgment_path.name}: quality_review claim IDs do not match judgments")
        warning = validate_reason_diversity(
            challenge_rows,
            "challenge_reason",
            f"{judgment_path.name} quality_review",
        )
        if warning:
            record_warning("templated_challenge_reason", "quality_review", warning)

        if claims and all(claim["verdict"] == "supported" for claim in claims):
            perfect = quality.get("all_supported_challenge")
            if not isinstance(perfect, dict) or perfect.get("performed") is not True:
                record_warning("missing_all_supported_challenge", "quality_review", f"{judgment_path.name}: 100% result has no complete all_supported_challenge")
            challenged_ids = perfect.get("challenged_claim_ids") if isinstance(perfect, dict) else None
            if challenged_ids != [claim["claim_id"] for claim in claims]:
                record_warning("incomplete_all_supported_challenge", "quality_review", f"{judgment_path.name}: all_supported_challenge does not list every claim in order")
            if not isinstance(perfect, dict) or len(str(perfect.get("conclusion", "")).strip()) < 30:
                record_warning("short_all_supported_conclusion", "quality_review", f"{judgment_path.name}: all_supported_challenge needs a concrete conclusion")
    judgments["audit_quality"] = "warning" if audit_warnings else "passed"
    judgments["audit_warnings"] = audit_warnings
    return claims


def evidence_markdown(claim: dict) -> str:
    if not claim.get("evidence"):
        return "—"
    parts = []
    for evidence in claim["evidence"]:
        location = f"{evidence['source_file']}:{evidence['line_start']}"
        if evidence["line_end"] != evidence["line_start"]:
            location += f"–{evidence['line_end']}"
        parts.append(f"“{evidence['quote']}”<br>{location}")
    return "<br><br>".join(parts)


def content_summary(case: dict) -> dict:
    """Classify article sentence/table-row units without changing claim scoring."""
    claims_by_unit: dict[str, list[dict]] = {}
    for claim in case["claims"]:
        claims_by_unit.setdefault(claim["unit_id"], []).append(claim)
    unit_rows = []
    for item in case["prepared"]["article_units"]:
        unit_claims = claims_by_unit.get(item["unit_id"], [])
        semantic_statuses = {c.get("semantic_status") for c in unit_claims}
        if item.get("unit_type") in STRUCTURAL_UNIT_TYPES:
            kind, status, semantic_summary = "structural", "non_factual", "structural"
        elif not unit_claims:
            kind, status, semantic_summary = "non_factual", "non_factual", "non_factual"
        elif all(c["verdict"] == "supported" for c in unit_claims):
            kind, status, semantic_summary = "factual", "fully_supported", "entailed"
        elif any(c["verdict"] == "supported" for c in unit_claims):
            kind, status = "factual", "partially_supported"
            semantic_summary = "has_contradicted" if "contradicted" in semantic_statuses else "mixed_unknown"
        elif "contradicted" in semantic_statuses:
            kind, status, semantic_summary = "factual", "unsupported", "has_contradicted"
        elif "unknown" in semantic_statuses:
            kind, status, semantic_summary = "factual", "unsupported", "all_unknown"
        else:
            kind, status, semantic_summary = "factual", "unsupported", "legacy_unsupported"
        unit_rows.append({**item, "kind": kind, "status": status, "semantic_summary": semantic_summary, "claims": unit_claims})
    claims_by_line: dict[int, list[dict]] = {}
    for row in unit_rows:
        claims_by_line.setdefault(row["line"], []).extend(row["claims"])
    line_rows = []
    for item in case["prepared"]["article_lines"]:
        line_claims = claims_by_line.get(item["line"], [])
        semantic_statuses = {c.get("semantic_status") for c in line_claims}
        if not line_claims:
            kind, status, semantic_summary = "non_factual", "non_factual", "non_factual"
        elif all(c["verdict"] == "supported" for c in line_claims):
            kind, status, semantic_summary = "factual", "fully_supported", "entailed"
        elif any(c["verdict"] == "supported" for c in line_claims):
            kind, status = "factual", "partially_supported"
            semantic_summary = "has_contradicted" if "contradicted" in semantic_statuses else "mixed_unknown"
        elif "contradicted" in semantic_statuses:
            kind, status, semantic_summary = "factual", "unsupported", "has_contradicted"
        elif "unknown" in semantic_statuses:
            kind, status, semantic_summary = "factual", "unsupported", "all_unknown"
        else:
            kind, status, semantic_summary = "factual", "unsupported", "legacy_unsupported"
        line_rows.append({**item, "kind": kind, "status": status, "semantic_summary": semantic_summary, "claims": line_claims})
    content_rows = [row for row in unit_rows if row["kind"] != "structural"]
    factual = [row for row in content_rows if row["kind"] == "factual"]
    return {
        "total_units": len(content_rows),
        "factual_units": len(factual),
        "non_factual_units": len(content_rows) - len(factual),
        "structural_units": len(unit_rows) - len(content_rows),
        "factual_share": len(factual) / len(content_rows) * 100 if content_rows else None,
        "fully_supported_units": sum(row["status"] == "fully_supported" for row in unit_rows),
        "partially_supported_units": sum(row["status"] == "partially_supported" for row in unit_rows),
        "unsupported_units": sum(row["status"] == "unsupported" for row in unit_rows),
        "all_unknown_units": sum(row["semantic_summary"] == "all_unknown" for row in unit_rows),
        "contradicted_units": sum(row["semantic_summary"] == "has_contradicted" for row in unit_rows),
        "rows": unit_rows,
        "line_rows": line_rows,
    }


def write_detail(case: dict, output_dir: Path) -> Path:
    prepared, claims = case["prepared"], case["claims"]
    supported = sum(claim["verdict"] == "supported" for claim in claims)
    total = len(claims)
    rate = supported / total * 100 if total else None
    content = content_summary(case)
    lines = [
        f"# {prepared['article_id']} 文章知识库支持明细",
        "",
        f"> 评审模式：{MODE}",
        "",
        f"> 审核质量：{case['judgments'].get('audit_quality', 'passed')}（质量提示不改变知识附件明确支撑率，也不创建人工待办）",
        "",
        f"> 计算：{supported} ÷ {total} = {rate:.2f}%" if rate is not None else "> 计算：分母为 0，结果 N/A",
        "",
        f"- 文章：`{prepared['article_file']}`",
        *[f"- 知识文件：`{path}`" for path in prepared["knowledge_files"]],
        "",
        "## 内容单元识别",
        "",
        "> 本节先展示哪些正文句子或表格行包含可验证主张，再展示其下拆分的原子主张。表格字段仍按原子主张逐条核验。",
        "",
        f"> 可验证内容单元：{content['factual_units']} / {content['total_units']}（{content['factual_share']:.2f}%）" if content["factual_share"] is not None else "> 可验证内容单元：0 / 0",
        "",
        "| 文章行 | 内容分类 | 语义状态 | 原文内容 | 原子主张数 |",
        "|---:|---|---|---|---:|",
    ]
    warnings = case["judgments"].get("audit_warnings") or []
    if warnings:
        lines += [
            "## 审核质量提示", "",
            "> 以下提示仅说明审核过程质量，不阻塞报告生成，不要求逐条人工处理。", "",
            "| 范围 | 代码 | 说明 |", "|---|---|---|",
        ]
        lines += [
            f"| {md_cell(item.get('scope', ''))} | {md_cell(item.get('code', ''))} | {md_cell(item.get('message', ''))} |"
            for item in warnings
        ]
        lines.append("")
    semantic_labels = {
        "structural": "结构内容，不计入",
        "non_factual": "非主张内容，不计入",
        "entailed": "可验证主张：全部明确支持",
        "mixed_unknown": "可验证主张：部分尚未确认",
        "all_unknown": "可验证主张：全部尚未确认（证据不足）",
        "has_contradicted": "可验证主张：存在明确冲突",
        "legacy_unsupported": "可验证主张：旧协议未细分黄色与红色",
    }
    for row in content["rows"]:
        lines.append(
            "| " + " | ".join(
                md_cell(value)
                for value in (
                    row["line"],
                    "可验证主张" if row["kind"] == "factual" else "结构内容" if row["kind"] == "structural" else "非主张内容",
                    semantic_labels[row["semantic_summary"]],
                    row["text"],
                    len(row["claims"]),
                )
            ) + " |"
        )
    lines += [
        "",
        "## 原子事实主张明细",
        "",
        "| 主张 | 文章行 | 文章原文 | 原子事实主张 | 语义状态 | 知识库原文与位置 | 理由 |",
        "|---|---:|---|---|---|---|---|",
    ]
    for claim in claims:
        semantic = {
            "entailed": "明确支持",
            "unknown": "尚未确认（证据不足）",
            "contradicted": "明确冲突",
        }.get(claim.get("semantic_status"), "旧协议未细分黄色与红色")
        lines.append(
            "| "
            + " | ".join(
                md_cell(value)
                for value in (
                    claim["claim_id"],
                    claim["article_line"],
                    claim["article_quote"],
                    claim["claim"],
                    semantic,
                    evidence_markdown(claim),
                    claim.get("reason", ""),
                )
            )
            + " |"
        )
    output = output_dir / f"{safe_name(prepared['article_id'])}_faithfulness_details.md"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def summary_row(case: dict) -> dict:
    claims = case["claims"]
    scores = result_scores(claims, bool(case["judgments"].get("penalize_ambiguous_claims", False)))
    total = len(claims)
    return {
        "article_id": case["prepared"]["article_id"],
        "title": case["prepared"]["article_title"],
        "supported": scores["strict_supported"],
        "total": total,
        "unsupported": total - scores["strict_supported"],
        "rate": scores["strict_rate"],
        "deepeval_supported": scores["deepeval_supported"],
        "deepeval_rate": scores["deepeval_rate"],
        "unknown": scores["unknown"],
        "contradicted": scores["contradicted"],
        "mode": MODE,
        "audit_quality": case["judgments"].get("audit_quality", "passed"),
        "audit_warning_count": len(case["judgments"].get("audit_warnings") or []),
    }


def write_summary(cases: list[dict], output_dir: Path) -> Path:
    lines = [
        "# 文章知识库支持率汇总",
        "",
        f"> 评审模式：{MODE}",
        "",
        "> 正式指标公式：明确支持 ÷（明确支持 + 尚未确认 + 明确冲突）。非主张内容不计入。该指标没有目标值，不判断文章质量、发布资格或是否需要重写。",
        "> 可验证内容占比是辅助诊断：含可验证主张的句子/表格行 ÷ 正文内容单元；它不替代 Faithfulness，也不表示文字来源比例。",
        "",
        "| 文章编号 | 文章标题 | 明确支持主张数（分子） | 全部可验证主张数（分母） | 尚未确认与明确冲突合计 | 知识附件明确支撑率 | 评审模式 | 尚未确认（黄色） | 明确冲突（红色） |",
        "|---|---|---:|---:|---:|---:|---|---:|---:|",
    ]
    for case in cases:
        row = summary_row(case)
        rate = f"{row['rate']:.2f}%" if row["rate"] is not None else "N/A"
        lines.append(
            "| "
            + " | ".join(
                md_cell(value)
                for value in (
                    row["article_id"], row["title"], row["supported"], row["total"], row["unsupported"], rate, row["mode"], row["unknown"], row["contradicted"]
                )
            )
            + " |"
        )
    warning_rows = [summary_row(case) for case in cases if summary_row(case)["audit_warning_count"]]
    if warning_rows:
        lines += [
            "", "## 审核质量提示（不阻塞）", "",
        "> 质量提示只记录审核过程中的模板化或完整性风险，不改变知识附件明确支撑率，也不创建人工逐条待办。", "",
            "| 文章编号 | 审核质量 | 提示数 |", "|---|---|---:|",
        ]
        lines += [
            f"| {md_cell(row['article_id'])} | {md_cell(row['audit_quality'])} | {row['audit_warning_count']} |"
            for row in warning_rows
        ]
    lines += [
        "",
        "## 内容单元诊断（不改变上方主指标）",
        "",
        "| 文章编号 | 正文内容单元 | 可验证内容单元 | 可验证内容占比 | 全部有证据支持 | 部分有证据支持 | 全部证据不足 | 存在明确冲突 | 非主张内容不计入 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case in cases:
        row = summary_row(case)
        content = content_summary(case)
        share = f"{content['factual_share']:.2f}%" if content["factual_share"] is not None else "N/A"
        lines.append(
            "| " + " | ".join(
                md_cell(value)
                for value in (
                    row["article_id"], content["total_units"], content["factual_units"], share,
                    content["fully_supported_units"], content["partially_supported_units"],
                    content["all_unknown_units"], content["contradicted_units"], content["non_factual_units"],
                )
            ) + " |"
        )
    output = output_dir / "faithfulness_summary.md"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def claim_card(claim: dict) -> str:
    supported = claim["verdict"] == "supported"
    semantic_status = claim.get("semantic_status")
    semantic_labels = {
        "entailed": "明确支持",
        "unknown": "尚未确认（证据不足）",
        "contradicted": "明确冲突",
    }
    label = semantic_labels.get(semantic_status, "明确支持" if supported else "旧协议未细分黄色与红色")
    semantic_class = {
        "entailed": "entailed",
        "unknown": "unknown",
        "contradicted": "contradicted",
    }.get(semantic_status, "legacy")
    evidence = ""
    if claim.get("evidence"):
        blocks = []
        for item in claim["evidence"]:
            name = html.escape(Path(item["source_file"]).name)
            line = f"L{item['line_start']}" if item["line_start"] == item["line_end"] else f"L{item['line_start']}–L{item['line_end']}"
            blocks.append(
                f'<blockquote>{html.escape(item["quote"])}</blockquote>'
                f'<div class="source">{name} · {line}</div>'
            )
        evidence = "".join(blocks)
    else:
        evidence = '<div class="empty">提供的知识文件中未找到可确认该主张的证据</div>'
    return f'''
    <details class="claim {semantic_class} {claim['verdict']}" data-verdict="{claim['verdict']}" data-semantic-status="{html.escape(str(claim.get('semantic_status', 'legacy')))}" open>
       <summary><span class="badge">{label}</span><span class="claim-id">{html.escape(claim['claim_id'])}</span>{html.escape(claim['article_quote'])}</summary>
      <div class="claim-body">
        <div><strong>拆分后的事实主张</strong><p>{html.escape(claim['claim'])}</p></div>
        <div><strong>判断理由</strong><p>{html.escape(claim.get('reason', ''))}</p></div>
        <div><strong>对应知识库原文</strong>{evidence}</div>
      </div>
    </details>'''


def article_section(case: dict, index: int) -> str:
    prepared, claims = case["prepared"], case["claims"]
    content = content_summary(case)
    by_unit: dict[str, list[dict]] = {}
    for claim in claims:
        by_unit.setdefault(claim["unit_id"], []).append(claim)
    row = summary_row(case)
    rate = f"{row['rate']:.2f}%" if row["rate"] is not None else "N/A"
    factual_share = f"{content['factual_share']:.2f}%" if content["factual_share"] is not None else "N/A"
    body = []
    status_labels = {
        "non_factual": "非主张内容 · 不计入",
        "fully_supported": "可验证主张 · 全部明确支持",
        "partially_supported": "可验证主张 · 部分尚未确认",
        "unsupported": "可验证主张 · 尚未确认或明确冲突",
    }
    for content_row in content["rows"]:
        item = content_row
        unit_claims = by_unit.get(item["unit_id"], [])
        verdict_class = content_row["status"].replace("_", "-")
        semantic_status_labels = {
        "entailed": "可验证主张 · 全部明确支持",
        "mixed_unknown": "可验证主张 · 部分尚未确认",
        "all_unknown": "可验证主张 · 全部尚未确认（证据不足）",
        "has_contradicted": "可验证主张 · 存在明确冲突",
        "legacy_unsupported": "可验证主张 · 旧协议未细分黄色与红色",
        "non_factual": "非主张内容 · 不计入",
        "structural": "结构内容 · 不计入",
        }
        cards = "".join(claim_card(claim) for claim in unit_claims)
        body.append(
        f'<section class="article-line article-unit {verdict_class} {content_row["semantic_summary"]}" data-kind="{content_row["kind"]}" data-status="{content_row["status"]}" data-semantic-summary="{content_row["semantic_summary"]}" data-unit-id="{html.escape(item["unit_id"])}">'
            f'<div class="line-no">L{item["line"]} · {html.escape(item["unit_id"])}</div>'
            f'<div class="unit-meta"><span class="unit-status {verdict_class}">{semantic_status_labels.get(content_row["semantic_summary"], status_labels[content_row["status"]])}</span>'
            f'<span class="unit-count">{len(unit_claims)} 条原子主张</span></div>'
            f'<p class="article-text">{html.escape(item["text"])}</p>{cards}</section>'
        )
    active = " active" if index == 0 else ""
    return f'''
    <article class="article-panel{active}" id="panel-{index}">
      <header class="article-head">
        <div><div class="eyebrow">{html.escape(prepared['article_id'])}</div><h2>{html.escape(prepared['article_title'])}</h2></div>
         <div class="score"><strong>{rate}</strong><span>知识附件明确支撑率 · {row['supported']} / {row['total']}</span><span>尚未确认：{row['unknown']} · 明确冲突：{row['contradicted']}</span><span>可验证内容：{content['factual_units']} / {content['total_units']} 个单元（{factual_share}）</span></div>
      </header>
      <div class="article-body">{"".join(body)}</div>
    </article>'''


def write_html(cases: list[dict], output_dir: Path) -> Path:
    tabs = "".join(
        f'<button class="tab{" active" if index == 0 else ""}" data-target="panel-{index}">{html.escape(case["prepared"]["article_id"])}</button>'
        for index, case in enumerate(cases)
    )
    panels = "".join(article_section(case, index) for index, case in enumerate(cases))
    row_html = []
    for case in cases:
        row = summary_row(case)
        content = content_summary(case)
        strict_rate = f"{row['rate']:.2f}%" if row["rate"] is not None else "N/A"
        factual_share = f"{content['factual_share']:.2f}%" if content["factual_share"] is not None else "N/A"
        row_html.append(
            f'<tr><td>{html.escape(row["article_id"])}</td><td>{html.escape(row["title"])}</td>'
            f'<td>{row["supported"]}</td><td>{row["total"]}</td>'
            f'<td>{row["unknown"]}</td><td>{row["contradicted"]}</td><td><strong>{strict_rate}</strong></td>'
            f'<td>{content["factual_units"]}/{content["total_units"]}</td>'
            f'<td>{factual_share}</td></tr>'
        )
    rows = "".join(row_html)
    warning_total = sum(len(case["judgments"].get("audit_warnings") or []) for case in cases)
    warning_notice = (
        f'<div class="notice warning"><strong>审核质量提示：{warning_total} 条</strong><br>'
        '提示不改变知识附件明确支撑率，不创建人工逐条待办；报告仍按已生成的判断继续输出。</div>'
        if warning_total else ""
    )
    document = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:;">
<title>文章知识库支持率可视化</title>
<style>
:root{{--bg:#f4f7fb;--card:#fff;--ink:#172033;--muted:#667085;--line:#dce3ec;--green:#117a4b;--green-bg:#e9f8f0;--amber:#9a6700;--amber-bg:#fff7d6;--red:#b42318;--red-bg:#fff0ee;--blue:#2856a3}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 Inter,"Segoe UI","Microsoft YaHei",sans-serif}}
.shell{{max-width:1180px;margin:auto;padding:32px 20px 64px}}h1{{margin:0 0 8px;font-size:30px}}.subtitle{{color:var(--muted);margin:0 0 24px}}
.notice{{background:#fff7df;border:1px solid #f0d78a;border-radius:12px;padding:13px 16px;margin-bottom:18px}}
.summary{{width:100%;border-collapse:collapse;background:var(--card);border-radius:14px;overflow:hidden;box-shadow:0 8px 24px #253b5b12}}
.summary th,.summary td{{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left}}.summary th{{background:#eef3fa;color:#42526b;font-size:13px}}
.toolbar{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:24px 0 14px}}button{{border:1px solid var(--line);background:white;border-radius:999px;padding:8px 15px;cursor:pointer;color:var(--ink)}}button.active{{background:var(--blue);border-color:var(--blue);color:white}}
.filters{{margin-left:auto;display:flex;gap:8px}}.article-panel{{display:none}}.article-panel.active{{display:block}}
.article-head{{display:flex;justify-content:space-between;gap:22px;align-items:flex-start;background:var(--card);padding:22px;border-radius:16px 16px 0 0;border-bottom:1px solid var(--line)}}
 .article-head h2{{margin:2px 0 0;font-size:24px}}.eyebrow{{color:var(--blue);font-weight:700;letter-spacing:.04em}}.score{{min-width:210px;text-align:right}}.score strong{{display:block;font-size:34px;color:var(--blue)}}.score span{{display:block;color:var(--muted)}}.diagnostic{{margin-top:8px;color:var(--muted);font-size:12px}}.diagnostic summary{{cursor:pointer}}
.article-body{{background:var(--card);padding:8px 22px 28px;border-radius:0 0 16px 16px;box-shadow:0 8px 24px #253b5b12}}
.article-line{{position:relative;padding:18px 18px 16px 104px;border-bottom:1px solid #edf0f4}}.line-no{{position:absolute;left:8px;top:22px;color:#98a2b3;font:12px ui-monospace,monospace;white-space:nowrap}}.article-text{{margin:7px 0 12px;font-family:Georgia,"Times New Roman",serif;font-size:17px}}
.unit-meta{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12px}}.unit-status{{border-radius:999px;padding:2px 9px;font-weight:700}}.unit-count{{color:var(--muted)}}
.fully-supported .unit-status{{background:var(--green);color:white}}.partially-supported .unit-status,.all-unknown .unit-status,.legacy-unsupported .unit-status{{background:var(--amber);color:white}}.has-contradicted .unit-status{{background:var(--red);color:white}}.non-factual .unit-status,.structural .unit-status{{background:#e7ebf0;color:#536174}}.fully-supported .article-text{{border-left:4px solid var(--green);padding-left:12px}}.partially-supported .article-text,.all-unknown .article-text,.legacy-unsupported .article-text{{border-left:4px solid var(--amber);padding-left:12px}}.has-contradicted .article-text{{border-left:4px solid var(--red);padding-left:12px}}.non-factual .article-text,.structural .article-text{{border-left:4px solid #c4cad3;padding-left:12px;color:#667085}}.structural .article-text{{font-weight:700}}
.claim{{margin:9px 0;border:1px solid var(--line);border-radius:10px;overflow:hidden}}.claim.entailed{{border-color:#9bd5b6}}.claim.unknown,.claim.legacy{{border-color:#e5bd51}}.claim.contradicted{{border-color:#efaaa3}}
 .claim summary{{cursor:pointer;padding:10px 12px;background:#f9fafb;display:flex;gap:9px;align-items:flex-start;flex-wrap:wrap}}.claim.entailed summary{{background:var(--green-bg)}}.claim.unknown summary,.claim.legacy.unsupported summary{{background:var(--amber-bg)}}.claim.contradicted summary{{background:var(--red-bg)}}
 .badge{{border-radius:999px;padding:1px 8px;font-size:12px;font-weight:700;white-space:nowrap}}.claim.entailed .badge{{background:var(--green);color:white}}.claim.unknown .badge,.claim.legacy.unsupported .badge{{background:var(--amber);color:white}}.claim.contradicted .badge{{background:var(--red);color:white}}.claim-id{{font:12px ui-monospace,monospace;color:var(--muted);padding-top:2px}}
.claim-body{{padding:13px 15px;display:grid;grid-template-columns:1fr 1fr;gap:14px}}.claim-body>div:last-child{{grid-column:1/-1}}.claim-body p{{margin:3px 0}}blockquote{{margin:6px 0;padding:10px 13px;border-left:3px solid var(--blue);background:#f6f8fc}}.source{{color:var(--muted);font-size:12px;overflow-wrap:anywhere}}.empty{{color:var(--red);padding:8px 0}}.claim.unknown .empty{{color:var(--amber)}}
body[data-filter="supported"] .article-line:not(.fully-supported),body[data-filter="factual"] .article-line:not([data-kind="factual"]),body[data-filter="nonfactual"] .article-line[data-kind="factual"]{{display:none}}
 body[data-filter="supported"] .claim:not([data-semantic-status="entailed"]){{display:none}}
 body[data-filter="unknown"] .article-line:not(:has(.claim[data-semantic-status="unknown"])),body[data-filter="unknown"] .claim:not([data-semantic-status="unknown"]){{display:none}}
 body[data-filter="contradicted"] .article-line:not(:has(.claim[data-semantic-status="contradicted"])),body[data-filter="contradicted"] .claim:not([data-semantic-status="contradicted"]){{display:none}}
@media(max-width:760px){{.article-head{{display:block}}.score{{text-align:left;margin-top:12px}}.filters{{margin-left:0}}.claim-body{{grid-template-columns:1fr}}.claim-body>div:last-child{{grid-column:auto}}.summary-wrap{{overflow:auto}}}}
</style></head><body data-filter="all"><main class="shell">
 <h1>文章知识库支持率可视化</h1><p class="subtitle">先看哪些正文内容包含可验证主张，再看这些主张是否得到知识库支持。</p>
 {warning_notice}
  <div class="notice"><strong>{MODE}</strong><br>正式指标：明确支持 ÷（明确支持 + 尚未确认 + 明确冲突）。<br><strong>黄色“尚未确认”</strong>表示现有附件无法确认也无法否定；<strong>红色“明确冲突”</strong>只表示同一对象、范围、条件和时间/版本下存在明确不相容证据。灰色非主张内容不参与指标。本指标没有目标值，也不判断文章质量、发布资格或是否需要重写。</div>
 <div class="summary-wrap"><table class="summary"><thead><tr><th>文章</th><th>标题</th><th>明确支持</th><th>全部可验证主张</th><th>尚未确认</th><th>明确冲突</th><th>知识附件明确支撑率</th><th>可验证内容单元</th><th>可验证内容占比</th></tr></thead><tbody>{rows}</tbody></table></div>
 <div class="toolbar"><div>{tabs}</div><div class="filters"><button class="filter active" data-filter="all">全部</button><button class="filter" data-filter="factual">只看可验证主张</button><button class="filter" data-filter="supported">只看明确支持</button><button class="filter" data-filter="unknown">只看尚未确认</button><button class="filter" data-filter="contradicted">只看明确冲突</button><button class="filter" data-filter="nonfactual">只看非主张内容</button></div></div>
{panels}</main><script>
document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click',()=>{{document.querySelectorAll('.tab,.article-panel').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.getElementById(b.dataset.target).classList.add('active')}}));
 document.querySelectorAll('.filter').forEach(b=>b.addEventListener('click',()=>{{document.querySelectorAll('.filter').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.body.dataset.filter=b.dataset.filter}}));
</script></body></html>'''
    output = output_dir / "faithfulness_highlight.html"
    output.write_text(document, encoding="utf-8")
    return output


def discover_cases(
    input_dir: Path,
    article_override: Path | None = None,
    knowledge_override: list[Path] | None = None,
    persist_reconciliation: bool = False,
    require_protocol2_adversarial: bool = False,
) -> list[dict]:
    cases = []
    for judgment_path in sorted(input_dir.glob("*-judgments.json")):
        stem = judgment_path.name[: -len("-judgments.json")]
        prepared_path = input_dir / f"{stem}-prepared.json"
        if not prepared_path.exists():
            raise ValueError(f"Missing prepared JSON for {judgment_path.name}")
        prepared = load_json(prepared_path)
        judgments = load_json(judgment_path)
        review_path = input_dir / f"{stem}-adversarial-review.json"
        adversarial_review = load_json(review_path) if review_path.exists() else None
        if str(judgments.get("evaluation_protocol_version", "1.0")) == "2.0":
            if require_protocol2_adversarial and not isinstance(adversarial_review, dict):
                raise ValueError(
                    f"{judgment_path.name}: protocol 2.0 managed run requires "
                    f"{review_path.name}; an empty review must not be synthesized"
                )
            if require_protocol2_adversarial and isinstance(adversarial_review, dict):
                review_rows = adversarial_review.get("claim_reviews")
                if not isinstance(review_rows, list) or not review_rows:
                    raise ValueError(
                        f"{judgment_path.name}: protocol 2.0 adversarial review must contain "
                        "exactly one row for every claim"
                    )
            reconciliation = judgments.get("reconciliation")
            reconciliation_stale = reconciliation_needs_refresh(judgments, adversarial_review)
            if reconciliation_stale or not isinstance(reconciliation, dict) or (
                reconciliation.get("gate_version") != RECONCILIATION_GATE_VERSION
                or reconciliation.get("coverage_gate_version") != "1.0"
                or reconciliation.get("completed") is not True
            ):
                if not isinstance(adversarial_review, dict):
                    adversarial_review = {
                        "schema_version": "1.0",
                        "evaluation_protocol_version": "2.0",
                        "article_id": judgments.get("article_id", ""),
                        "review_mode": "independent_adversarial",
                        "claim_reviews": [],
                    }
                # Reconcile in memory for a read-only render. The producing
                # workflow must persist the reconciliation before import.  A
                # clean existing artifact is never silently rewritten by the
                # renderer.  A stale artifact containing a pure question is
                # repaired because it cannot pass the current structural
                # claim gate and would otherwise fail repeatedly at render.
                reconcile_payloads(judgments, adversarial_review, prepared)
                if persist_reconciliation:
                    atomic_write_json(judgment_path, judgments)
                    atomic_write_json(review_path, adversarial_review)
            if require_protocol2_adversarial:
                handoff_error = protocol2_handoff_error(judgments, adversarial_review)
                if handoff_error:
                    raise ValueError(f"{judgment_path.name}: {handoff_error}")
        claims = validate_case(
            prepared,
            judgments,
            prepared_path,
            judgment_path,
            adversarial_review,
            article_override,
            knowledge_override,
        )
        cases.append({
            "prepared": prepared,
            "judgments": judgments,
            "claims": claims,
            "adversarial_review": adversarial_review,
        })
    if not cases:
        raise ValueError(f"No *-judgments.json files found in {input_dir}")
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--result-dir",
        type=Path,
        help="v0.6受管模式：prepared、judgments和报告均位于同一文章版本目录。",
    )
    parser.add_argument("--article-id", help="受管模式下用于确认目录中只有当前文章的结果。")
    parser.add_argument("--article-version", help="受管模式下用于确认prepared的文章版本。")
    parser.add_argument("--article", type=Path, help="移动任务后复核时传入当前40_最终文章.md。")
    parser.add_argument(
        "--knowledge",
        type=Path,
        action="append",
        help="移动任务后复核时传入当前30_本篇知识库资料.md；可重复但v0.6只能一个。",
    )
    args = parser.parse_args()

    if args.result_dir and args.input_dir:
        raise SystemExit("Do not combine --result-dir with --input-dir")
    managed = args.result_dir is not None
    if managed:
        input_dir = args.result_dir.resolve()
        output_dir = args.output_dir.resolve() if args.output_dir else input_dir
        if not input_dir.is_dir():
            raise SystemExit(f"Managed result directory not found: {input_dir}")
        if output_dir == input_dir and (input_dir / "faithfulness_summary.md").exists():
            raise SystemExit(f"Refusing to overwrite existing managed summary: {input_dir / 'faithfulness_summary.md'}")
    else:
        if not args.input_dir or not args.output_dir:
            raise SystemExit("Provide --input-dir and --output-dir, or use --result-dir")
        input_dir, output_dir = args.input_dir, args.output_dir

    if (args.article or args.knowledge) and not args.article:
        raise SystemExit("--knowledge requires --article when overriding moved v0.6 sources")
    if args.article and not args.knowledge:
        raise SystemExit("--article requires --knowledge when overriding moved v0.6 sources")
    if args.knowledge and len(args.knowledge) != 1 and managed:
        raise SystemExit("Managed v0.6 review accepts exactly one --knowledge override")

    cases = discover_cases(
        input_dir,
        args.article,
        args.knowledge,
        persist_reconciliation=managed and output_dir == input_dir,
        require_protocol2_adversarial=managed,
    )
    if managed:
        if len(cases) != 1:
            raise SystemExit("A managed result directory must contain exactly one article audit")
        if len(list(input_dir.glob("*-prepared.json"))) != 1 or len(list(input_dir.glob("*-judgments.json"))) != 1:
            raise SystemExit("A managed result directory must contain exactly one prepared and one judgments artifact")
        prepared = cases[0]["prepared"]
        if prepared.get("integration_mode") != "manage-article-knowledge-v0.6":
            raise SystemExit("Managed result directory contains a non-v0.6 prepared artifact")
        if not str(prepared.get("project_id", "")).strip():
            raise SystemExit("Managed prepared artifact is missing project_id")
        if input_dir.parent.parent.name != str(prepared.get("project_id", "")):
            raise SystemExit("Managed result directory project_id does not match prepared artifact")
        try:
            _, contract = load_contract()
            contract_version = validate_version(
                str(prepared.get("handoff_contract_version", "")), contract
            )
        except ManagedHandoffError as exc:
            raise SystemExit(str(exc)) from exc
        if args.article_id and prepared.get("article_id") != args.article_id:
            raise SystemExit("Managed result directory article_id does not match --article-id")
        if args.article_version and prepared.get("article_version") != args.article_version:
            raise SystemExit("Managed result directory article_version does not match --article-version")
        expected_dir_version = "v" + str(prepared.get("article_version", "")).strip().lstrip("vV")
        if input_dir.name != expected_dir_version or input_dir.parent.name != str(prepared.get("article_id", "")):
            raise SystemExit("Managed result directory must end with [article_id]/v[version]")
        try:
            validate_event(
                "faithfulness_completed",
                {
                    "handoff_event": "faithfulness_completed",
                    "handoff_contract_version": contract_version,
                    "result_dir": str(input_dir.resolve()),
                    "article_id": prepared.get("article_id", ""),
                    "article_version": prepared.get("article_version", ""),
                },
                contract,
            )
        except ManagedHandoffError as exc:
            raise SystemExit(str(exc)) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = [str(write_detail(case, output_dir).resolve()) for case in cases]
    outputs.append(str(write_summary(cases, output_dir).resolve()))
    outputs.append(str(write_html(cases, output_dir).resolve()))
    payload = {"articles": len(cases), "outputs": outputs}
    if managed:
        prepared = cases[0]["prepared"]
        payload.update(
            {
                "handoff_event": "faithfulness_completed",
                "handoff_contract_version": prepared.get("handoff_contract_version", ""),
                "result_dir": str(input_dir.resolve()),
                "article_id": prepared.get("article_id", ""),
                "article_version": prepared.get("article_version", ""),
            }
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
