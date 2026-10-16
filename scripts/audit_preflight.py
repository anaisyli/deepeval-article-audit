"""Freeze extraction inventories, then check records before semantic reconciliation.

This helper never decides entailment. It repairs only verifiable citation-list
omissions and returns bounded review work for Codex; uncertainty still finishes.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from audit_common import extract_knowledge_chunks, STRUCTURAL_UNIT_TYPES
from prepare_article_audit import sha256_file
from reconcile_article_audit import (
    CHECK_KEYS, claim_identity_key, traceable_to_quote, is_pure_question,
    permitted_nonclaim_unit, validate_entailed_review, review_reason_duplicates,
    atomic_write_json,
)


def freeze_inventories(prepared: dict, first: dict, blind: dict,
                       replacements: list[dict] | None = None) -> dict:
    """Produce an unjudged union; no old verdict/evidence is carried forward."""
    article_id = prepared.get("article_id")
    if not article_id or any(p.get("article_id") != article_id for p in (first, blind)):
        raise ValueError("Inventory article identity mismatch")
    units = {str(u["unit_id"]): u for u in prepared["article_units"]}
    classifications = []
    for payload in (first, blind):
        rows = payload.get("coverage_review", {}).get("unit_classifications", [])
        mapping = {str(r.get("unit_id")): r for r in rows}
        if set(mapping) != set(units) or len(rows) != len(units):
            raise ValueError("Each extraction pass must classify every article unit exactly once")
        classifications.append(mapping)
    sources: dict[str, dict] = {}
    excluded = []
    for pass_name, payload in (("first", first), ("blind", blind)):
        inventory = payload.get("claim_inventory")
        if not isinstance(inventory, list):
            raise ValueError("Extraction requires claim_inventory arrays, not judgments")
        for item in inventory:
            item = copy.deepcopy(item)
            ref = f"{pass_name}:{item.get('inventory_id', '')}"
            if not item.get("inventory_id") or ref in sources or ref in excluded:
                raise ValueError("Extraction inventory IDs must be unique and nonempty")
            unit_id = str(item.get("unit_id", ""))
            unit = units.get(unit_id)
            if not unit or item.get("article_line") != unit.get("line"):
                raise ValueError(f"Invalid article unit/line: {ref}")
            quote = str(item.get("article_quote", ""))
            if not quote or quote not in str(unit.get("text", "")):
                raise ValueError(f"Inventory quotation is not traceable: {ref}")
            if not traceable_to_quote(item) and not str(item.get("derivation_note", "")).strip():
                raise ValueError(f"Inventory proposition is not traceable: {ref}")
            if (unit.get("unit_type") in STRUCTURAL_UNIT_TYPES or is_pure_question(item.get("claim"))
                    or permitted_nonclaim_unit(unit, classifications[0][unit_id], classifications[1][unit_id])):
                excluded.append(ref)
                continue
            sources[ref] = item
    replaced: dict[str, list[str]] = {}
    for replacement in replacements or []:
        old = str(replacement.get("source_ref", ""))
        targets = replacement.get("replacement_refs")
        if (old not in sources or old in replaced or not isinstance(targets, list)
                or not targets or len(set(targets)) != len(targets)
                or replacement.get("all_predicates_preserved") is not True
                or len(str(replacement.get("coverage_reason", "")).strip()) < 20):
            raise ValueError("Semantic replacement requires explicit complete predicate coverage")
        if any(t not in sources or t == old or sources[t]["unit_id"] != sources[old]["unit_id"] for t in targets):
            raise ValueError("Replacement must refer to retained propositions in the same source unit")
        replaced[old] = targets
    if any(t in replaced for targets in replaced.values() for t in targets):
        raise ValueError("Replacement chains cannot silently lose source propositions")
    canonical: dict[tuple, dict] = {}
    order = {unit_id: n for n, unit_id in enumerate(units)}
    for ref, item in sources.items():
        if ref in replaced:
            continue
        key = claim_identity_key(item)
        if key not in canonical:
            canonical[key] = {k: item[k] for k in (
                "unit_id", "article_line", "article_quote", "claim", "derivation_note", "atomicity_note"
            ) if k in item}
            canonical[key].update(source_refs=[], judgment_state="awaiting_independent_judgments")
        canonical[key]["source_refs"].append(ref)
    claims = sorted(canonical.values(), key=lambda c: order[c["unit_id"]])
    for n, item in enumerate(claims, 1):
        item["claim_id"] = f"C{n:03d}"
        item["inventory_origin"] = "both" if len({r.split(':')[0] for r in item["source_refs"]}) == 2 else item["source_refs"][0].split(':')[0] + "_only"
    return dict(schema_version="1.0", article_id=article_id,
                article_version=prepared.get("article_version", ""),
                article_sha256=prepared.get("article_sha256", ""),
                knowledge_sha256=prepared.get("knowledge_sha256", []), claims=claims,
                excluded_source_refs=excluded, replacements=replacements or [],
                source_mapping={ref: (replaced.get(ref) or [ref]) for ref in sources})


def preflight_records(prepared: dict, judgments: dict, blind: dict, *, attempt: int = 0,
                      frozen: dict | None = None) -> dict:
    if attempt not in (0, 1):
        raise ValueError("Preflight permits one bounded review retry (attempt 0 or 1)")
    if any(p.get("article_id") != prepared.get("article_id") for p in (judgments, blind)):
        raise ValueError("Preflight article identity mismatch")
    # Existing completed audits remain immutable; use a new result version.
    if judgments.get("reconciliation", {}).get("completed"):
        raise ValueError("Do not preflight an already reconciled historical result")
    if frozen is not None:
        if (frozen.get("article_id") != prepared.get("article_id")
                or frozen.get("article_version") != prepared.get("article_version", "")
                or frozen.get("article_sha256") != prepared.get("article_sha256", "")
                or frozen.get("knowledge_sha256") != prepared.get("knowledge_sha256", [])):
            raise ValueError("Frozen inventory source identity mismatch")
        expected = {claim_identity_key(c) for c in frozen["claims"]}
        for rows in (judgments.get("claims", []), blind.get("claim_reviews", [])):
            if len(rows) != len(expected) or {claim_identity_key(c) for c in rows} != expected:
                raise ValueError("Every frozen proposition requires two independent judgments")
    sources = {}
    hashes = prepared.get("knowledge_sha256", [])
    for n, name in enumerate(prepared.get("knowledge_files", [])):
        path = Path(name).resolve()
        if n >= len(hashes) or sha256_file(path) != hashes[n]:
            raise ValueError("Knowledge changed after preparation")
        sources[str(path).casefold()] = (path.read_text(encoding="utf-8-sig").splitlines(),
                                        extract_knowledge_chunks(path))
    def valid_citation(item: dict) -> bool:
        try:
            lines, chunks = sources[str(Path(item["source_file"]).resolve()).casefold()]
            start, end = item["line_start"], item["line_end"]
            quote = item["quote"]
            return (type(start) is int and type(end) is int and 1 <= start <= end <= len(lines)
                    and bool(quote) and quote in "\n".join(lines[start-1:end])
                    and any(c.get("_evidence_eligible", True) and c["line_start"] <= start
                            and end <= c["line_end"] for c in chunks))
        except (KeyError, TypeError, ValueError):
            return False
    reviews = {claim_identity_key(r): r for r in blind.get("claim_reviews", [])}
    if len(reviews) != len(blind.get("claim_reviews", [])):
        raise ValueError("Duplicate blind proposition identities")
    repaired, tasks = [], []
    duplicates = review_reason_duplicates(list(reviews.values()))
    for claim in judgments.get("claims", []):
        row = reviews.get(claim_identity_key(claim))
        if row is None:
            tasks.append(dict(claim_id=claim["claim_id"], cause="missing_independent_review"))
            continue
        if claim.get("semantic_status") != "entailed" or row.get("independent_semantic_status", row.get("semantic_status")) != "entailed":
            continue  # Never promote a first-pass unknown or resolve semantic disagreement by code.
        checks = row.get("entailment_checks", {})
        needed = [str(c.get("evidence_fragment", "")) for c in checks.values()
                  if isinstance(c, dict) and c.get("status") == "covered"]
        evidence = claim.setdefault("evidence", [])
        for fragment in needed:
            if not fragment or any(fragment in str(e.get("quote", "")) and valid_citation(e) for e in evidence):
                continue
            candidates = [e for e in row.get("evidence", []) if isinstance(e, dict)
                          and fragment in str(e.get("quote", "")) and valid_citation(e)]
            if candidates:
                item = copy.deepcopy(candidates[0])
                if item not in evidence:
                    evidence.append(item)
                    repaired.append(dict(claim_id=claim["claim_id"], cause="citation_list_omission", evidence=item))
        invalid = ["引用不属于当前允许证据正文" for e in evidence if not valid_citation(e)]
        problems = invalid + validate_entailed_review(claim, row, duplicates)
        if problems:
            tasks.append(dict(claim_id=claim["claim_id"], cause="component_record_review",
                              problems=problems, allowed_action="核对原文后补记录；缺真实支持则保留unknown"))
    receipt = dict(schema_version="1.0", article_id=prepared["article_id"], attempt=attempt,
                   terminal=False, next_owner="deepeval-article-audit", automatic_repairs=repaired,
                   review_tasks=tasks, next_action="review_records_once" if tasks and attempt == 0 else "reconcile_and_render",
                   resume_condition="完成一次独立记录复核；未解决项按unknown继续报告",
                   ready_for_reconciliation=not tasks or attempt == 1)
    judgments["audit_preflight"] = receipt
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    freeze = sub.add_parser("freeze")
    for flag in ("prepared", "first-inventory", "blind-inventory", "output"):
        freeze.add_argument("--" + flag, type=Path, required=True)
    freeze.add_argument("--replacements", type=Path)
    check = sub.add_parser("check")
    for flag in ("prepared", "judgments", "adversarial-review", "receipt"):
        check.add_argument("--" + flag, type=Path, required=True)
    check.add_argument("--frozen-inventory", type=Path, required=True)
    check.add_argument("--attempt", type=int, default=0)
    args = parser.parse_args()
    def read(path):
        return json.loads(path.read_text(encoding="utf-8-sig"))
    if args.mode == "freeze":
        if args.output.exists():
            raise ValueError("Frozen inventory exists; never overwrite historical extraction")
        payload = freeze_inventories(read(args.prepared), read(args.first_inventory), read(args.blind_inventory),
                                     read(args.replacements) if args.replacements else None)
        atomic_write_json(args.output, payload)
    else:
        judgments = read(args.judgments)
        payload = preflight_records(read(args.prepared), judgments, read(args.adversarial_review),
                                    attempt=args.attempt, frozen=read(args.frozen_inventory))
        atomic_write_json(args.judgments, judgments)
        atomic_write_json(args.receipt, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
