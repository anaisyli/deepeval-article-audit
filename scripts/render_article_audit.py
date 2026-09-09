"""Validate Codex judgments and render Markdown plus interactive HTML reports."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from pathlib import Path

from audit_common import (
    extract_article,
    extract_knowledge_chunks,
    normalize_inline_markdown,
    normalized_path,
    public_chunk,
    read_text,
)
from manage_handoff_contract import ManagedHandoffError, load_contract, validate_event, validate_version


MODE = "DeepEval Faithfulness 规则复现（Codex 评审，非 DeepEval 官方运行）"
VALID_VERDICTS = {"supported", "unsupported"}
LOW_COVERAGE_MIN_UNITS = 20
LOW_COVERAGE_MAX_SHARE = 0.20


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(read_text(path))


def normalized(text: str) -> str:
    return normalize_inline_markdown(text)


def read_source_lines(path: Path) -> list[str]:
    return read_text(path).splitlines()


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
    if prepared_units != current_article["article_units"]:
        raise ValueError(f"{prepared_path.name}: prepared article units no longer match the current article")
    metadata_id = current_article.get("metadata_article_id")
    if metadata_id and prepared.get("article_id") != metadata_id:
        raise ValueError(f"{prepared_path.name}: article_id does not match current article metadata")
    metadata_version = current_article.get("metadata_article_version")
    prepared_version = str(prepared.get("article_version", "")).strip()
    if metadata_version and prepared_version and prepared_version != metadata_version:
        raise ValueError(f"{prepared_path.name}: article_version does not match current article metadata")
    recorded_article_hash = str(prepared.get("article_sha256", "")).strip().lower()
    if recorded_article_hash and recorded_article_hash != sha256_file(article_path):
        raise ValueError(f"{prepared_path.name}: article SHA-256 no longer matches the current article")

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
    article_override: Path | None = None,
    knowledge_override: list[Path] | None = None,
) -> list[dict]:
    article_path, knowledge_paths, eligible_by_file = validate_prepared(
        prepared, prepared_path, article_override, knowledge_override
    )
    if str(judgments.get("schema_version")) != "1.0":
        raise ValueError(f"{judgment_path.name}: unsupported judgment schema_version")
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
    # A very low factual share is a common failure mode: the model can obtain
    # an apparently perfect score by silently classifying most article units as
    # non-factual.  Managed runs must provide a second-pass, unit-level review
    # before such a result is accepted.
    if prepared.get("integration_mode") == "manage-article-knowledge-v0.6":
        claim_unit_ids = {claim["unit_id"] for claim in claims}
        total_units = len(units)
        factual_units = len(claim_unit_ids)
        share = factual_units / total_units if total_units else 0.0
        if total_units >= LOW_COVERAGE_MIN_UNITS and share < LOW_COVERAGE_MAX_SHARE:
            review = judgments.get("coverage_review")
            if not isinstance(review, dict):
                raise ValueError(
                    f"{judgment_path.name}: factual coverage is {factual_units}/{total_units} "
                    "(<20%); a complete coverage_review second pass is required"
                )
            if review.get("reviewed_unit_count") != total_units:
                raise ValueError(
                    f"{judgment_path.name}: coverage_review.reviewed_unit_count must equal "
                    f"{total_units}"
                )
            classifications = review.get("unit_classifications")
            if not isinstance(classifications, list) or len(classifications) != total_units:
                raise ValueError(
                    f"{judgment_path.name}: coverage_review must classify every article unit"
                )
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
                        f"{judgment_path.name}: each coverage_review row needs classification "
                        "and a concrete reason"
                    )
                by_id[unit_id] = row
            if set(by_id) != set(units):
                raise ValueError(f"{judgment_path.name}: coverage_review unit IDs do not match prepared units")
            declared_factual = sum(row["classification"] == "factual" for row in by_id.values())
            declared_non_factual = total_units - declared_factual
            if review.get("factual_unit_count") != declared_factual or review.get("non_factual_unit_count") != declared_non_factual:
                raise ValueError(f"{judgment_path.name}: coverage_review counts do not match classifications")
            missing_claims = [unit_id for unit_id, row in by_id.items() if row["classification"] == "factual" and unit_id not in claim_unit_ids]
            if missing_claims:
                raise ValueError(
                    f"{judgment_path.name}: factual coverage rows without claims: {', '.join(sorted(missing_claims))}"
                )
            candidate_ids = {
                item["unit_id"] for item in prepared.get("article_units", [])
                if item.get("candidate_evidence")
            }
            excluded_candidates = candidate_ids - claim_unit_ids
            reconsidered = set(review.get("reconsidered_candidate_unit_ids") or [])
            if not excluded_candidates.issubset(reconsidered):
                missing = ", ".join(sorted(excluded_candidates - reconsidered))
                raise ValueError(
                    f"{judgment_path.name}: candidate-bearing units excluded without explicit reconsideration: {missing}"
                )
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
        if not unit_claims:
            kind, status = "non_factual", "non_factual"
        elif all(c["verdict"] == "supported" for c in unit_claims):
            kind, status = "factual", "fully_supported"
        elif any(c["verdict"] == "supported" for c in unit_claims):
            kind, status = "factual", "partially_supported"
        else:
            kind, status = "factual", "unsupported"
        unit_rows.append({**item, "kind": kind, "status": status, "claims": unit_claims})
    claims_by_line: dict[int, list[dict]] = {}
    for row in unit_rows:
        claims_by_line.setdefault(row["line"], []).extend(row["claims"])
    line_rows = []
    for item in case["prepared"]["article_lines"]:
        line_claims = claims_by_line.get(item["line"], [])
        if not line_claims:
            kind, status = "non_factual", "non_factual"
        elif all(c["verdict"] == "supported" for c in line_claims):
            kind, status = "factual", "fully_supported"
        elif any(c["verdict"] == "supported" for c in line_claims):
            kind, status = "factual", "partially_supported"
        else:
            kind, status = "factual", "unsupported"
        line_rows.append({**item, "kind": kind, "status": status, "claims": line_claims})
    factual = [row for row in unit_rows if row["kind"] == "factual"]
    return {
        "total_units": len(unit_rows),
        "factual_units": len(factual),
        "non_factual_units": len(unit_rows) - len(factual),
        "factual_share": len(factual) / len(unit_rows) * 100 if unit_rows else None,
        "fully_supported_units": sum(row["status"] == "fully_supported" for row in unit_rows),
        "partially_supported_units": sum(row["status"] == "partially_supported" for row in unit_rows),
        "unsupported_units": sum(row["status"] == "unsupported" for row in unit_rows),
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
        f"> 计算：{supported} ÷ {total} = {rate:.2f}%" if rate is not None else "> 计算：分母为 0，结果 N/A",
        "",
        f"- 文章：`{prepared['article_file']}`",
        *[f"- 知识文件：`{path}`" for path in prepared["knowledge_files"]],
        "",
        "## 内容单元识别",
        "",
        "> 本节先展示哪些正文句子或表格行被识别为事实内容，再展示其下拆分的原子事实主张。表格字段仍按原子主张逐条核验。",
        "",
        f"> 事实内容单元：{content['factual_units']} / {content['total_units']}（{content['factual_share']:.2f}%）" if content["factual_share"] is not None else "> 事实内容单元：0 / 0",
        "",
        "| 文章行 | 内容分类 | 支持状态 | 原文内容 | 原子主张数 |",
        "|---:|---|---|---|---:|",
    ]
    status_labels = {
        "non_factual": "非事实，不计入",
        "fully_supported": "事实：全部支持",
        "partially_supported": "事实：部分支持",
        "unsupported": "事实：不支持",
    }
    for row in content["rows"]:
        lines.append(
            "| " + " | ".join(
                md_cell(value)
                for value in (
                    row["line"],
                    "事实" if row["kind"] == "factual" else "非事实",
                    status_labels[row["status"]],
                    row["text"],
                    len(row["claims"]),
                )
            ) + " |"
        )
    lines += [
        "",
        "## 原子事实主张明细",
        "",
        "| 主张 | 文章行 | 文章原文 | 原子事实主张 | 判断 | 知识库原文与位置 | 理由 |",
        "|---|---:|---|---|---|---|---|",
    ]
    for claim in claims:
        verdict = "支持" if claim["verdict"] == "supported" else "不支持"
        lines.append(
            "| "
            + " | ".join(
                md_cell(value)
                for value in (
                    claim["claim_id"],
                    claim["article_line"],
                    claim["article_quote"],
                    claim["claim"],
                    verdict,
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
    supported = sum(claim["verdict"] == "supported" for claim in claims)
    total = len(claims)
    return {
        "article_id": case["prepared"]["article_id"],
        "title": case["prepared"]["article_title"],
        "supported": supported,
        "total": total,
        "unsupported": total - supported,
        "rate": supported / total * 100 if total else None,
        "mode": MODE,
    }


def write_summary(cases: list[dict], output_dir: Path) -> Path:
    lines = [
        "# 文章知识库支持率汇总",
        "",
        f"> 评审模式：{MODE}",
        "",
        "> 主指标公式：知识库支持的事实主张数 ÷ 文章全部事实主张数。标题和各级小标题不计入。",
        "> 事实内容占比是辅助诊断：被识别为事实的句子/表格行 ÷ 正文内容单元；它不替代 Faithfulness，也不表示文字来源比例。",
        "",
        "| 文章编号 | 文章标题 | 支持主张数（分子） | 全部事实主张数（分母） | 不支持主张数 | Faithfulness | 评审模式 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for case in cases:
        row = summary_row(case)
        rate = f"{row['rate']:.2f}%" if row["rate"] is not None else "N/A"
        lines.append(
            "| "
            + " | ".join(
                md_cell(value)
                for value in (
                    row["article_id"], row["title"], row["supported"], row["total"], row["unsupported"], rate, row["mode"]
                )
            )
            + " |"
        )
    lines += [
        "",
        "## 内容单元诊断（不改变上方主指标）",
        "",
        "| 文章编号 | 正文内容单元 | 事实内容单元 | 事实内容占比 | 完全支持 | 部分支持 | 完全不支持 | 非事实不计入 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
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
                    content["unsupported_units"], content["non_factual_units"],
                )
            ) + " |"
        )
    output = output_dir / "faithfulness_summary.md"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def claim_card(claim: dict) -> str:
    supported = claim["verdict"] == "supported"
    label = "支持" if supported else "不支持"
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
        evidence = '<div class="empty">提供的知识文件中未找到支持证据</div>'
    return f'''
    <details class="claim {claim['verdict']}" data-verdict="{claim['verdict']}" open>
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
        "non_factual": "非事实 · 不计入",
        "fully_supported": "事实 · 全部支持",
        "partially_supported": "事实 · 部分支持",
        "unsupported": "事实 · 不支持",
    }
    for content_row in content["rows"]:
        item = content_row
        unit_claims = by_unit.get(item["unit_id"], [])
        verdict_class = content_row["status"].replace("_", "-")
        cards = "".join(claim_card(claim) for claim in unit_claims)
        body.append(
            f'<section class="article-line article-unit {verdict_class}" data-kind="{content_row["kind"]}" data-status="{content_row["status"]}" data-unit-id="{html.escape(item["unit_id"])}">'
            f'<div class="line-no">L{item["line"]} · {html.escape(item["unit_id"])}</div>'
            f'<div class="unit-meta"><span class="unit-status {verdict_class}">{status_labels[content_row["status"]]}</span>'
            f'<span class="unit-count">{len(unit_claims)} 条原子主张</span></div>'
            f'<p class="article-text">{html.escape(item["text"])}</p>{cards}</section>'
        )
    active = " active" if index == 0 else ""
    return f'''
    <article class="article-panel{active}" id="panel-{index}">
      <header class="article-head">
        <div><div class="eyebrow">{html.escape(prepared['article_id'])}</div><h2>{html.escape(prepared['article_title'])}</h2></div>
        <div class="score"><strong>{rate}</strong><span>{row['supported']} / {row['total']} 个原子事实主张得到支持</span><span>事实内容：{content['factual_units']} / {content['total_units']} 个单元（{factual_share}）</span></div>
      </header>
      <div class="article-body">{"".join(body)}</div>
    </article>'''


def write_html(cases: list[dict], output_dir: Path) -> Path:
    tabs = "".join(
        f'<button class="tab{" active" if index == 0 else ""}" data-target="panel-{index}">{html.escape(case["prepared"]["article_id"])}</button>'
        for index, case in enumerate(cases)
    )
    panels = "".join(article_section(case, index) for index, case in enumerate(cases))
    rows = "".join(
        f'<tr><td>{html.escape(row["article_id"])}</td><td>{html.escape(row["title"])}</td><td>{row["supported"]}</td><td>{row["total"]}</td><td>{row["unsupported"]}</td><td><strong>{row["rate"]:.2f}%</strong></td><td>{content_summary(case)["factual_units"]}/{content_summary(case)["total_units"]}</td><td>{content_summary(case)["factual_share"]:.2f}%</td></tr>'
        if row["rate"] is not None
        else f'<tr><td>{html.escape(row["article_id"])}</td><td>{html.escape(row["title"])}</td><td>0</td><td>0</td><td>0</td><td><strong>N/A</strong></td><td>{content_summary(case)["factual_units"]}/{content_summary(case)["total_units"]}</td><td>N/A</td></tr>'
        for case in cases for row in [summary_row(case)]
    )
    document = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:;">
<title>文章知识库支持率可视化</title>
<style>
:root{{--bg:#f4f7fb;--card:#fff;--ink:#172033;--muted:#667085;--line:#dce3ec;--green:#117a4b;--green-bg:#e9f8f0;--red:#b42318;--red-bg:#fff0ee;--blue:#2856a3}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 Inter,"Segoe UI","Microsoft YaHei",sans-serif}}
.shell{{max-width:1180px;margin:auto;padding:32px 20px 64px}}h1{{margin:0 0 8px;font-size:30px}}.subtitle{{color:var(--muted);margin:0 0 24px}}
.notice{{background:#fff7df;border:1px solid #f0d78a;border-radius:12px;padding:13px 16px;margin-bottom:18px}}
.summary{{width:100%;border-collapse:collapse;background:var(--card);border-radius:14px;overflow:hidden;box-shadow:0 8px 24px #253b5b12}}
.summary th,.summary td{{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left}}.summary th{{background:#eef3fa;color:#42526b;font-size:13px}}
.toolbar{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:24px 0 14px}}button{{border:1px solid var(--line);background:white;border-radius:999px;padding:8px 15px;cursor:pointer;color:var(--ink)}}button.active{{background:var(--blue);border-color:var(--blue);color:white}}
.filters{{margin-left:auto;display:flex;gap:8px}}.article-panel{{display:none}}.article-panel.active{{display:block}}
.article-head{{display:flex;justify-content:space-between;gap:22px;align-items:flex-start;background:var(--card);padding:22px;border-radius:16px 16px 0 0;border-bottom:1px solid var(--line)}}
.article-head h2{{margin:2px 0 0;font-size:24px}}.eyebrow{{color:var(--blue);font-weight:700;letter-spacing:.04em}}.score{{min-width:210px;text-align:right}}.score strong{{display:block;font-size:34px;color:var(--blue)}}.score span{{color:var(--muted)}}
.article-body{{background:var(--card);padding:8px 22px 28px;border-radius:0 0 16px 16px;box-shadow:0 8px 24px #253b5b12}}
.article-line{{position:relative;padding:18px 18px 16px 104px;border-bottom:1px solid #edf0f4}}.line-no{{position:absolute;left:8px;top:22px;color:#98a2b3;font:12px ui-monospace,monospace;white-space:nowrap}}.article-text{{margin:7px 0 12px;font-family:Georgia,"Times New Roman",serif;font-size:17px}}
.unit-meta{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12px}}.unit-status{{border-radius:999px;padding:2px 9px;font-weight:700}}.unit-count{{color:var(--muted)}}
.fully-supported .unit-status{{background:var(--green);color:white}}.partially-supported .unit-status{{background:#9a6700;color:white}}.unsupported .unit-status{{background:var(--red);color:white}}.non-factual .unit-status{{background:#e7ebf0;color:#536174}}.fully-supported .article-text{{border-left:4px solid var(--green);padding-left:12px}}.partially-supported .article-text{{border-left:4px solid #d99000;padding-left:12px}}.unsupported .article-text{{border-left:4px solid var(--red);padding-left:12px}}.non-factual .article-text{{border-left:4px solid #c4cad3;padding-left:12px;color:#667085}}
.claim{{margin:9px 0;border:1px solid var(--line);border-radius:10px;overflow:hidden}}.claim.supported{{border-color:#9bd5b6}}.claim.unsupported{{border-color:#efaaa3}}
.claim summary{{cursor:pointer;padding:10px 12px;background:#f9fafb;display:flex;gap:9px;align-items:flex-start}}.claim.supported summary{{background:var(--green-bg)}}.claim.unsupported summary{{background:var(--red-bg)}}
.badge{{border-radius:999px;padding:1px 8px;font-size:12px;font-weight:700;white-space:nowrap}}.supported .badge{{background:var(--green);color:white}}.unsupported .badge{{background:var(--red);color:white}}.claim-id{{font:12px ui-monospace,monospace;color:var(--muted);padding-top:2px}}
.claim-body{{padding:13px 15px;display:grid;grid-template-columns:1fr 1fr;gap:14px}}.claim-body>div:last-child{{grid-column:1/-1}}.claim-body p{{margin:3px 0}}blockquote{{margin:6px 0;padding:10px 13px;border-left:3px solid var(--blue);background:#f6f8fc}}.source{{color:var(--muted);font-size:12px;overflow-wrap:anywhere}}.empty{{color:var(--red);padding:8px 0}}
body[data-filter="supported"] .article-line:not(.fully-supported),body[data-filter="unsupported"] .article-line:not(.unsupported):not(.partially-supported),body[data-filter="factual"] .article-line.non-factual,body[data-filter="nonfactual"] .article-line:not(.non-factual){{display:none}}
body[data-filter="supported"] .claim.unsupported,body[data-filter="unsupported"] .claim.supported{{display:none}}
@media(max-width:760px){{.article-head{{display:block}}.score{{text-align:left;margin-top:12px}}.filters{{margin-left:0}}.claim-body{{grid-template-columns:1fr}}.claim-body>div:last-child{{grid-column:auto}}.summary-wrap{{overflow:auto}}}}
</style></head><body data-filter="all"><main class="shell">
<h1>文章知识库支持率可视化</h1><p class="subtitle">先看哪些正文内容被识别为事实，再看这些事实是否得到知识库支持。</p>
<div class="notice"><strong>{MODE}</strong><br>主指标：知识库支持的原子事实主张 ÷ 全部原子事实主张。<br>“事实内容占比”是辅助诊断，不表示文章文字来源比例；灰色内容不参与主指标。</div>
<div class="summary-wrap"><table class="summary"><thead><tr><th>文章</th><th>标题</th><th>支持（分子）</th><th>全部主张（分母）</th><th>不支持</th><th>Faithfulness</th><th>事实单元</th><th>事实内容占比</th></tr></thead><tbody>{rows}</tbody></table></div>
<div class="toolbar"><div>{tabs}</div><div class="filters"><button class="filter active" data-filter="all">全部</button><button class="filter" data-filter="factual">只看事实</button><button class="filter" data-filter="supported">只看全部支持</button><button class="filter" data-filter="unsupported">只看不支持</button><button class="filter" data-filter="nonfactual">只看非事实</button></div></div>
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
) -> list[dict]:
    cases = []
    for judgment_path in sorted(input_dir.glob("*-judgments.json")):
        stem = judgment_path.name[: -len("-judgments.json")]
        prepared_path = input_dir / f"{stem}-prepared.json"
        if not prepared_path.exists():
            raise ValueError(f"Missing prepared JSON for {judgment_path.name}")
        prepared = load_json(prepared_path)
        judgments = load_json(judgment_path)
        claims = validate_case(
            prepared, judgments, prepared_path, judgment_path, article_override, knowledge_override
        )
        cases.append({"prepared": prepared, "judgments": judgments, "claims": claims})
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

    cases = discover_cases(input_dir, args.article, args.knowledge)
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
