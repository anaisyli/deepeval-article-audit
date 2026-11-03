"""Compact reading view and assemblers for decisive_recheck_v1 audits.

The model writes only semantic content: unit classifications, atomic
propositions, verdicts, reasons, verbatim quotes and component checks. This
module copies identity and mechanical fields from prepared/frozen artifacts so
the model never retypes them. It never decides factuality or entailment, and
every validator downstream (check, reconcile, render) runs unchanged.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

from audit_common import STRUCTURAL_UNIT_TYPES, extract_knowledge_chunks, normalize_inline_markdown, read_text
from evidence_identity import validate_coreference
from reconcile_article_audit import (
    AUTO_CHECK_REASONS, AUTO_NOT_APPLICABLE, CHECK_KEYS, DECISIVE_REVIEW_POLICY, QUANTITY_RE, SCOPE_RE, STATUS_FIELDS,
    has_causal_effect_signal, is_pure_question,
)

MODE = "DeepEval Faithfulness 规则复现（Codex 评审，非 DeepEval 官方运行）"
STRUCTURAL_REASON = "原始Markdown或DOCX结构已将该单元标记为标题、小标题或表格表头，确定性排除且不进入主张分母。"
UNION_REASON = "另一轮盲抽取在该单元识别出可验证主张，按并集规则纳入分母。"
FACTUAL_REASON = "本轮在该单元抽出{count}条可验证主张，按主张清单计为可验证单元。"
SEMANTIC_STATUSES = ("entailed", "unknown", "contradicted")
# The renderer's NON_FACTUAL_CATEGORIES, in the order and wording shown to extraction passes.
# An extraction subagent may read only the view, so the view carries the list (P-005 2026-10-27:
# a blind pass invented `discourse_framing` and freeze stopped the run).
NONFACTUAL_CATEGORY_HELP = (
    ("heading", "没有 Markdown 标记的孤立标题行"),
    ("question", "以问号结尾的问句（不以问号结尾的单元不能用）"),
    ("transition", "只承上启下，不陈述任何可核验关系"),
    ("cta", "联系、询价等行动号召"),
    ("subjective", "纯主观感受或评价，没有可核验的对象关系"),
    ("hypothetical", "假设情境，不断言现实事实"),
    ("pure_recommendation", "不含事实前提的纯建议"),
    ("title", "文章标题（通常已结构排除）"),
    ("table_header", "表格表头（通常已结构排除）"),
)
NONFACTUAL_CATEGORY_ORDER = tuple(name for name, _ in NONFACTUAL_CATEGORY_HELP)
EXTRACTION_GUIDE = [
    "## 抽取轮写法（第一轮写 `_work/first.json`；之后另一个上下文做覆盖复查）",
    "",
    "```json",
    '{"coverage_review": {"unit_classifications": ['
    '{"unit_id": "U009", "classification": "non_factual", "category": "transition", '
    '"reason": "只承上启下，没有可独立判断的关系。"}]},',
    ' "claim_inventory": ['
    '{"unit_id": "U004", "article_quote": "原文中逐字连续的片段", "claim": "带主语和谓词的完整命题"}]}',
    "```",
    "",
    "- 每个未标“结构排除”的单元二选一：在 `claim_inventory` 写出它的主张（不再写分类行），"
    "或写一条 `non_factual` 分类行。",
    "- `claim` 是能单独判断真假的完整命题；与引文相同时省略 `claim`，主张覆盖整个单元时连 `article_quote` 也可省略。"
    "补了原文没有的主语或谓词时写 `derivation_note`。逐条阅读写出，不用脚本切句。",
    "- 独立谓词、条件、因果、转折各拆成一条；同一主体和谓词下并列的成员保留为一条。",
    "- 同一单元里每个事实只写一次：不要既写概括句（“流程包括出设计稿”）又写逐项句（“第一步出设计稿”）。",
    "- `non_factual` 的 `category` 只能用下列之一，理由至少12字，不能以“没有证据”作理由：",
    *[f"  - `{name}`：{meaning}" for name, meaning in NONFACTUAL_CATEGORY_HELP],
    "",
    "### 覆盖复查（另一个上下文写 `_work/coverage.json`）",
    "",
    "读本视图和第一轮的 `first.json`，不重新抽取全文，只做两件事：",
    "- 第一轮判为 `non_factual` 的每个单元：同意排除，就写一条自己的 `non_factual` 行（自己的类别和理由）；"
    "不同意，就在 `claim_inventory` 写出这个单元的主张。",
    "- 第一轮已有主张的单元里漏掉的独立事实：在 `claim_inventory` 补写。只写第一轮没写的事实，"
    "不改写、不重复第一轮已有的主张，也不给这些单元写分类行。",
    "- 不取消第一轮的主张；第一轮的主张有问题时在交付说明里指出，不在本文件里删改。",
    "",
    "```json",
    '{"coverage_review": {"unit_classifications": ['
    '{"unit_id": "U009", "classification": "non_factual", "category": "transition", '
    '"reason": "只预告下文结构，没有可核验的关系。"}]},',
    ' "claim_inventory": [{"unit_id": "U012", "article_quote": "原文中逐字连续的片段", "claim": "第一轮漏掉的完整命题"}]}',
    "```",
    "",
]


# ---------------------------------------------------------------- reading view

VIEW_SUFFIXES = ("-audit-view.md", "-extract-view.md", "-evidence-view.md")


def view_parts(prepared: dict) -> dict[str, str]:
    """The full reading view plus the two parts each reader actually needs.

    Extraction decides claims before reading evidence (method §2), so its part holds the guide
    and article units only; judges get the knowledge files and evidence.  Splitting saves each
    extraction pass the evidence (about 2K tokens per P-005 article) and keeps evidence out of
    extraction.  The full view stays the record `status` and the snapshot check read.
    """
    full = build_view(prepared)
    lines = full.split("\n")
    head = lines[:4]  # title, blank, the note on verbatim copying, blank
    def section(start: str, end: str | None) -> list[str]:
        i = next(n for n, line in enumerate(lines) if line.startswith(start))
        j = next((n for n, line in enumerate(lines) if end and line.startswith(end)), len(lines))
        return lines[i:j]
    extract = head + section("## 抽取轮写法", "## 允许证据正文")
    evidence = head + section("## 知识文件", "## 抽取轮写法") + section("## 允许证据正文", None)
    return {"-audit-view.md": full, "-extract-view.md": "\n".join(extract).rstrip() + "\n",
            "-evidence-view.md": "\n".join(evidence).rstrip() + "\n"}


def write_views(prepared: dict, work: Path, article_id: str) -> Path:
    """Write the three views into `work`; return the full view's path."""
    work.mkdir(parents=True, exist_ok=True)
    for suffix, text in view_parts(prepared).items():
        (work / f"{article_id}{suffix}").write_text(text, encoding="utf-8")
    return work / f"{article_id}-audit-view.md"


def claims_list(frozen: dict) -> str:
    """One line per frozen claim for the judges: frozen.json repeats identity fields for the
    scripts (about 14K tokens per P-005 article); this list is about 2K."""
    out = ["# 冻结主张清单（主判断按此逐条写一行；证据见 evidence-view）", ""]
    for claim in frozen.get("claims", []):
        out.append(f"{claim['claim_id']} [{claim['unit_id']}] {claim['claim']}")
        if normalize_inline_markdown(str(claim.get("article_quote", ""))) != str(claim.get("claim", "")):
            out.append(f"    原文：{claim.get('article_quote', '')}")
    return "\n".join(out) + "\n"

def build_view(prepared: dict) -> str:
    """Render article units and allowed evidence once, without duplicated candidates."""
    files = [str(path) for path in prepared.get("knowledge_files", [])]
    alias = {str(Path(path).resolve()).casefold(): f"F{n}" for n, path in enumerate(files, 1)}
    lines = [
        f"# 审核阅读视图：{prepared.get('article_id', '')} {prepared.get('article_version', '')}".rstrip(),
        "",
        "机读记录仍是 prepared.json；本视图只为减少重复读取，内容与之等价。"
        "引用证据时从下方编号行逐字复制连续片段；`source_file` 可写文件别名（F1…）。",
        "",
        "## 知识文件",
        "",
    ]
    lines += [f"- F{n} = {path}" for n, path in enumerate(files, 1)]
    lines += ["", *EXTRACTION_GUIDE]
    lines += [
        "## 文章单元",
        "",
        "格式：`单元 L行号 [类型｜信号] 候选证据块`，下一行是单元正文。标为“结构排除”的单元由脚本确定性排除，抽取时不必分类。",
        "",
    ]
    for unit in prepared.get("article_units", []):
        unit_type = str(unit.get("unit_type", ""))
        tags = [f"{unit_type}·结构排除"] if unit_type in STRUCTURAL_UNIT_TYPES else []
        signals = unit.get("claim_review_signals") or []
        if signals:
            tags.append("信号:" + ",".join(signals))
        head = f"- {unit['unit_id']} L{unit.get('line')}"
        if tags:
            head += " [" + "｜".join(tags) + "]"
        candidates = [str(c.get("chunk_id")) for c in unit.get("candidate_evidence") or [] if c.get("chunk_id")]
        if candidates:
            head += " 候选:" + ",".join(candidates)
        lines += [head, f"  {unit.get('text', '')}"]

    lines += ["", "## 允许证据正文（只有这些行可作 evidence 引用）", ""]
    offset = 0
    for path in files:
        source = Path(path)
        file_alias = alias.get(str(source.resolve()).casefold(), "F?")
        raw = read_text(source).splitlines()
        chunks = extract_knowledge_chunks(source)
        for index, chunk in enumerate(chunks, 1):
            if not chunk.get("_evidence_eligible", True):
                continue
            start, end = chunk["line_start"], chunk["line_end"]
            section = f" · {chunk['section']}" if chunk.get("section") else ""
            lines.append(f"### K{offset + index:04d} · {file_alias} L{start}–{end}{section}")
            lines += [f"{n}| {raw[n - 1]}" for n in range(start, end + 1) if raw[n - 1].strip()]
            lines.append("")
        offset += len(chunks)

    contexts = prepared.get("identity_contexts") or []
    if contexts:
        lines += [
            "## EC-1 证据归属（仅用于 coreference_evidence，不能作普通 evidence）",
            "",
        ]
        for context in contexts:
            file_alias = alias.get(str(Path(str(context.get("source_file", ""))).resolve()).casefold(), "F?")
            lines.append(
                f"### {file_alias} L{context.get('line_start')}–{context.get('line_end')}"
                f" → 绑定证据正文 L{context.get('evidence_line_start')}–{context.get('evidence_line_end')}"
            )
            start = int(context.get("line_start") or 0)
            for n, text in enumerate(str(context.get("quote", "")).splitlines(), start):
                lines.append(f"{n}| {text}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------- inventories

def _units(prepared: dict) -> dict[str, dict]:
    return {str(u["unit_id"]): u for u in prepared.get("article_units", [])}


def normalize_inventory(prepared: dict, payload: dict) -> dict:
    """Fill the mechanical parts of a compact extraction pass.

    Structural rows, article_id and inventory IDs are filled; a claim equal to
    its quote may be omitted; a unit that has inventory rows but no explicit
    classification is factual by construction. Units with neither stay
    unclassified, so freeze still rejects a pass that skipped a unit.
    """
    result = copy.deepcopy(payload)
    result.setdefault("article_id", prepared.get("article_id"))
    units = _units(prepared)
    inventory = result.get("claim_inventory")
    claims_per_unit: dict[str, int] = {}
    raw_lines: list[str] | None = None
    for item in inventory if isinstance(inventory, list) else []:
        if not isinstance(item, dict):
            continue
        unit = units.get(str(item.get("unit_id", "")))
        if unit and not str(item.get("article_quote", "")).strip():
            # Omitted quote = the whole unit, taken verbatim from its raw article line.
            if raw_lines is None:
                article = Path(str(prepared.get("article_file", "")))
                raw_lines = read_text(article).splitlines() if prepared.get("article_file") and article.is_file() else []
            text = str(unit.get("text", ""))
            line = int(unit.get("line") or 0)
            raw = raw_lines[line - 1] if 0 < line <= len(raw_lines) else text
            if text not in raw:
                raise ValueError(f"{unit['unit_id']}: 该单元原文含行内格式，省略 article_quote 无法唯一对应原始行；请逐字写出 article_quote")
            item["article_quote"] = text
        if not str(item.get("claim", "")).strip() and item.get("article_quote"):
            item["claim"] = normalize_inline_markdown(str(item["article_quote"]))
        unit_id = str(item.get("unit_id", ""))
        claims_per_unit[unit_id] = claims_per_unit.get(unit_id, 0) + 1
    coverage = result.setdefault("coverage_review", {})
    rows = {str(r.get("unit_id")): r for r in coverage.get("unit_classifications") or [] if isinstance(r, dict)}
    for unit_id, unit in units.items():
        unit_type = unit.get("unit_type")
        if unit_type in STRUCTURAL_UNIT_TYPES:
            rows[unit_id] = dict(unit_id=unit_id, classification="non_factual",
                                 category=unit_type, reason=STRUCTURAL_REASON)
        elif unit_id not in rows and claims_per_unit.get(unit_id):
            rows[unit_id] = dict(unit_id=unit_id, classification="factual",
                                 reason=FACTUAL_REASON.format(count=claims_per_unit[unit_id]))
    order = {unit_id: n for n, unit_id in enumerate(units)}
    coverage["unit_classifications"] = sorted(rows.values(), key=lambda r: order.get(str(r.get("unit_id")), len(order)))
    if isinstance(inventory, list):
        used = {str(item.get("inventory_id")) for item in inventory if isinstance(item, dict) and item.get("inventory_id")}
        n = 0
        for item in inventory:
            if isinstance(item, dict) and not item.get("inventory_id"):
                while True:
                    n += 1
                    candidate = f"I{n:03d}"
                    if candidate not in used:
                        break
                item["inventory_id"] = candidate
                used.add(candidate)
    return result


def coverage_problems(prepared: dict, payload: dict) -> list[str]:
    """Apply the renderer's managed coverage rules to one extraction pass, before any judging.

    These are the same rejections render_article_audit raises at the end of a
    run; surfacing them at freeze time avoids redoing judgments after a late failure.
    """
    from render_article_audit import (  # local: the renderer is only needed for this check
        EVIDENCE_ABSENCE_REASON, NON_FACTUAL_CATEGORIES, is_question, looks_like_plain_heading,
    )
    if prepared.get("integration_mode") != "manage-article-knowledge-v0.6":
        return []
    article = Path(str(prepared.get("article_file", "")))
    source_lines = read_text(article).splitlines() if article.is_file() else []
    units = _units(prepared)
    problems = []
    for row in payload.get("coverage_review", {}).get("unit_classifications") or []:
        unit_id = str(row.get("unit_id", ""))
        unit = units.get(unit_id)
        if not unit or unit.get("unit_type") in STRUCTURAL_UNIT_TYPES:
            continue
        classification, reason = row.get("classification"), str(row.get("reason", "")).strip()
        if classification not in {"factual", "non_factual"} or len(reason) < 12:
            problems.append(f"{unit_id}: 需要 factual/non_factual 分类和至少12字符的具体理由")
            continue
        if classification != "non_factual":
            continue
        category = str(row.get("category", "")).strip()
        if category not in NON_FACTUAL_CATEGORIES:
            problems.append(f"{unit_id}: non_factual 类别 {category!r} 不在允许类别中"
                            f"（只能用 {'/'.join(NONFACTUAL_CATEGORY_ORDER)}；含可核验关系的单元应写主张）")
        elif EVIDENCE_ABSENCE_REASON.search(reason):
            problems.append(f"{unit_id}: 不能用缺少证据作为非主张理由")
        elif is_question(unit["text"]) and category != "question":
            problems.append(f"{unit_id}: 以问号结尾的单元必须用 question 类别")
        elif category == "question" and not is_question(unit["text"]):
            problems.append(f"{unit_id}: 不以问号结尾的单元不能用 question 类别")
        elif looks_like_plain_heading(unit, source_lines) and category != "heading":
            problems.append(f"{unit_id}: 孤立无标记的标题候选必须用 heading 类别")
    return problems


def _count(coverage: dict, total: int) -> dict:
    rows = coverage["unit_classifications"]
    factual = sum(r.get("classification") == "factual" for r in rows)
    coverage.update(reviewed_unit_count=total, factual_unit_count=factual, non_factual_unit_count=len(rows) - factual)
    return coverage


def _check_source(prepared: dict, frozen: dict) -> None:
    for key, default in (("article_id", None), ("article_version", ""), ("article_sha256", ""), ("knowledge_sha256", [])):
        if frozen.get(key, default) != prepared.get(key, default):
            raise ValueError(f"Frozen inventory {key} does not match prepared; use the frozen file built from this prepared, "
                             "and never re-prepare or refreeze under another name")


def _header(prepared: dict) -> dict:
    return {key: prepared[key] for key in (
        "project_id", "article_version", "handoff_contract_version", "article_sha256", "knowledge_sha256",
    ) if prepared.get(key) not in (None, "")}


def _rows(payloads: list) -> list[dict]:
    """Accept compact files as a list, or {"verdicts": [...]} / {"reviews": [...]}."""
    rows: list[dict] = []
    for payload in payloads:
        if isinstance(payload, dict):
            payload = payload.get("verdicts", payload.get("reviews", payload.get("claim_reviews")))
        if not isinstance(payload, list):
            raise ValueError("Compact file must be a JSON list or contain verdicts/reviews")
        rows.extend(payload)
    return rows


def _by_claim_id(rows: list[dict], expected: list[str], label: str) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    for row in rows:
        claim_id = str(row.get("claim_id", "")) if isinstance(row, dict) else ""
        if not claim_id or claim_id in mapping:
            raise ValueError(f"{label}: missing or duplicate claim_id {claim_id!r}")
        mapping[claim_id] = row
    missing = [c for c in expected if c not in mapping]
    extra = [c for c in mapping if c not in set(expected)]
    if missing or extra:
        raise ValueError(f"{label}: missing {missing or '-'}; unexpected {extra or '-'}")
    return mapping


def _knowledge_blocks(prepared: dict) -> tuple[dict, dict]:
    """Per knowledge file its lines and eligible chunks, plus the view's K-number -> (file, chunk).

    The numbering repeats build_view exactly, so a `source_file` copied from a view heading
    (`### K0002 · F1 L12–15`) resolves to the block the model was reading.
    """
    files: dict[str, dict] = {}
    blocks: dict[str, tuple[str, dict]] = {}
    offset = 0
    for path in prepared.get("knowledge_files", []):
        source = str(path)
        chunks = extract_knowledge_chunks(Path(source)) if Path(source).is_file() else []
        text = _knowledge_text(source)
        files[str(Path(source).resolve()).casefold()] = dict(
            path=source, lines=text.split("\n") if text is not None else None,
            chunks=[c for c in chunks if c.get("_evidence_eligible", True)])
        for index, chunk in enumerate(chunks, 1):
            if chunk.get("_evidence_eligible", True):
                blocks[f"K{offset + index:04d}"] = (source, chunk)
        offset += len(chunks)
    return files, blocks


def _quote_spans(lines: list[str], chunks: list[dict], quote: str) -> list[tuple[int, int]]:
    """Line spans of every occurrence of `quote` that lies inside one of `chunks` (as `check` locates)."""
    text, spans, position = "\n".join(lines), [], -1
    while quote:
        position = text.find(quote, position + 1)
        if position == -1:
            break
        start = text[:position].count("\n") + 1
        end = start + quote.count("\n")
        if any(c["line_start"] <= start and end <= c["line_end"] for c in chunks):
            spans.append((start, end))
    return spans


def _source_files(prepared: dict, items: list, label: str, *, supported: bool = False) -> list[dict]:
    """Resolve each evidence row's file and give it line numbers that `check` and `render` accept.

    `source_file` may be omitted (single knowledge file), a view alias `F1`, a view block `K0002`
    or the file path.  A supported claim's quote must lie inside allowed evidence (in its K block
    when one is named); other rows only need a span where the quote really is.  Before this,
    lines were filled only at `check` and only for a unique occurrence, and the failures surfaced
    at `render`, after the records could no longer change.
    """
    paths = [str(path) for path in prepared.get("knowledge_files", [])]
    files, blocks = _knowledge_blocks(prepared)
    output = []
    for item in items or []:
        if not isinstance(item, dict):
            raise ValueError(f"{label}: evidence rows must be objects")
        item = dict(item)
        source = str(item.get("source_file", "")).strip()
        block_id, scope = "", None
        if not source:
            if len(paths) != 1:
                raise ValueError(f"{label}: source_file is required with several knowledge files (write F1… or K0001…)")
            source = paths[0]
        elif source.upper().startswith("F") and source[1:].isdigit():
            index = int(source[1:]) - 1
            if not 0 <= index < len(paths):
                raise ValueError(f"{label}: unknown knowledge alias {source}")
            source = paths[index]
        elif source.upper().startswith("K") and source[1:].isdigit():
            block_id = source.upper()
            if block_id not in blocks:
                raise ValueError(f"{label}: {source} is not an evidence block of this article's view "
                                 "(use a K number from 允许证据正文, F1…, or omit source_file)")
            source, chunk = blocks[block_id]
            scope = [chunk]
        elif str(Path(source).resolve()).casefold() not in {str(Path(f).resolve()).casefold() for f in paths}:
            # Evidence from another article's attachment would score this article against facts its writer never had.
            raise ValueError(f"{label}: evidence cites {Path(source).name} from outside this article's knowledge files; "
                             "judge only against the allowed evidence in this article's view and re-judge the claim "
                             "(source_file may be omitted, F1… or a K number from the view)")
        quote = str(item.get("quote", ""))
        text = _knowledge_text(source)
        if quote and text is not None and quote not in text:
            raise ValueError(f"{label}: evidence quote is not in {Path(source).name} (copied from another article or "
                             f"paraphrased: {quote[:50]!r}); copy a contiguous span from this article's view and re-judge")
        item["source_file"] = source
        record = files.get(str(Path(source).resolve()).casefold())
        if quote and record and record["lines"] is not None:
            lines = record["lines"]
            stated = (item.get("line_start"), item.get("line_end"))
            allowed = _quote_spans(lines, scope or record["chunks"], quote)
            if scope and not allowed:
                raise ValueError(f"{label}: quote {quote[:50]!r} is not inside {block_id}; copy it from that block "
                                 "or name the block it comes from")
            if supported and not allowed:
                raise ValueError(f"{label}: supported evidence quote {quote[:50]!r} is outside the allowed evidence "
                                 f"blocks of {Path(source).name}; cite only 允许证据正文 lines (K blocks in the view)")
            anywhere = allowed or _quote_spans(lines, [dict(line_start=1, line_end=len(lines))], quote)
            if stated not in anywhere:
                item["line_start"], item["line_end"] = anywhere[0]
        output.append(item)
    return output


_KNOWLEDGE_TEXT: dict[str, str | None] = {}


def _knowledge_text(source: str) -> str | None:
    """Knowledge file text with normalized line breaks, as check reads it; None when the file is absent."""
    key = str(Path(source).resolve()).casefold()
    if key not in _KNOWLEDGE_TEXT:
        path = Path(source)
        _KNOWLEDGE_TEXT[key] = "\n".join(read_text(path).splitlines()) if path.is_file() else None
    return _KNOWLEDGE_TEXT[key]


# --------------------------------------------------------------- judgments

# A verdict may carry one short, claim-specific phrase instead of a full
# sentence; the assembler adds the fixed frame. The phrase is still the
# model's own per-claim content, so reason-diversity checks apply unchanged.
SHORT_REASON_FIELDS = {
    "entailed": ("support", "证据正文支持：{}"),
    "unknown": ("gap", "允许证据未覆盖：{}"),
    "contradicted": ("conflict", "允许证据明确冲突：{}"),
}
MIN_PHRASE_CHARS = 6
# More rows than this sharing one reason means a template was stamped on, not a judgment made.
MAX_SHARED_REASON = 3


def verdict_reason(claim_id: str, status: str, row: dict) -> str:
    field, frame = SHORT_REASON_FIELDS[status]
    stray = [f for f, _ in SHORT_REASON_FIELDS.values() if f != field and str(row.get(f, "")).strip()]
    if stray:
        raise ValueError(f"{claim_id}: {status} uses {field!r}, not {stray[0]!r}")
    reason, phrase = str(row.get("reason", "")).strip(), str(row.get(field, "")).strip()
    if reason and phrase:
        raise ValueError(f"{claim_id}: give either reason or {field}, not both")
    if phrase:
        if len(phrase) < MIN_PHRASE_CHARS:
            raise ValueError(f"{claim_id}: {field} must name the specific support or gap (at least {MIN_PHRASE_CHARS} characters)")
        return frame.format(phrase)
    if len(reason) < 12:
        raise ValueError(f"{claim_id}: reason needs at least 12 characters, or give a short {field}")
    return reason

def reason_skeleton(text: str, claim_id: str, own_texts: list[str]) -> str:
    """The reason with its claim's own id, claim and quote cut out, so a template such as
    "could not confirm claim C00X: <claim>" groups as one reason (P-005 v4, 2026-10-26)."""
    skeleton = " ".join(str(text).split())
    for own in sorted({" ".join(str(t).split()) for t in own_texts if str(t).strip()}, key=len, reverse=True):
        skeleton = skeleton.replace(own, "")
    skeleton = skeleton.replace(claim_id, "")
    return " ".join(skeleton.strip(" ：:，,。.；;\"'“”「」《》()（）").split())


def reject_stamped_reasons(reasons: dict[str, str], label: str,
                           own_texts: dict[str, list[str]] | None = None) -> None:
    groups: dict[str, list[str]] = {}
    for claim_id, text in reasons.items():
        # A reason that only restates its claim has an empty skeleton and groups with the others like it.
        if str(text).strip():
            key = reason_skeleton(text, claim_id, (own_texts or {}).get(claim_id, []))
            groups.setdefault(key, []).append(claim_id)
    stamped = [(text, ids) for text, ids in groups.items() if len(ids) > MAX_SHARED_REASON]
    if stamped:
        lines = [f"{len(ids)} rows ({ids[0]}…{ids[-1]}) share {text[:40]!r}" for text, ids in stamped]
        raise ValueError(f"{label}: the same reason is stamped on more than {MAX_SHARED_REASON} claims; "
                         "judge each claim against the evidence and name its own support or gap:\n"
                         + "\n".join(lines))


# Same pronoun test as evidence_identity.validate_coreference.
COREFERENCE_PRONOUN = re.compile(r"\b(?:we|our|ours|this|these|they|their)\b", re.I)


def coreference_records(prepared: dict, claim: dict) -> list[dict]:
    """The EC-1 attribution records a claim's evidence calls for, built from prepared."""
    contexts = prepared.get("identity_contexts") or []
    refs: list[dict] = []
    for fact in claim.get("evidence", []):
        if not COREFERENCE_PRONOUN.search(str(fact.get("quote", ""))) or type(fact.get("line_start")) is not int:
            continue
        for context in contexts:
            same_file = (str(Path(str(context["source_file"])).resolve()).casefold()
                         == str(Path(str(fact.get("source_file", ""))).resolve()).casefold())
            if (same_file and context["evidence_line_start"] <= fact["line_start"]
                    <= fact["line_end"] <= context["evidence_line_end"]
                    and not any(r["line_start"] == context["line_start"] and r["source_file"] == context["source_file"]
                                for r in refs)):
                relation = str((context.get("fields") or {}).get("指代关系", "")).strip()
                refs.append(dict(source_file=context["source_file"], line_start=context["line_start"],
                                 line_end=context["line_end"], quote=context["quote"],
                                 reason=f"按EC-1归属解析证据中的指代：{relation}" if relation
                                 else "按当前30的EC-1归属板块解析证据中的指代"))
    return refs


def _attach_coreference(prepared: dict, claim: dict) -> None:
    """Fill EC-1 coreference_evidence from prepared and validate it now.

    Each record is the attribution block bound to an evidence row whose quote uses we/our/this…;
    its file, lines and four-line quote are fixed by the knowledge file, so the model never copies
    them.  Hand-copied records failed at render in every P-005 v4 attempt (2026-10-27): the block
    spans one line more than the lines it shows and its quote ends with a newline.
    """
    contexts = prepared.get("identity_contexts") or []
    refs = coreference_records(prepared, claim)
    if refs:
        claim["coreference_evidence"] = refs
    else:
        claim.pop("coreference_evidence", None)
    try:
        validate_coreference(claim, contexts)
    except ValueError as exc:
        raise ValueError(f"{claim['claim_id']}: {exc}") from exc


def assemble_judgments(prepared: dict, frozen: dict, first: dict, verdict_payloads: list,
                       blind: dict | None = None) -> dict:
    """Merge compact primary verdicts with frozen identities and first-pass coverage."""
    _check_source(prepared, frozen)
    units = _units(prepared)
    first = normalize_inventory(prepared, first)
    claim_ids = [str(c["claim_id"]) for c in frozen["claims"]]
    verdicts = _by_claim_id(_rows(verdict_payloads), claim_ids, "primary verdicts")
    claims = []
    for frozen_claim in frozen["claims"]:
        claim_id = str(frozen_claim["claim_id"])
        row = verdicts[claim_id]
        status = row.get("semantic_status")
        if status not in SEMANTIC_STATUSES:
            raise ValueError(f"{claim_id}: semantic_status must be entailed/unknown/contradicted")
        claim = {k: frozen_claim[k] for k in (
            "claim_id", "unit_id", "article_line", "article_quote", "claim",
            "derivation_note", "atomicity_note", "inventory_origin",
        ) if k in frozen_claim}
        deepeval_verdict, verdict = STATUS_FIELDS[status]
        claim.update(deepeval_verdict=deepeval_verdict, semantic_status=status, verdict=verdict,
                     reason=verdict_reason(claim_id, status, row),
                     evidence=_source_files(prepared, row.get("evidence"), claim_id,
                                            supported=status == "entailed"))
        _attach_coreference(prepared, claim)
        claims.append(claim)
    reject_stamped_reasons({c["claim_id"]: c["reason"] for c in claims}, "primary verdicts",
                           {c["claim_id"]: [c["claim"], c["article_quote"]] for c in claims})

    # The first pass's coverage becomes the judgments coverage; reconcile it
    # with the frozen union so classifications and claims agree per unit.
    claimed_units = {str(c["unit_id"]) for c in claims}
    first_rows = {str(r["unit_id"]): r for r in first["coverage_review"]["unit_classifications"]}
    blind_rows = {str(r.get("unit_id")): r for r in
                  normalize_inventory(prepared, blind or {})["coverage_review"]["unit_classifications"]}
    missing = [u for u in units if u not in first_rows]
    if missing:
        raise ValueError(f"First-pass coverage does not classify units: {', '.join(missing)}")
    for unit_id, row in first_rows.items():
        if unit_id in claimed_units and row.get("classification") != "factual":
            row.clear()
            row.update(unit_id=unit_id, classification="factual", reason=UNION_REASON)
        elif unit_id not in claimed_units and row.get("classification") == "factual":
            unit = units[unit_id]
            blind = blind_rows.get(unit_id) or {}
            if unit.get("unit_type") in STRUCTURAL_UNIT_TYPES:
                category, reason = unit["unit_type"], STRUCTURAL_REASON
            elif blind.get("classification") == "non_factual" and blind.get("category"):
                category = blind["category"]
                reason = "冻结分母中该单元没有保留主张；采用第二轮排除理由：" + str(blind.get("reason", ""))
            elif is_pure_question(unit.get("text")):
                category, reason = "question", "该单元是纯问题，冻结时已排除其中的问句主张，不计入分母。"
            else:
                raise ValueError(
                    f"{unit_id}: 第一轮标为可验证但冻结分母中没有主张；补充第一轮清单并重新冻结到新文件"
                )
            row.clear()
            row.update(unit_id=unit_id, classification="non_factual", category=category, reason=reason)
    coverage = first["coverage_review"]
    candidate_units = {str(u["unit_id"]) for u in prepared.get("article_units", []) if u.get("candidate_evidence")}
    reconsidered = list(dict.fromkeys(
        list(coverage.get("reconsidered_candidate_unit_ids") or [])
        + [u for u in units if u in candidate_units and u not in claimed_units]
    ))
    coverage["reconsidered_candidate_unit_ids"] = reconsidered
    _count(coverage, len(units))
    payload = dict(schema_version="1.1", evaluation_protocol_version="2.0", evaluation_mode=MODE,
                   primary_metric="strict_support", penalize_ambiguous_claims=False,
                   review_policy=DECISIVE_REVIEW_POLICY, article_id=prepared["article_id"])
    payload.update(_header(prepared))
    payload.update(coverage_review=coverage, claims=claims)
    return payload


# ------------------------------------------------------------ second review

def required_checks(claim_text: str) -> set[str]:
    """Checks the reconciler requires as covered for an entailed claim."""
    required = {"subject_object", "predicate_relation"}
    if SCOPE_RE.search(claim_text):
        required.add("scope_condition")
    if QUANTITY_RE.search(claim_text):
        required.add("quantity_time_version")
    if has_causal_effect_signal(claim_text):
        required.add("causal_effect")
    return required


def _entailed_row_gaps(claim_id: str, row: dict, required: set[str]) -> list[str]:
    """Fields an entailed second-judgment row must fill; the same minimums the reconciler applies.

    A gap here is a form problem the reviewer can fix now.  The reviewer's conclusions (split_required,
    a check marked missing) are not gaps: the reconciler turns them into unknown.  Before these were
    checked here, `check` listed them as records to repair after assembly, which nothing could do.
    """
    gaps = []
    if len(str(row.get("challenge_reason", "")).strip()) < 20:
        gaps.append(f"{claim_id}.challenge_reason（至少20字）")
    atomicity = row.get("atomicity_review")
    if not isinstance(atomicity, dict) or atomicity.get("status") not in ("atomic", "split_required"):
        gaps.append(f"{claim_id}.atomicity_review（status 为 atomic 或 split_required）")
    elif len(str(atomicity.get("reason", "")).strip()) < 12:
        gaps.append(f"{claim_id}.atomicity_review.reason（至少12字）")
    elif atomicity.get("status") == "atomic" and atomicity.get("independent_proposition_count") != 1:
        gaps.append(f"{claim_id}.atomicity_review（atomic 只能对应1个命题；多个命题写 split_required）")
    for key in CHECK_KEYS:
        check = row["entailment_checks"].get(key)
        if check is None:
            gaps.append(f"{claim_id}.{key}（主张措辞含此维度，须写出）" if key in required else f"{claim_id}.{key}")
        elif not isinstance(check, dict) or check.get("status") not in ("covered", "missing", "not_applicable"):
            gaps.append(f"{claim_id}.{key}.status（covered/missing/not_applicable）")
        elif len(str(check.get("reason", "")).strip()) < 8:
            gaps.append(f"{claim_id}.{key}.reason（至少8字）")
    return gaps


def _fill_mechanical_review_fields(row: dict, required: set[str]) -> None:
    """Fill what status and fragments already say, so a missing field cannot silently downgrade a claim.

    P-005 v4 (2026-10-27): reviewers wrote `count` for `independent_proposition_count` (read as
    "several propositions") and left per-check reasons out of covered checks whose verbatim
    fragments carry the alignment; the reconciler downgraded those supported claims to unknown.
    """
    atomicity = row.get("atomicity_review")
    if isinstance(atomicity, dict):
        if "independent_proposition_count" not in atomicity and isinstance(atomicity.get("count"), int):
            atomicity["independent_proposition_count"] = atomicity.pop("count")
        if atomicity.get("status") == "atomic":
            atomicity.setdefault("independent_proposition_count", 1)
    for key, check in row["entailment_checks"].items():
        if not isinstance(check, dict) or len(str(check.get("reason", "")).strip()) >= 8:
            continue
        status = check.get("status")
        if status == "covered" and check.get("claim_fragment") and check.get("evidence_fragment"):
            check["reason"] = f"主张片段“{check['claim_fragment']}”对应证据片段“{check['evidence_fragment']}”。"
        elif status == "not_applicable" and key not in required:
            # A dimension the claim's wording signals is ruled out only by the reviewer's own reason.
            check["reason"] = AUTO_CHECK_REASONS[1]
        elif status == "missing":
            check["reason"] = "复核判定允许证据未覆盖该维度。"


def _contradicted_row_gaps(claim_id: str, row: dict) -> list[str]:
    gaps = []
    if len(str(row.get("challenge_reason", "")).strip()) < 20:
        gaps.append(f"{claim_id}.challenge_reason（至少20字）")
    if not str(row.get("contradiction_evidence_fragment", "")).strip():
        gaps.append(f"{claim_id}.contradiction_evidence_fragment")
    alignment = row.get("contradiction_alignment")
    if not isinstance(alignment, dict) or set(alignment) != {"subject_object", "scope_condition", "time_version"}:
        gaps.append(f"{claim_id}.contradiction_alignment（subject_object/scope_condition/time_version）")
    return gaps


def assemble_review(prepared: dict, frozen: dict, blind: dict, recheck: dict, review_payloads: list) -> dict:
    """Merge compact blind second judgments with recheck identities and the blind pass."""
    _check_source(prepared, frozen)
    if recheck.get("article_id") != prepared.get("article_id"):
        raise ValueError("Recheck list article_id does not match prepared")
    units = _units(prepared)
    blind = normalize_inventory(prepared, blind)
    expected = [str(c["claim_id"]) for c in recheck.get("claims", [])]
    review_rows = _rows(review_payloads)
    all_supported = next((p.get("all_supported_challenge") for p in review_payloads
                          if isinstance(p, dict) and p.get("all_supported_challenge")), None)
    reviews = _by_claim_id(review_rows, expected, "second judgments")
    rows = []
    fragment_gaps: list[str] = []
    format_gaps: list[str] = []
    knowledge_texts = [text for text in (_knowledge_text(str(path)) for path in prepared.get("knowledge_files", []))
                       if text is not None]
    for identity in recheck.get("claims", []):
        claim_id = str(identity["claim_id"])
        source = reviews[claim_id]
        status = source.get("independent_semantic_status", source.get("semantic_status"))
        if status not in SEMANTIC_STATUSES:
            raise ValueError(f"{claim_id}: independent_semantic_status must be entailed/unknown/contradicted")
        row = {k: identity[k] for k in ("claim_id", "unit_id", "article_line", "article_quote", "claim")}
        row.update({k: copy.deepcopy(v) for k, v in source.items() if k not in row})
        row.update(independent_semantic_status=status, semantic_status=status)
        if "evidence" in row:
            row["evidence"] = _source_files(prepared, row["evidence"], claim_id)
        if status == "entailed":
            checks = row.setdefault("entailment_checks", {})
            required = required_checks(str(identity["claim"]))
            for key in CHECK_KEYS:
                if key not in checks and key not in required:
                    checks[key] = dict(status="not_applicable", claim_fragment="", evidence_fragment="",
                                       reason=AUTO_NOT_APPLICABLE)
                elif isinstance(checks.get(key), dict):
                    checks[key].setdefault("claim_fragment", "")
                    checks[key].setdefault("evidence_fragment", "")
            row["entailment_checks"] = {k: checks[k] for k in CHECK_KEYS if k in checks}
            _fill_mechanical_review_fields(row, required)
            # The reconciler downgrades a covered check without exact fragments; say so while the
            # reviewer can still fix the row (P-005 v4 001 lost supported claims to this).
            for key, check in row["entailment_checks"].items():
                if not isinstance(check, dict) or check.get("status") != "covered":
                    continue
                claim_fragment = str(check.get("claim_fragment") or "").strip()
                evidence_fragment = str(check.get("evidence_fragment") or "").strip()
                if not claim_fragment or claim_fragment not in str(identity["claim"]):
                    fragment_gaps.append(f"{claim_id}.{key}.claim_fragment")
                if not evidence_fragment or (knowledge_texts and not any(
                        evidence_fragment in text for text in knowledge_texts)):
                    fragment_gaps.append(f"{claim_id}.{key}.evidence_fragment")
            covered = [c.get("evidence_fragment") for c in row["entailment_checks"].values()
                       if isinstance(c, dict) and c.get("status") == "covered" and c.get("evidence_fragment")]
            row.setdefault("evidence_fragment", covered[0] if covered else "")
            format_gaps += _entailed_row_gaps(claim_id, row, required)
        elif status == "contradicted":
            row.setdefault("evidence_fragment", row.get("contradiction_evidence_fragment", ""))
            format_gaps += _contradicted_row_gaps(claim_id, row)
        row.setdefault("claim_fragment", identity["claim"])
        row.setdefault("evidence_fragment", "")
        rows.append(row)
    if fragment_gaps or format_gaps:
        raise ValueError(
            "second judgments: nothing was written; fix these rows in the review files and run assemble-review again"
            + ("\n- covered checks need a claim_fragment copied from the claim text and an evidence_fragment copied "
               "verbatim from this article's knowledge file: " + "、".join(fragment_gaps) if fragment_gaps else "")
            + ("\n- incomplete rows: " + "、".join(format_gaps) if format_gaps else ""))
    reject_stamped_reasons({r["claim_id"]: r.get("challenge_reason", "") for r in rows}, "second judgments",
                           {str(c["claim_id"]): [str(c.get("claim", "")), str(c.get("article_quote", ""))]
                            for c in frozen["claims"]})

    by_ref = {ref: str(c["claim_id"]) for c in frozen["claims"] for ref in c.get("source_refs", [])}
    replaced = {str(r.get("source_ref")) for r in frozen.get("replacements") or []}
    inventory, replaced_ids = [], []
    for item in blind.get("claim_inventory") or []:
        ref = f"blind:{item.get('inventory_id')}"
        if ref in replaced:
            replaced_ids.append(str(item["inventory_id"]))  # covered by its frozen replacement propositions
            continue
        item = dict(item)
        unit = units.get(str(item.get("unit_id")))
        if unit and item.get("article_line") in (None, ""):
            item["article_line"] = unit.get("line")
        item["matched_claim_id"] = by_ref.get(ref, "")
        inventory.append(item)
    payload = dict(schema_version="1.0", evaluation_protocol_version="2.0", article_id=prepared["article_id"],
                   review_mode="independent_adversarial", review_policy=DECISIVE_REVIEW_POLICY)
    payload.update(_header(prepared))
    payload.update(coverage_review=_count(blind["coverage_review"], len(units)),
                   claim_inventory=inventory, claim_reviews=rows)
    if replaced_ids:
        payload["replaced_inventory_ids"] = replaced_ids
    if all_supported:
        payload["all_supported_challenge"] = all_supported
    return payload
