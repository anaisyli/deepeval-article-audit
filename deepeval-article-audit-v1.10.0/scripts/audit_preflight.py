"""Freeze extraction inventories, then check records before semantic reconciliation.

This helper never decides entailment. It fills mechanical fields (article_line
from unit_id, citation line numbers from a uniquely located quote), repairs only
verifiable citation-list omissions and returns bounded review work for Codex;
uncertainty still finishes.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path

from audit_common import (
    extract_knowledge_chunks, normalize_inline_markdown, read_text, STRUCTURAL_UNIT_TYPES,
)
from audit_compact import (
    VIEW_SUFFIXES, _attach_coreference, assemble_judgments, assemble_review, build_view, claims_list, coverage_problems,
    normalize_inventory, view_parts, write_views,
)
from audit_snapshot import parsed_input_conflicts
from reconcile_article_audit import (
    CHECK_KEYS, claim_identity_key, traceable_to_quote, is_pure_question,
    permitted_nonclaim_unit, validate_entailed_review, review_reason_duplicates,
    atomic_write_json, REVIEW_POLICY, validate_review_risk_profile, merge_review_risk,
    DECISIVE_REVIEW_POLICY, SUPPORTED_REVIEW_POLICIES, requires_second_review, review_policy_of,
    PRE_RECONCILE_DIR, is_review_outcome, record_only_downgrade,
)


def declared_policy(first: dict, blind: dict) -> str | None:
    """Return the review_policy both extraction passes declare (None when neither does).

    A pass that omits the field uses the current default, so it only conflicts
    with a pass that declares a historical policy.
    """
    first_policy, blind_policy = first.get("review_policy"), blind.get("review_policy")
    policy = first_policy or blind_policy
    if ((first_policy and blind_policy and first_policy != blind_policy)
            or (None in (first_policy, blind_policy) and policy not in (None, DECISIVE_REVIEW_POLICY))):
        raise ValueError("Both extraction passes must use the same review_policy")
    if policy is not None and policy not in SUPPORTED_REVIEW_POLICIES:
        raise ValueError(f"Unsupported review_policy: {policy}")
    return policy


def recheck_list(judgments: dict) -> dict:
    """List the propositions the second reviewer must judge, without verdicts.

    Only identity fields leave the first pass, so the second judgment stays
    blind to the first pass's status, evidence and reasoning.
    """
    policy = review_policy_of(judgments)
    if policy != DECISIVE_REVIEW_POLICY:
        raise ValueError("recheck-list is defined for decisive_recheck_v1 judgments")
    if judgments.get("reconciliation", {}).get("completed"):
        raise ValueError("Do not build a recheck list from an already reconciled result")
    items = [
        {k: claim[k] for k in ("claim_id", "unit_id", "article_line", "article_quote", "claim") if k in claim}
        for claim in judgments.get("claims", [])
        if requires_second_review(claim, policy)
    ]
    return dict(schema_version="1.0", article_id=judgments.get("article_id"),
                review_policy=policy, claims=items)


# Genuinely independent passes on P-005 v1 split at most 1 of 62 units differently;
# the stitched recovery run split 47 of 67.  Above this the union double-counts.
MAX_DIVERGENT_UNITS = 3


def divergent_units(claims: list[dict]) -> list[str]:
    """Units where each pass contributed propositions the other pass does not share."""
    origins: dict[str, set[str]] = {}
    for claim in claims:
        origin = claim_origin(claim)
        if origin != "both":
            origins.setdefault(claim["unit_id"], set()).add(origin)
    return [unit for unit, seen in origins.items() if seen == {"first_only", "blind_only"}]


def claim_origin(claim: dict) -> str:
    passes = {ref.split(":")[0] for ref in claim["source_refs"]}
    return "both" if len(passes) == 2 else next(iter(passes)) + "_only"


# A claim is a self-contained proposition.  Cutting sentences at commas yields fragments such as
# "size" or "For wholesale buyers" that cannot be true or false; P-005 v4 (2026-10-26) produced
# 165 and 204 claims that way by script.  Passes from the published skill had at most 4 in 100.
MAX_FRAGMENT_SHARE = 0.1
MIN_CLAIMS_FOR_FRAGMENT_SHARE = 20


def is_fragment(text: str) -> bool:
    han = sum(1 for ch in text if 0x4E00 <= ord(ch) <= 0x9FFF)
    return han < 6 and len(text.split()) <= 3


def fragment_pass_problem(label: str, payload: dict) -> str:
    """Name a pass whose claims are cut-up phrases rather than propositions."""
    # Compact rows may omit `claim` (it equals the quote) or both (the whole unit): neither is a fragment.
    claims = [str(item.get("claim") or item.get("article_quote") or "") for item in payload.get("claim_inventory") or []]
    if len(claims) < MIN_CLAIMS_FOR_FRAGMENT_SHARE:
        return ""
    fragments = [text for text in claims if text.strip() and is_fragment(text)]
    if len(fragments) <= MAX_FRAGMENT_SHARE * len(claims):
        return ""
    return (f"{label} pass has {len(fragments)} of {len(claims)} claims that are phrase fragments, not "
            f"propositions (e.g. {'; '.join(fragments[:4])}). Extraction is a reading task: write each claim as a "
            "self-contained statement with its subject and predicate (method §2); never cut sentences by script.")


# Two independent passes word the same proposition differently ("The open drawer should make the
# assortment obvious." / "the open drawer should make the assortment obvious"); matched on exact
# wording, P-005 v4 (2026-10-26) showed 41/54 and 42/48 divergent units and a union of 163/151 claims
# where meaning-word pairing leaves 3 and 2 units and 94/83 claims.
PAIR_MIN_OVERLAP = 0.75
PAIR_STOPWORDS = frozenset(
    "a an the to of and or is are was were be been being can could should would will may might must its it "
    "their this that these those for with as in on at by from into when while what which who whose".split())
PAIR_NEGATIONS = frozenset("not no never without cannot none neither nor".split())
HAN_NEGATIONS = ("不", "无", "未", "没", "非", "勿", "否")


def content_words(text: str) -> set[str]:
    """Meaning-bearing words: English stems without function words, Han character bigrams."""
    words: set[str] = set()
    for token in re.findall(r"\w+", str(text).casefold().replace("n't", " not")):
        han = "".join(ch for ch in token if 0x4E00 <= ord(ch) <= 0x9FFF)
        if han:
            words.update(han[i:i + 2] for i in range(max(1, len(han) - 1)))
            continue
        # A single letter is a possessive or initial ("JERL Packaging's" -> "s"), never meaning;
        # counting it kept "process includes quoting" from pairing with "quotes in the second step".
        if token in PAIR_STOPWORDS or token in PAIR_NEGATIONS or token.isdigit() or len(token) == 1:
            continue
        words.add(re.sub(r"(ing|ed|es|s|e)$", "", token) if len(token) > 4 else token)
    return words


def polarity_and_numbers(text: str) -> tuple:
    lowered = str(text).casefold().replace("n't", " not")
    return (frozenset(set(re.findall(r"\w+", lowered)) & PAIR_NEGATIONS),
            frozenset(ch for ch in HAN_NEGATIONS if ch in lowered),
            frozenset(re.findall(r"\d+(?:\.\d+)?", lowered)))


def pair_score(a: str, b: str) -> float:
    """Share of the shorter claim's meaning words found in the other; 0 when negation or numbers differ."""
    if polarity_and_numbers(a) != polarity_and_numbers(b):
        return 0.0
    words_a, words_b = content_words(a), content_words(b)
    if min(len(words_a), len(words_b)) < 2:
        return 0.0
    return len(words_a & words_b) / min(len(words_a), len(words_b))


def distinctive_overlap(a: str, b: str, frame_a: frozenset[str], frame_b: frozenset[str]) -> int:
    """Meaning words two claims share outside the words each pass repeats across the unit.

    -1 when both claims have such words and share none: they state different facts under the
    same frame and never pair, however high their overall overlap.  Otherwise it ranks candidates
    that already clear PAIR_MIN_OVERLAP: when a claim could pair with several, the one sharing its
    distinctive words wins.  In P-005 v4 U045 the blind "JERL
    quotes in the second step of its four-step process" otherwise took the first pass's "JERL
    describes its process as four steps" (frame words JERL/process/four/step, 0.83) over "JERL's
    process includes quoting" (0.80), and the four-steps fact left the denominator.
    """
    own_a, own_b = content_words(a) - frame_a, content_words(b) - frame_b
    if own_a and own_b and not own_a & own_b:
        return -1
    return len(own_a & own_b)


def unit_frame(claims: list[str]) -> frozenset[str]:
    """Meaning words one pass repeats in at least 3 and at least half of its claims of a unit.

    Three, not two of three: a pass writing "closing the drawer" and "the closed drawer" in two of
    three claims has not made "close" its frame (P-005 v4 attempt-5 U048).
    """
    counts: dict[str, int] = {}
    for claim in claims:
        for word in content_words(claim):
            counts[word] = counts.get(word, 0) + 1
    return frozenset(word for word, count in counts.items() if count >= 3 and count * 2 >= len(claims))


def pair_passes(sources: dict[str, dict], skip: set[str]) -> dict[str, str]:
    """Map each reworded claim of one pass onto its counterpart in the other pass of the same unit.

    Returns dropped ref -> kept ref.  Exact matches merge by identity anyway and are left alone;
    pairing is one-to-one, so a compound kept whole by one pass pairs with at most one split part.
    The kept wording is the one with more meaning words (the more complete proposition).
    """
    exact: dict[tuple, set[str]] = {}
    for ref, item in sources.items():
        if ref not in skip:
            exact.setdefault(claim_identity_key(item), set()).add(ref.split(":")[0])
    by_unit: dict[str, dict[str, list[str]]] = {}
    for ref, item in sources.items():
        if ref in skip or len(exact[claim_identity_key(item)]) == 2:
            continue
        by_unit.setdefault(str(item["unit_id"]), {}).setdefault(ref.split(":")[0], []).append(ref)
    pass_claims: dict[tuple[str, str], list[str]] = {}
    for ref, item in sources.items():
        if ref not in skip:
            pass_claims.setdefault((ref.split(":")[0], str(item["unit_id"])), []).append(str(item["claim"]))
    paired: dict[str, str] = {}
    for unit_id, refs in by_unit.items():
        frame_first = unit_frame(pass_claims.get(("first", unit_id), []))
        frame_blind = unit_frame(pass_claims.get(("blind", unit_id), []))
        candidates = sorted(
            ((distinctive_overlap(sources[f]["claim"], sources[b]["claim"], frame_first, frame_blind),
              pair_score(sources[f]["claim"], sources[b]["claim"]), n, f, b)
             for n, (f, b) in enumerate((f, b) for f in refs.get("first", []) for b in refs.get("blind", []))),
            key=lambda c: (-c[0], -c[1], c[2]))
        used: set[str] = set()
        for distinct, score, _, f, b in candidates:
            if distinct < 0 or score < PAIR_MIN_OVERLAP or f in used or b in used:
                continue
            used.update((f, b))
            keep_blind = len(content_words(sources[b]["claim"])) > len(content_words(sources[f]["claim"]))
            kept, dropped = (b, f) if keep_blind else (f, b)
            paired[dropped] = kept
    return paired


def second_pass_from_coverage(prepared: dict, first: dict, coverage: dict) -> dict:
    """Build the second-pass payload from the first pass and a coverage review (v1.9 default flow).

    The coverage reviewer reads the extract view and the first pass, and writes only (a) a
    non_factual row of its own for each unit the first pass excluded and it agrees to exclude, and
    (b) claims the first pass missed, in any unit.  It does not re-extract the article, so nothing
    has to be paired or merged: the second pass is the first pass's claims plus these additions,
    and a unit is excluded only when both agree.  Two full blind passes cost about 40% of an audit
    and caused most freeze-time instability (pairing, divergence, double counting; P-005 v4).
    """
    first = normalize_inventory(prepared, first)
    units = {str(u["unit_id"]): u for u in prepared.get("article_units", [])}
    rows = (coverage.get("coverage_review") or {}).get("unit_classifications") or []
    additions = coverage.get("claim_inventory") or []
    claimed_first = {str(i["unit_id"]) for i in first["claim_inventory"]}
    added_units = {str(i.get("unit_id")) for i in additions}
    excluded_first = [str(r["unit_id"]) for r in first["coverage_review"]["unit_classifications"]
                      if r.get("classification") == "non_factual"
                      and units.get(str(r["unit_id"]), {}).get("unit_type") not in STRUCTURAL_UNIT_TYPES]
    by_unit = {str(r.get("unit_id")): r for r in rows}
    problems = []
    for unit_id in by_unit:
        if unit_id in claimed_first:
            problems.append(f"{unit_id}：第一轮已有主张，覆盖复查不取消主张，只补漏或复核非主张单元")
        elif unit_id not in units:
            problems.append(f"{unit_id}：不是本篇的单元")
    missing = [u for u in excluded_first if u not in by_unit and u not in added_units]
    if missing:
        problems.append("第一轮判为非主张的这些单元缺少复查结论（写 non_factual 行表示同意排除，或补写主张）："
                        + "、".join(missing))
    if problems:
        raise ValueError("Coverage review is incomplete. Nothing is judged yet: fix coverage.json and run the same "
                         "freeze command again:\n" + "\n".join(problems))
    keep = ("unit_id", "article_quote", "claim", "derivation_note", "atomicity_note",
            "review_risk_level", "review_risk_categories")
    inventory = [{k: item[k] for k in keep if k in item} for item in first["claim_inventory"]]
    inventory += [dict(item) for item in additions]
    second = dict(article_id=first.get("article_id"), derived_from="coverage_review",
                  coverage_review=dict(unit_classifications=[
                      dict(r) for r in rows if r.get("classification") == "non_factual"]),
                  claim_inventory=inventory)
    for field in ("review_policy", "evaluation_protocol_version"):
        if first.get(field):
            second[field] = first[field]
    # Units the first pass claimed and the reviewer left alone stay factual; normalize adds their rows.
    return normalize_inventory(prepared, second)


def freeze_inventories(prepared: dict, first: dict, blind: dict,
                       replacements: list[dict] | None = None, *, coverage_review: bool = False) -> dict:
    """Produce an unjudged union; no old verdict/evidence is carried forward.

    With `coverage_review` the second pass was built by second_pass_from_coverage: it repeats the
    first pass's claims by design, so the independence, pairing and divergence checks of two blind
    passes do not apply.
    """
    # Compact passes may omit structural rows and inventory IDs; fill them mechanically.
    first, blind = normalize_inventory(prepared, first), normalize_inventory(prepared, blind)
    problems = [f"{label}: {p}" for label, payload in (("first", first), ("blind", blind))
                for p in coverage_problems(prepared, payload)]
    if problems:
        raise ValueError("Coverage rows the renderer would reject. Nothing is judged yet, so correct just these rows "
                         "in the named pass file (the reading view lists the allowed categories), then run the same "
                         "freeze command again; do not stop the audit:\n" + "\n".join(problems))
    article_id = prepared.get("article_id")
    if not article_id or any(p.get("article_id") != article_id for p in (first, blind)):
        raise ValueError("Inventory article identity mismatch")
    # Two genuinely independent readings of a non-trivial article never list identical propositions.
    listed = [[(str(i.get("unit_id")), str(i.get("article_quote")), str(i.get("claim")))
               for i in p.get("claim_inventory") or []] for p in (first, blind)]
    if not coverage_review and len(listed[0]) >= 5 and listed[0] == listed[1]:
        raise ValueError("The two extraction passes list identical propositions, so they are not independent; "
                         "redo the blind pass in a fresh subagent or session that has not seen the first pass")
    policy = declared_policy(first, blind)
    risk_weighted = policy == REVIEW_POLICY
    units = {str(u["unit_id"]): u for u in prepared["article_units"]}
    article = Path(str(prepared.get("article_file", "")))
    raw_lines = read_text(article).splitlines() if prepared.get("article_file") and article.is_file() else []
    classifications = []
    for label, payload in (("first", first), ("blind", blind)):
        rows = payload.get("coverage_review", {}).get("unit_classifications", [])
        mapping = {str(r.get("unit_id")): r for r in rows}
        if set(mapping) != set(units) or len(rows) != len(units):
            skipped = [u for u in units if u not in mapping]
            raise ValueError("Each extraction pass must classify every article unit exactly once"
                             + (f"; {label} pass has neither claims nor a non_factual row for: {', '.join(skipped)}"
                                if skipped else ""))
        classifications.append(mapping)
    # A pass that lists claims for a unit and also classifies it non_factual contradicts itself.
    # Two non_factual rows exclude a unit, so such claims used to vanish silently (P-005 v4
    # attempt-6 blind U017: two claims plus a "transition" row).
    contradictions = [
        f"{label}: {unit_id}" for (label, payload), mapping in zip((("first", first), ("blind", blind)), classifications)
        for unit_id in sorted({str(i.get("unit_id")) for i in payload.get("claim_inventory") or []})
        if mapping.get(unit_id, {}).get("classification") == "non_factual"
        and units.get(unit_id, {}).get("unit_type") not in STRUCTURAL_UNIT_TYPES]
    if contradictions:
        raise ValueError("These units have claims and a non_factual row in the same pass. Nothing is judged yet: "
                         "keep the claims and delete the non_factual row, or drop the claims if the unit really "
                         "states no fact, then run the same freeze command again: " + "、".join(contradictions))
    fragments = [problem for label, payload in (("first", first), ("blind", blind))
                 for problem in [fragment_pass_problem(label, payload)] if problem]
    if fragments:
        raise ValueError(" ".join(fragments))
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
            if unit and item.get("article_line") in (None, ""):
                item["article_line"] = unit.get("line")  # mechanical: derived from unit_id
            if not unit or item.get("article_line") != unit.get("line"):
                raise ValueError(f"Invalid article unit/line: {ref}")
            quote = str(item.get("article_quote", ""))
            # Same rule as the renderer: verbatim in the unit (inline markup ignored)
            # and, when the article is readable, an exact substring of its raw line.
            if (not quote or normalize_inline_markdown(quote) not in normalize_inline_markdown(str(unit.get("text", "")))
                    or (raw_lines and quote not in raw_lines[int(unit["line"]) - 1])):
                raise ValueError(f"Inventory quotation is not traceable: {ref}")
            if not traceable_to_quote(item) and not str(item.get("derivation_note", "")).strip():
                raise ValueError(f"Inventory proposition is not traceable: {ref}")
            if (unit.get("unit_type") in STRUCTURAL_UNIT_TYPES or is_pure_question(item.get("claim"))
                    or permitted_nonclaim_unit(unit, classifications[0][unit_id], classifications[1][unit_id])):
                excluded.append(ref)
                continue
            if risk_weighted:
                validate_review_risk_profile(item)
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
    paired = {} if coverage_review else pair_passes(
        sources, set(replaced) | {t for targets in replaced.values() for t in targets})
    canonical: dict[tuple, dict] = {}
    order = {unit_id: n for n, unit_id in enumerate(units)}
    # Paired refs come last so the kept wording has already opened its canonical claim.
    for ref, item in sorted(sources.items(), key=lambda pair: pair[0] in paired):
        if ref in replaced:
            continue
        key = claim_identity_key(sources[paired[ref]] if ref in paired else item)
        if key not in canonical:
            canonical[key] = {k: item[k] for k in (
                "unit_id", "article_line", "article_quote", "claim", "derivation_note", "atomicity_note",
                "review_risk_level", "review_risk_categories",
            ) if k in item}
            canonical[key].update(source_refs=[], judgment_state="awaiting_independent_judgments")
        else:
            # A note written by either pass serves the merged claim.
            for note in ("derivation_note", "atomicity_note"):
                if len(str(canonical[key].get(note) or "").strip()) < len(str(item.get(note) or "").strip()):
                    canonical[key][note] = item[note]
            if risk_weighted:
                merge_review_risk(canonical[key], item)
        canonical[key]["source_refs"].append(ref)
    claims = sorted(canonical.values(), key=lambda c: order[c["unit_id"]])
    divergent = [] if coverage_review else divergent_units(claims)
    claimed_units = len({c["unit_id"] for c in claims})
    if len(divergent) > max(MAX_DIVERGENT_UNITS, claimed_units // 5):
        raise ValueError(
            f"The two passes word or split {len(divergent)} of {claimed_units} claimed units differently "
            f"({', '.join(divergent[:8])}{'…' if len(divergent) > 8 else ''}); a union would count paraphrases and "
            "compound/split forms of one fact twice. Independent passes on one article rarely diverge like this: "
            "redo one pass in a fresh subagent, or map each paraphrase or compound onto the kept propositions "
            "with --replacements, then freeze again")
    for n, item in enumerate(claims, 1):
        item["claim_id"] = f"C{n:03d}"
        item["inventory_origin"] = claim_origin(item)
    result = dict(schema_version="1.0", article_id=article_id,
                article_version=prepared.get("article_version", ""),
                article_sha256=prepared.get("article_sha256", ""),
                knowledge_sha256=prepared.get("knowledge_sha256", []), claims=claims,
                excluded_source_refs=excluded,
                replacements=(replacements or []) + [
                    dict(source_ref=dropped, replacement_refs=[kept], all_predicates_preserved=True,
                         pairing="content_word_overlap",
                         coverage_reason="机器配对：同一单元内两轮措辞不同的同一命题（实词重合，否定词与数字一致），合并为一条")
                    for dropped, kept in paired.items()],
                source_mapping={ref: (replaced.get(ref) or ([paired[ref]] if ref in paired else [ref]))
                                for ref in sources})
    if policy:
        result["review_policy"] = policy
    return result


def refuse_final_record(output: Path) -> None:
    """An assembled record may be rebuilt from corrected compact files until reconciliation."""
    try:
        reconciled = bool(json.loads(output.read_text(encoding="utf-8-sig")).get("reconciliation", {}).get("completed"))
    except (OSError, ValueError):
        reconciled = False
    if reconciled or (output.parent / "article_completed.json").is_file():
        raise ValueError(f"{output.name} is already reconciled; it can no longer be rebuilt. Run audit_preflight.py "
                         f"reset --result-dir \"{output.parent}\" before import, or have the knowledge base start a "
                         "new article version after import")


def supersede_assembled(output: Path, mode: str) -> str:
    """Move an unreconciled assembled record, and the files built from it, to _work/superseded-N/.

    Correcting a compact shard and assembling again is the repair path for anything `check`
    reports; before this, assembled records refused to be rebuilt and only `reset` was left.
    """
    result_dir = output.resolve().parent
    work = result_dir / "_work"
    stale = [output]
    if mode == "assemble-judgments":  # the recheck list, review and receipts were derived from these verdicts
        review = result_dir / output.name.replace("-judgments.json", "-adversarial-review.json")
        stale += [review, work / "recheck.json"]
    stale += sorted(work.glob("receipt*.json")) if work.is_dir() else []
    stale = [path for path in stale if path.is_file()]
    n = 1
    while (work / f"superseded-{n}").exists():
        n += 1
    target = work / f"superseded-{n}"
    target.mkdir(parents=True)
    for path in stale:
        path.replace(target / path.name)
    return str(target)


def preflight_records(prepared: dict, judgments: dict, blind: dict, *, attempt: int = 0,
                      frozen: dict | None = None, audit_view_path: Path | None = None) -> dict:
    if attempt not in (0, 1):
        raise ValueError("Preflight permits one bounded review retry (attempt 0 or 1)")
    if any(p.get("article_id") != prepared.get("article_id") for p in (judgments, blind)):
        raise ValueError("Preflight article identity mismatch")
    # Existing completed audits remain immutable; use a new result version.
    if judgments.get("reconciliation", {}).get("completed"):
        raise ValueError("Do not preflight an already reconciled historical result")
    policy = review_policy_of(judgments)
    if policy not in SUPPORTED_REVIEW_POLICIES:
        raise ValueError(f"Unsupported review_policy: {policy}")
    risk_weighted = policy == REVIEW_POLICY
    decisive = policy == DECISIVE_REVIEW_POLICY
    if (risk_weighted or decisive) and blind.get("review_policy") != policy:
        raise ValueError("Selective judgments and adversarial review must use the same review_policy")
    snapshot_problems = parsed_input_conflicts(prepared, view_path=audit_view_path)
    if snapshot_problems:
        raise ValueError("input_snapshot_changed: " + "; ".join(snapshot_problems)
                         + ". 停止审核并交回知识库；不得删除或重建prepared、不得改名重新冻结")
    if frozen is not None:
        if (frozen.get("article_id") != prepared.get("article_id")
                or frozen.get("article_version") != prepared.get("article_version", "")
                or frozen.get("article_sha256") != prepared.get("article_sha256", "")
                or frozen.get("knowledge_sha256") != prepared.get("knowledge_sha256", [])):
            raise ValueError("Frozen inventory source identity mismatch")
        expected = {claim_identity_key(c) for c in frozen["claims"]}
        judgment_rows = judgments.get("claims", [])
        review_rows = blind.get("claim_reviews", [])
        if len(judgment_rows) != len(expected) or {claim_identity_key(c) for c in judgment_rows} != expected:
            raise ValueError("Every frozen proposition requires a primary judgment")
        if risk_weighted:
            if frozen.get("review_policy") != REVIEW_POLICY:
                raise ValueError("Frozen inventory review_policy does not match judgments")
            for claim in frozen["claims"]:
                validate_review_risk_profile(claim)
            frozen_by_identity = {claim_identity_key(claim): claim for claim in frozen["claims"]}
            for claim in judgment_rows:
                validate_review_risk_profile(claim)
                frozen_claim = frozen_by_identity[claim_identity_key(claim)]
                if (claim.get("review_risk_level") != frozen_claim.get("review_risk_level")
                        or claim.get("review_risk_categories") != frozen_claim.get("review_risk_categories")):
                    raise ValueError("Judgment review-risk profile differs from the frozen union")
            high_risk = {claim_identity_key(c) for c in frozen["claims"]
                         if c["review_risk_level"] == "high"}
            if len(review_rows) != len(high_risk) or {claim_identity_key(c) for c in review_rows} != high_risk:
                raise ValueError("Only high-risk frozen propositions require a second independent judgment")
        elif decisive:
            if frozen.get("review_policy") not in (None, policy):
                raise ValueError("Frozen inventory review_policy does not match judgments")
            decisive_ids = {claim_identity_key(c) for c in judgment_rows if requires_second_review(c, policy)}
            if {claim_identity_key(c) for c in review_rows} - decisive_ids:
                raise ValueError("Only first-pass entailed or contradicted propositions receive a second judgment")
        elif len(review_rows) != len(expected) or {claim_identity_key(c) for c in review_rows} != expected:
            raise ValueError("Every frozen proposition requires two independent judgments")
    sources = {}
    for n, name in enumerate(prepared.get("knowledge_files", [])):
        path = Path(name).resolve()
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
    def locate_lines(item: dict) -> bool:
        """Fill line_start/line_end when the quote occurs exactly once in allowed text."""
        if not isinstance(item, dict) or valid_citation(item):
            return False
        try:
            lines, chunks = sources[str(Path(item["source_file"]).resolve()).casefold()]
        except (KeyError, TypeError, ValueError):
            return False
        quote = str(item.get("quote", ""))
        text = "\n".join(lines)
        if not quote:
            return False
        # The quote may also appear outside evidence (e.g. a writing-fact line);
        # only an occurrence inside allowed evidence counts, and it must be unique.
        spans, position = [], text.find(quote)
        while position != -1:
            start = text[:position].count("\n") + 1
            end = start + quote.count("\n")
            if any(c.get("_evidence_eligible", True) and c["line_start"] <= start and end <= c["line_end"]
                   for c in chunks):
                spans.append((start, end))
            position = text.find(quote, position + 1)
        if len(spans) != 1:
            return False
        item["line_start"], item["line_end"] = spans[0]
        return True
    located = []
    for owner, rows in (("judgments", judgments.get("claims", [])),
                        ("adversarial_review", blind.get("claim_reviews", []))):
        for row in rows:
            for item in row.get("evidence", []) if isinstance(row, dict) else []:
                if locate_lines(item):
                    located.append(dict(claim_id=row.get("claim_id"), owner=owner, cause="citation_line_located",
                                        line_start=item["line_start"], line_end=item["line_end"]))
    reviews = {claim_identity_key(r): r for r in blind.get("claim_reviews", [])}
    if len(reviews) != len(blind.get("claim_reviews", [])):
        raise ValueError("Duplicate blind proposition identities")
    repaired, tasks = [], []
    duplicates = review_reason_duplicates(list(reviews.values()))
    for claim in judgments.get("claims", []):
        if risk_weighted:
            validate_review_risk_profile(claim)
        if not requires_second_review(claim, policy):
            continue  # decisive: a first-pass unknown is final and needs no second judgment.
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
                    # Evidence the reviewer added needs its EC-1 record as much as the first pass's did
                    # (P-005-ART-20261022-002 C060 failed render without it).
                    _attach_coreference(prepared, claim)
        invalid = ["引用不属于当前允许证据正文" for e in evidence if not valid_citation(e)]
        # The reviewer's conclusions are not records to repair: the reconciler turns them into unknown.
        # Listing them as tasks asked for a fix nobody may make once the review is assembled.
        problems = invalid + [p for p in validate_entailed_review(claim, row, duplicates) if not is_review_outcome(p)]
        if problems:
            tasks.append(dict(claim_id=claim["claim_id"], cause="component_record_review",
                              problems=problems, allowed_action="核对原文后补记录；缺真实支持则保留unknown"))
    receipt = dict(schema_version="1.0", article_id=prepared["article_id"], attempt=attempt,
                   terminal=False, next_owner="deepeval-article-audit", automatic_repairs=located + repaired,
                   review_tasks=tasks, next_action="review_records_once" if tasks and attempt == 0 else "reconcile_and_render",
                   resume_condition="完成一次独立记录复核；未解决项按unknown继续报告",
                   ready_for_reconciliation=not tasks or attempt == 1)
    judgments["audit_preflight"] = receipt
    return receipt


def _claim_ids(payload: object) -> list[str]:
    """Claim IDs in a frozen/recheck file ({"claims": [...]}) or a compact shard."""
    if isinstance(payload, dict):
        payload = payload.get("claims", payload.get("verdicts", payload.get("reviews", payload.get("claim_reviews"))))
    return [str(row.get("claim_id")) for row in payload or [] if isinstance(row, dict) and row.get("claim_id")]


def _id_ranges(ids: list[str]) -> list[str]:
    """Collapse C001,C002,C003,C007 into C001-C003, C007 for a short resume hint."""
    numbers = sorted(int(i[1:]) for i in ids if i[:1] == "C" and i[1:].isdigit())
    ranges, start = [], None
    for n, value in enumerate(numbers):
        start = value if start is None else start
        if n + 1 == len(numbers) or numbers[n + 1] != value + 1:
            ranges.append(f"C{start:03d}" if start == value else f"C{start:03d}-C{value:03d}")
            start = None
    return ranges + [i for i in ids if not (i[:1] == "C" and i[1:].isdigit())]


def snapshot_conflicts(prepared: dict | None, paths: list[Path], load,
                       view_path: Path | None = None) -> list[str]:
    """Name inputs with parsed-content changes or audit files from another snapshot."""
    if not prepared:
        return []
    conflicts = parsed_input_conflicts(prepared, view_path=view_path)
    for path in paths:
        data = load(path) if path.is_file() else None
        if isinstance(data, dict) and any(
                key in data and data[key] != prepared.get(key) for key in ("article_sha256", "knowledge_sha256")):
            conflicts.append(f"{path.name}: 来自另一套输入")
    return conflicts


def reset_attempt(result_dir: Path) -> dict:
    """Archive an unimported audit attempt so the audit can restart from extraction.

    Records refuse to be overwritten, so before this a failed attempt could only be redone by
    deleting or renaming files by hand.  Prepared stays (the inputs are locked) and the reading
    view is rebuilt from it, so a fresh attempt reads the current view guide.  Everything else
    moves to _work/attempt-N/ and the next run starts at extraction.
    A rendered report nobody has imported yet is archived with it; an imported result is history
    and is never reset (the knowledge base starts a new article version).
    """
    from result_layout import work_dir

    result_dir = result_dir.resolve()
    if (result_dir / "article_completed.json").is_file():
        raise ValueError("该结果已导入知识库，不能重置；按版本合同由知识库建立新文章版本后重新审核")
    prepared = sorted(result_dir.glob("*-prepared.json"))
    if len(prepared) != 1:
        raise ValueError("结果目录必须恰好有一个prepared，才能确定要保留的输入")
    article_id = prepared[0].name[: -len("-prepared.json")]
    work = work_dir(result_dir)
    keep = {f"{article_id}{suffix}" for suffix in VIEW_SUFFIXES}
    keep_root = {prepared[0].name, work.name}
    moved = [path for path in sorted(result_dir.iterdir()) if path.is_file() and path.name not in keep_root]
    moved += [path for path in sorted(work.iterdir()) if path.name not in keep
              and (path.is_file() or path.name.startswith(("superseded-", "reopened-"))
                   or path.name == PRE_RECONCILE_DIR)] if work.is_dir() else []
    try:
        rebuilt = view_parts(json.loads(prepared[0].read_text(encoding="utf-8-sig")))
    except (OSError, ValueError):
        rebuilt = None  # an unreadable input keeps the existing views; snapshot checks report the cause

    def write_rebuilt() -> None:
        if rebuilt is not None:
            work.mkdir(exist_ok=True)
            for suffix, text in rebuilt.items():
                (work / f"{article_id}{suffix}").write_text(text, encoding="utf-8")
    if not moved:
        write_rebuilt()
        return dict(result_dir=str(result_dir), archived=None, moved=0, next_stage="extract_first")
    attempt = 1
    while (work / f"attempt-{attempt}").exists():
        attempt += 1
    archive = work / f"attempt-{attempt}"
    archive.mkdir(parents=True)
    for path in moved:
        path.replace(archive / path.name)
    write_rebuilt()
    return dict(result_dir=str(result_dir), archived=str(archive), moved=len(moved), next_stage="extract_first",
                next_action="从第一轮抽取和覆盖复查重新开始；归档的抽取、判断和复核不得复制或迁移到新一轮")


REOPEN_FILE = "reopen.json"
# Fields a reopened review may not change for a claim outside the reopened set.
REVIEW_JUDGMENT_FIELDS = ("independent_semantic_status", "entailment_checks", "atomicity_review",
                          "contradiction_evidence_fragment", "contradiction_alignment", "evidence")


def reopen_review(result_dir: Path, article_id: str) -> dict:
    """Reopen the second judgments of claims downgraded only for a record problem; no reset.

    P-005-ART-20261029-002: three claims both passes judged entailed were downgraded because the
    subject fragment came from the EC-1 block; the only way back was `reset`, which re-extracted and
    re-judged all 105 claims.  Here the unreconciled records saved by reconciliation are restored,
    and only the listed claims' review rows may change (assemble-review enforces it).  Once per
    result, and never after the knowledge base imported it.
    """
    from result_layout import work_dir

    result_dir = result_dir.resolve()
    work = work_dir(result_dir)
    if (result_dir / "article_completed.json").is_file():
        raise ValueError("该结果已导入知识库，不能重开复核；按版本合同由知识库建立新文章版本后重新审核")
    judgments_path = result_dir / f"{article_id}-judgments.json"
    review_path = result_dir / f"{article_id}-adversarial-review.json"
    judgments = json.loads(judgments_path.read_text(encoding="utf-8-sig"))
    if not judgments.get("reconciliation", {}).get("completed"):
        raise ValueError("结果尚未收敛：直接按check的review_tasks修正复核行后重新assemble-review，不需要重开")
    if any(work.glob("reopened-*")):
        raise ValueError("该结果已重开过一次复核；仍未通过的主张按unknown报告，交知识库按audit_process_limitation处置")
    keep = work / PRE_RECONCILE_DIR
    saved = [keep / judgments_path.name, keep / review_path.name]
    if not all(path.is_file() for path in saved):
        raise ValueError("缺少收敛前记录（该结果由旧版脚本收敛）；只能用reset重新审核，或交知识库按audit_process_limitation处置")
    eligible = [c for c in judgments.get("claims", []) if record_only_downgrade(c)]
    if not eligible:
        raise ValueError("没有“两轮都判支持、只因记录问题降级”的主张；其他unknown是复核结论，不能重开")
    before = json.loads(saved[0].read_text(encoding="utf-8-sig"))
    keys = {claim_identity_key(c) for c in eligible}
    # Claim IDs are renumbered at reconciliation; the review files use the unreconciled ones.
    reopened = [str(c["claim_id"]) for c in before.get("claims", []) if claim_identity_key(c) in keys]
    n = 1
    while (work / f"reopened-{n}").exists():
        n += 1
    archive = work / f"reopened-{n}"
    archive.mkdir(parents=True)
    moved = [p for p in sorted(result_dir.iterdir()) if p.is_file() and not p.name.endswith("-prepared.json")]
    moved += sorted(work.glob("receipt*.json"))
    for path in moved:
        path.replace(archive / path.name)
    for source in saved:
        (result_dir / source.name).write_bytes(source.read_bytes())
    atomic_write_json(work / REOPEN_FILE, dict(article_id=article_id, claim_ids=reopened,
                                                identity_keys=[list(k) for k in keys], archived=str(archive)))
    reasons = {str(c["claim_id"]): (c.get("reconciliation") or {}).get("reasons") for c in eligible}
    return dict(result_dir=str(result_dir), archived=str(archive), reopened_claim_ids=reopened,
                reconciled_reasons=reasons,
                next_action=("只改_work/reviews-*.json中这些claim的复核行，按原因补记录（主体可写证据原词如We，或该证据块EC-1归属中的主体），"
                             "结论保持entailed；其他claim的复核行不得改动。然后依次assemble-review、check、reconcile、render"))


def reopened_review_errors(result_dir: Path, assembled: dict) -> list[str]:
    """While a review is reopened, only the reopened claims' rows may change, and only their records."""
    from result_layout import work_dir

    marker = work_dir(result_dir) / REOPEN_FILE
    if not marker.is_file():
        return []
    reopen = json.loads(marker.read_text(encoding="utf-8-sig"))
    keep = work_dir(result_dir) / PRE_RECONCILE_DIR / f"{reopen['article_id']}-adversarial-review.json"
    if not keep.is_file():
        return []
    before = {claim_identity_key(r): r for r in json.loads(keep.read_text(encoding="utf-8-sig")).get("claim_reviews", [])}
    allowed = {tuple(k) for k in reopen.get("identity_keys", [])}
    errors = []
    for row in assembled.get("claim_reviews", []):
        key = claim_identity_key(row)
        if key in allowed:
            if row.get("independent_semantic_status") != "entailed":
                errors.append(f"{row.get('claim_id')}：重开只补记录，复核结论须保持entailed")
            continue
        old = before.get(key)
        if old is None or any(row.get(f) != old.get(f) for f in REVIEW_JUDGMENT_FIELDS):
            errors.append(f"{row.get('claim_id')}：不在重开范围内，复核行不得改动")
    return errors


def audit_status(result_dir: Path) -> dict:
    """Report which audit stages exist in a result directory without printing their content.

    A resumed audit reads this instead of reopening prepared, inventories,
    judgments or review files to work out where it stopped; it then reads only
    the files the next step needs. Read-only.
    """
    from result_layout import work_dir

    work = work_dir(result_dir)
    prepared_files = sorted(result_dir.glob("*-prepared.json"))
    article_id = prepared_files[0].name[: -len("-prepared.json")] if len(prepared_files) == 1 else ""

    def load(path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None

    stages: list[dict] = []

    def stage(name: str, done: bool, file: str = "", **extra) -> None:
        stages.append(dict(stage=name, done=bool(done), **({"file": file} if file else {}), **extra))

    judgments_path = result_dir / f"{article_id}-judgments.json"
    review_path = result_dir / f"{article_id}-adversarial-review.json"
    judgments = load(judgments_path) if judgments_path.is_file() else None
    reconciled = bool((judgments or {}).get("reconciliation", {}).get("completed"))
    rendered = (result_dir / "faithfulness_summary.md").is_file() and reconciled
    stage("prepare", len(prepared_files) == 1, prepared_files[0].name if len(prepared_files) == 1 else "",
          **({"problem": f"根目录有{len(prepared_files)}个prepared"} if len(prepared_files) > 1 else {}))
    view = work / f"{article_id}-audit-view.md"
    stage("view", view.is_file() or rendered, f"_work/{view.name}")
    stage("extract_first", (work / "first.json").is_file() or rendered, "_work/first.json")
    # v1.9 writes coverage.json and freeze derives blind.json from it; a v1.8 attempt has a blind pass instead.
    second = "coverage" if (work / "coverage.json").is_file() or not (work / "blind.json").is_file() else "blind"
    stage("coverage_review" if second == "coverage" else "extract_blind",
          (work / f"{second}.json").is_file() or rendered, f"_work/{second}.json")
    frozen = load(work / "frozen.json") if (work / "frozen.json").is_file() else None
    frozen_ids = _claim_ids(frozen)
    stage("freeze", frozen is not None or rendered, "_work/frozen.json", claims=len(frozen_ids))
    shards = sorted(work.glob("verdicts-*.json")) if work.is_dir() else []
    judged = {i for path in shards for i in _claim_ids(load(path))}
    missing_verdicts = [i for i in frozen_ids if i not in judged]
    stage("primary_verdicts", (frozen is not None and not missing_verdicts) or judgments is not None,
          shards=[p.name for p in shards], **({"missing": _id_ranges(missing_verdicts)} if frozen and missing_verdicts else {}))
    stage("assemble_judgments", judgments is not None, judgments_path.name)
    recheck = load(work / "recheck.json") if (work / "recheck.json").is_file() else None
    recheck_ids = _claim_ids(recheck)
    stage("recheck_list", recheck is not None or reconciled or review_path.is_file(), "_work/recheck.json",
          claims=len(recheck_ids))
    review_shards = sorted(work.glob("reviews-*.json")) if work.is_dir() else []
    reviewed = {i for path in review_shards for i in _claim_ids(load(path))}
    missing_reviews = [i for i in recheck_ids if i not in reviewed]
    stage("second_reviews", (recheck is not None and not missing_reviews) or review_path.is_file(),
          shards=[p.name for p in review_shards],
          **({"missing": _id_ranges(missing_reviews)} if recheck is not None and missing_reviews else {}))
    stage("assemble_review", review_path.is_file(), review_path.name)
    receipt = load(work / "receipt.json") if (work / "receipt.json").is_file() else None
    stage("check", bool(receipt and receipt.get("ready_for_reconciliation")) or reconciled, "_work/receipt.json",
          **({"next_action": receipt.get("next_action"), "attempt": receipt.get("attempt"),
              "review_tasks": len(receipt.get("review_tasks") or [])} if receipt else {}))
    stage("reconcile", reconciled)
    stage("render", rendered, "faithfulness_summary.md")
    pending = next((item for item in stages if not item["done"]), None)
    snapshot = snapshot_conflicts(load(prepared_files[0]) if len(prepared_files) == 1 else None,
                                  [*sorted(work.glob("*.json")), judgments_path, review_path] if work.is_dir()
                                  else [judgments_path, review_path], load, view_path=view)
    known = {"_work", "faithfulness_summary.md", "faithfulness_highlight.html", f"{article_id}_faithfulness_details.md",
             "unsupported-disposition.json", "article_completed.json"} | {p.name for p in prepared_files} | {
             judgments_path.name, review_path.name}
    other = sorted(p.name for p in result_dir.iterdir() if p.name not in known) if result_dir.is_dir() else []
    if snapshot and not rendered:
        return dict(result_dir=str(result_dir), article_id=article_id, stages=stages,
                    next_stage="input_snapshot_changed", snapshot_conflicts=snapshot,
                    next_action="解析后的文章单元/证据范围在prepare之后变了，或_work混有其他输入版本的产物："
                                "停止审核并交回知识库；"
                                "不删除或重建prepared，不改名重新冻结")
    return dict(result_dir=str(result_dir), article_id=article_id, stages=stages,
                next_stage=pending["stage"] if pending else "done",
                next_action=("按SKILL运行顺序执行next_stage；只读取该步需要的文件，不重读已完成阶段的产物"
                             if pending else "审核产物已齐全；交回知识库导入"),
                **({"other_files": other} if other else {}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    status = sub.add_parser("status", help="只读：报告结果目录中各阶段是否完成及缺失的主张ID")
    status.add_argument("--result-dir", type=Path, required=True)
    reset = sub.add_parser("reset", help="渲染前归档失败的审核尝试，保留prepared与阅读视图，从抽取重新开始")
    reset.add_argument("--result-dir", type=Path, required=True)
    reopen = sub.add_parser("reopen-review", help="收敛后只重开“两轮都支持、只因记录问题降级”的主张复核，不重置整轮")
    reopen.add_argument("--result-dir", type=Path, required=True)
    reopen.add_argument("--article-id", required=True)
    freeze = sub.add_parser("freeze")
    for flag in ("prepared", "first-inventory", "output"):
        freeze.add_argument("--" + flag, type=Path, required=True)
    second = freeze.add_mutually_exclusive_group(required=True)
    second.add_argument("--coverage-review", type=Path, help="覆盖复查文件（v1.9 默认）；freeze 据此写出 _work/blind.json")
    second.add_argument("--blind-inventory", type=Path, help="旧版两轮盲抽取的第二轮（续跑 v1.8 尝试时用）")
    freeze.add_argument("--replacements", type=Path)
    check = sub.add_parser("check")
    for flag in ("prepared", "judgments", "adversarial-review", "receipt"):
        check.add_argument("--" + flag, type=Path, required=True)
    check.add_argument("--frozen-inventory", type=Path, required=True)
    check.add_argument("--attempt", type=int, default=0)
    recheck = sub.add_parser("recheck-list")
    for flag in ("judgments", "output"):
        recheck.add_argument("--" + flag, type=Path, required=True)
    view = sub.add_parser("view")
    for flag in ("prepared", "output"):
        view.add_argument("--" + flag, type=Path, required=True)
    primary = sub.add_parser("assemble-judgments")
    for flag in ("prepared", "frozen-inventory", "first-inventory", "blind-inventory", "output"):
        primary.add_argument("--" + flag, type=Path, required=True)
    primary.add_argument("--verdicts", type=Path, action="append", required=True)
    second = sub.add_parser("assemble-review")
    for flag in ("prepared", "frozen-inventory", "blind-inventory", "recheck", "output"):
        second.add_argument("--" + flag, type=Path, required=True)
    second.add_argument("--reviews", type=Path, action="append", required=True)
    args = parser.parse_args()
    def read(path):
        return json.loads(path.read_text(encoding="utf-8-sig"))
    if args.mode == "status":
        payload = audit_status(args.result_dir)
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return
    if args.mode == "reset":
        print(json.dumps(reset_attempt(args.result_dir), ensure_ascii=False, separators=(",", ":")))
        return
    if args.mode == "reopen-review":
        print(json.dumps(reopen_review(args.result_dir, args.article_id), ensure_ascii=False, separators=(",", ":")))
        return
    if args.mode == "freeze":
        # Refreezing the same file is fine until judging starts (refused below); a second file
        # name would leave `status` reading the stale frozen.json.
        result_dir = args.prepared.resolve().parent
        judged = sorted(result_dir.glob("*-judgments.json"))
        if list((result_dir / "_work").glob("frozen*.json")):  # verdict shards only count after a freeze
            judged += sorted((result_dir / "_work").glob("verdicts-*.json"))
        if judged:
            # Patching a pass after judging would let verdicts and reviews outlive the claims they judged.
            raise ValueError(f"This attempt has already been judged ({judged[0].name}); its extraction passes can no "
                             f"longer change. Run: python scripts/audit_preflight.py reset --result-dir \"{result_dir}\" "
                             "and restart from a fresh first pass and coverage review")
        prepared, first = read(args.prepared), read(args.first_inventory)
        if args.coverage_review:
            blind = second_pass_from_coverage(prepared, first, read(args.coverage_review))
            blind_path = args.output.with_name("blind.json")  # what assemble-* read as the second pass
            if blind_path.is_file() and read(blind_path).get("derived_from") != "coverage_review":
                raise ValueError(f"{blind_path} is a blind pass written by an earlier two-pass attempt; run reset "
                                 "before starting a coverage-review attempt")
        else:
            blind = read(args.blind_inventory)
        payload = freeze_inventories(prepared, first, blind, read(args.replacements) if args.replacements else None,
                                     coverage_review=bool(args.coverage_review))
        if args.coverage_review:
            atomic_write_json(blind_path, blind)
        atomic_write_json(args.output, payload)
        claims_path = args.output.with_name("claims.md")  # what the judges read instead of frozen.json
        claims_path.write_text(claims_list(payload), encoding="utf-8")
        # Print counts only: echoing the whole frozen union back to the model costs more than the view itself.
        origins = [c["inventory_origin"] for c in payload["claims"]]
        payload = {"output": str(args.output.resolve()), "claims_list": str(claims_path.resolve()), "claims": len(origins),
                   **{o: origins.count(o) for o in ("both", "first_only", "blind_only")},
                   "reworded_pairs": sum(r.get("pairing") == "content_word_overlap" for r in payload["replacements"]),
                   "excluded_source_refs": len(payload["excluded_source_refs"])}
    elif args.mode == "recheck-list":
        payload = recheck_list(read(args.judgments))
        atomic_write_json(args.output, payload)
        payload = {"output": str(args.output.resolve()), "claims": len(payload["claims"])}
    elif args.mode == "view":
        prepared = read(args.prepared)
        view = write_views(prepared, args.output.resolve().parent, str(prepared.get("article_id", "")))
        if view.resolve() != args.output.resolve():  # an explicit other name still gets the full view
            args.output.write_text(build_view(prepared), encoding="utf-8")
        payload = {"output": str(args.output.resolve())}
    elif args.mode in ("assemble-judgments", "assemble-review"):
        if args.output.exists():
            refuse_final_record(args.output)
        if args.mode == "assemble-judgments":
            assembled = assemble_judgments(read(args.prepared), read(args.frozen_inventory),
                                           read(args.first_inventory), [read(p) for p in args.verdicts],
                                           read(args.blind_inventory))
            counts = {s: sum(c["semantic_status"] == s for c in assembled["claims"])
                      for s in ("entailed", "unknown", "contradicted")}
        else:
            assembled = assemble_review(read(args.prepared), read(args.frozen_inventory),
                                        read(args.blind_inventory), read(args.recheck),
                                        [read(p) for p in args.reviews])
            counts = {"claim_reviews": len(assembled["claim_reviews"])}
            changed = reopened_review_errors(args.output.resolve().parent, assembled)
            if changed:
                raise ValueError("重开复核只允许补记录：" + "；".join(changed))
        superseded = supersede_assembled(args.output, args.mode) if args.output.exists() else None
        atomic_write_json(args.output, assembled)
        payload = {"output": str(args.output.resolve()), **counts, **({"superseded": superseded} if superseded else {})}
    else:
        judgments = read(args.judgments)
        prepared = read(args.prepared)
        audit_view_path = args.prepared.parent / "_work" / f"{prepared.get('article_id', '')}-audit-view.md"
        payload = preflight_records(prepared, judgments, read(args.adversarial_review),
                                    attempt=args.attempt, frozen=read(args.frozen_inventory),
                                    audit_view_path=audit_view_path)
        atomic_write_json(args.judgments, judgments)
        atomic_write_json(args.receipt, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import sys

    from audit_common import utf8_stdio

    utf8_stdio()
    try:
        main()
    except ValueError as exc:  # a gate refusal: the message is the whole answer, a traceback only costs tokens
        print(f"refused: {exc}", file=sys.stderr)
        sys.exit(1)
