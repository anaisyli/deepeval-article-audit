"""Shared deterministic parsing for article Faithfulness artifacts."""

from __future__ import annotations

import re
import hashlib
from pathlib import Path
from typing import Any


CONTENT_RE = re.compile(r"[A-Za-z0-9\u3400-\u9fff]")
TERM_RE = re.compile(
    r"[A-Za-z0-9\u3400-\u9fff]+(?:['’][A-Za-z0-9]+)*(?:[-–—/][A-Za-z0-9]+(?:['’][A-Za-z0-9]+)*)*%?"
)
SENTENCE_RE = re.compile(
    r"(?<=[。！？])|(?<=[.!?])\s+(?=(?:[A-Z0-9\[\"'“‘（(]|[\u3400-\u9fff]))"
)
ARTICLE_ID_RE = re.compile(
    r"^\s*[-*]\s*(?:文章ID|Article\s*ID)[：:]\s*(.*?)\s*$",
    re.IGNORECASE,
)
ADMIN_END_HEADINGS = (
    "codex自动闭环结果",
    "codex 自动闭环结果",
    "自动审核结果",
    "引用率统计",
)
V05_KNOWLEDGE_NAME = "30_本篇知识库资料.md"
ARTICLE_BODY_START = "<!-- ARTICLE_BODY_START -->"
ARTICLE_BODY_END = "<!-- ARTICLE_BODY_END -->"
STRUCTURAL_UNIT_TYPES = {"title", "heading", "table_header"}
DOCX_HEADING_STYLE_RE = re.compile(r"<!--\s*MAK_DOCX_HEADING_STYLE:([^>]+?)\s*-->", re.IGNORECASE)
EFFECT_SIGNAL_RE = re.compile(
    r"\b(?:because|therefore|so that|prevents?|helps?|improves?|ensures?|allows?|"
    r"enables?|leads? to|results? in|causes?|changes?|makes?|keeps?|guides?|matches?)\b|"
    r"(?:因为|所以|因此|防止|避免|帮助|改善|确保|使得|导致|造成|改变|指导|匹配)",
    re.IGNORECASE,
)
METHOD_SIGNAL_RE = re.compile(
    r"(?:^|\|\s*)(?:use|record|keep|compare|mark|define|check|confirm|document|measure|verify|"
    r"test|select|choose|avoid|ensure|show|include|exclude|leave)\b|"
    r"(?:^|\|\s*)(?:使用|记录|保留|比较|标记|定义|检查|确认|核验|测试|选择|避免|确保|展示|纳入|排除)",
    re.IGNORECASE,
)
COMPARISON_SIGNAL_RE = re.compile(
    r"\b(?:more|less|better|worse|higher|lower|different|same|than|versus|vs\.?)\b|"
    r"(?:更多|更少|更好|更差|更高|更低|不同|相同|相比|对比)",
    re.IGNORECASE,
)
CONDITION_SIGNAL_RE = re.compile(
    r"\b(?:if|when|unless|only when|subject to|after|before|during)\b|"
    r"(?:如果|当|除非|仅当|取决于|之后|之前|期间)",
    re.IGNORECASE,
)


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Cannot decode text file: {path}")


def normalize_inline_markdown(text: str) -> str:
    # Image alt text describes an asset; it is not article prose to audit.
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"!\[[^\]]*\](?!\()", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("`", "").replace("**", "").replace("__", "")
    text = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|>\s*)", "", text)
    return " ".join(text.split())


def markdown_heading(line: str) -> tuple[int, str] | None:
    match = re.match(r"^\s*(#{1,6})\s+(.+?)\s*$", line)
    if not match:
        return None
    return len(match.group(1)), normalize_inline_markdown(match.group(2))


def heading_source(line: str) -> tuple[str, str]:
    match = DOCX_HEADING_STYLE_RE.search(line)
    if match:
        return "docx_style", match.group(1).strip()
    return "markdown", ""


def is_table_rule(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*", line))


def is_table_row(line: str) -> bool:
    return line.strip().startswith("|") and line.count("|") >= 2


def table_column_count(line: str) -> int:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|") and not value.endswith("\\|"):
        value = value[:-1]
    return len(re.split(r"(?<!\\)\|", value)) if value else 0


def visible_body_line(line: str) -> str:
    if is_table_rule(line):
        return ""
    if "|" in line and line.strip().startswith("|"):
        cells = [normalize_inline_markdown(cell) for cell in line.strip().strip("|").split("|")]
        return " | ".join(cell for cell in cells if cell)
    return normalize_inline_markdown(line)


def article_content_sha256(article_lines: list[dict[str, Any]]) -> str:
    """Hash the parsed article content, excluding Markdown serialization details."""
    payload = "\n".join(str(item.get("text", "")) for item in article_lines)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def metadata_article_id(lines: list[str]) -> str | None:
    for raw in lines:
        match = ARTICLE_ID_RE.match(raw)
        if match and match.group(1).strip():
            return match.group(1).strip()
    return None


def parse_field(lines: list[str], label: str) -> str:
    pattern = re.compile(rf"^\s*[-*]\s*{re.escape(label)}[：:]\s*(.*?)\s*$")
    for raw in lines:
        match = pattern.match(raw)
        if match:
            return match.group(1).strip()
    return ""


def is_v05_writing_material(lines: list[str], path: Path) -> bool:
    return path.name == V05_KNOWLEDGE_NAME and parse_field(lines, "资料视图") == "写作素材包"


def split_units(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_RE.split(text) if CONTENT_RE.search(part)]


def claim_review_signals(text: str, source_kind: str) -> list[str]:
    """Return conservative review hints; signals are not final classifications."""
    signals: list[str] = []
    if source_kind == "table_data_row":
        signals.append("table_data_row")
    if EFFECT_SIGNAL_RE.search(text):
        signals.append("effect_or_causal_relation")
    if METHOD_SIGNAL_RE.search(text):
        signals.append("operational_method")
    if COMPARISON_SIGNAL_RE.search(text):
        signals.append("comparison")
    if CONDITION_SIGNAL_RE.search(text):
        signals.append("condition_or_scope")
    return signals


def extract_article(path: Path) -> dict[str, Any]:
    lines = read_text(path).splitlines()
    title = path.stem
    metadata_title = (
        parse_field(lines, "文章标题") or parse_field(lines, "最终标题")
        or parse_field(lines, "Article Title") or parse_field(lines, "Final Title")
    )
    if metadata_title:
        title = metadata_title
    start = 0
    end = len(lines)
    body_heading = ""
    start_markers = [index for index, raw in enumerate(lines) if raw.strip() == ARTICLE_BODY_START]
    end_markers = [index for index, raw in enumerate(lines) if raw.strip() == ARTICLE_BODY_END]
    if start_markers or end_markers:
        if len(start_markers) != 1 or len(end_markers) != 1:
            raise ValueError(f"Managed article must contain exactly one body marker pair: {path}")
        if start_markers[0] >= end_markers[0]:
            raise ValueError(f"Managed article body markers are out of order: {path}")
        start = start_markers[0] + 1
        end = end_markers[0]
        body_heading = "显式正文边界"
    elif path.name == "40_最终文章.md" and parse_field(lines, "模板版本"):
        raise ValueError(
            f"Managed 40 is missing explicit body markers: {path}. "
            "Regenerate it through the current writing bridge before auditing."
        )
    for index, raw in enumerate(lines[:start if body_heading else len(lines)]):
        heading = markdown_heading(raw)
        if heading and heading[0] == 1 and not metadata_title and title == path.stem:
            title = heading[1]
        if not body_heading and heading and heading[1] in {"最终正文", "正文"}:
            start = index + 1
            body_heading = heading[1]
            break

    # Older v0.5/v0.6 articles may omit the explicit body heading. Once article
    # metadata is present, skip it and the title heading instead of auditing the
    # administrative fields as article content.
    if not body_heading and metadata_title:
        metadata_end = -1
        for index, raw in enumerate(lines):
            if re.match(
                r"^\s*[-*]\s*(?:文章ID|文章版本|文章标题|最终标题|完成日期|模板版本|"
                r"Article\s*ID|Article\s*Version|Article\s*Title|Final\s*Title|Completed\s*Date|"
                r"Target\s*Language|Keywords?)[：:]",
                raw,
                re.I,
            ):
                metadata_end = index
        for index in range(metadata_end + 1, len(lines)):
            heading = markdown_heading(lines[index])
            if heading and heading[0] == 1:
                start = index + 1
                body_heading = heading[1]
                break

    article_lines: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    body_title_found = bool(metadata_title)
    first_visible_body_line = True
    for index in range(start, end):
        raw = lines[index]
        heading = markdown_heading(raw)
        if heading:
            lowered = heading[1].lower().replace(" ", "")
            if any(marker in lowered for marker in ADMIN_END_HEADINGS):
                break
            if heading[0] == 1 and not body_title_found:
                title = heading[1]
                body_title_found = True
            source, source_style = heading_source(raw)
            normalized_title = normalize_inline_markdown(metadata_title or title).casefold()
            unit_type = (
                "title"
                if heading[0] == 1 and heading[1].casefold() == normalized_title
                else "heading"
            )
            units.append({
                "unit_id": f"U{len(units) + 1:03d}",
                "line": index + 1,
                "text": heading[1],
                "source_kind": "heading",
                "unit_type": unit_type,
                "heading_level": heading[0],
                "heading_source": source,
                "heading_style": source_style,
                "claim_review_signals": [],
            })
            continue
        text = visible_body_line(raw)
        if not text or not CONTENT_RE.search(text):
            continue
        if first_visible_body_line and metadata_title and text == normalize_inline_markdown(metadata_title):
            # Some managed bridges preserve the title as a plain line inside the
            # body markers.  It remains a title, not an auditable factual unit.
            first_visible_body_line = False
            continue
        first_visible_body_line = False
        line_no = index + 1
        article_lines.append({"line": line_no, "text": text})
        is_table = is_table_row(raw)
        source_kind = "prose"
        if is_table:
            next_is_rule = index + 1 < end and is_table_rule(lines[index + 1])
            legacy_header = (
                not next_is_rule
                and index + 1 < end
                and is_table_row(lines[index + 1])
                and not is_table_rule(lines[index + 1])
                and (index == start or not is_table_row(lines[index - 1]))
                and table_column_count(raw) >= 2
                and table_column_count(raw) == table_column_count(lines[index + 1])
            )
            source_kind = (
                "table_header"
                if next_is_rule or legacy_header
                else "table_data_row"
            )
        for part in split_units(text):
            unit_type = "table_header" if source_kind == "table_header" else "content"
            units.append({
                "unit_id": f"U{len(units) + 1:03d}",
                "line": line_no,
                "text": part,
                "source_kind": source_kind,
                "unit_type": unit_type,
                "claim_review_signals": claim_review_signals(part, source_kind),
            })

    if not article_lines:
        raise ValueError(f"No article body found in {path}")
    return {
        "title": title,
        "metadata_article_id": metadata_article_id(lines),
        "metadata_article_version": parse_field(lines, "文章版本"),
        "article_lines": article_lines,
        "article_units": units,
        "body_heading": body_heading,
        "body_line_start": start + 1,
        "body_line_end": end,
    }


def source_scope(lines: list[str], path: Path) -> list[bool]:
    """Match the manage-article-knowledge v0.5 importer's chunk scope."""
    if is_v05_writing_material(lines, path):
        selected = [False] * len(lines)
        evidence_sections = {
            "一、可直接用于正文的事实",
            "二、可直接采用的英文表达",
            "三、可使用的数据表",
        }
        in_evidence_section = False
        evidence_level: int | None = None
        found_evidence = False
        for index, line in enumerate(lines):
            heading = markdown_heading(line)
            if heading:
                level, title = heading
                if level == 2:
                    in_evidence_section = title in evidence_sections
                    evidence_level = None
                elif evidence_level is not None and level <= evidence_level:
                    evidence_level = None
                if in_evidence_section and re.match(r"^证据正文(?:（供Faithfulness核验）)?$", title):
                    evidence_level = level
                    found_evidence = True
                continue
            if evidence_level is not None:
                selected[index] = True
        if not found_evidence:
            raise ValueError(
                f"Writing material has no recognizable evidence bodies: {path}. "
                "Use a '证据正文（供Faithfulness核验）' heading in sections 1-3."
            )
        return selected

    writing_input = "写作输入" in path.name
    if not writing_input:
        return [True] * len(lines)
    has_source_blocks = any(re.match(r"^\s*#{3,5}\s+来源块[：:]", line) for line in lines)
    if not has_source_blocks:
        return [True] * len(lines)

    selected = [False] * len(lines)
    in_source_block = False
    in_evidence_body = False
    for index, line in enumerate(lines):
        heading = markdown_heading(line)
        if heading:
            level, title = heading
            if re.match(r"^来源块[：:]", title):
                in_source_block = True
                in_evidence_body = False
            elif in_source_block and level <= 4:
                in_source_block = False
                in_evidence_body = False
            elif in_source_block and any(marker in title for marker in ("原文", "证据", "来源正文")):
                in_evidence_body = True
            continue
        if in_source_block and in_evidence_body:
            selected[index] = True
    return selected


def extract_knowledge_chunks(path: Path) -> list[dict[str, Any]]:
    lines = read_text(path).splitlines()
    allowed = source_scope(lines, path)
    is_v05_input = is_v05_writing_material(lines, path)
    chunks: list[dict[str, Any]] = []
    section = ""
    buffer: list[str] = []
    start_line = 0
    end_line = 0
    buffer_eligible = not is_v05_input
    v05_content_started = False

    def flush() -> None:
        nonlocal buffer, start_line, end_line, buffer_eligible
        text = " ".join(buffer).strip()
        if text and CONTENT_RE.search(text):
            chunks.append(
                {
                    "source_file": str(path.resolve()),
                    "section": section,
                    "line_start": start_line,
                    "line_end": end_line,
                    "text": text,
                    "_evidence_eligible": buffer_eligible,
                }
            )
        buffer = []
        start_line = 0
        end_line = 0
        buffer_eligible = not is_v05_input

    for index, raw in enumerate(lines):
        line_no = index + 1
        heading = markdown_heading(raw)
        if heading:
            flush()
            level, section = heading
            if is_v05_input and level == 2:
                v05_content_started = True
            continue
        if not allowed[index]:
            flush()
            continue
        text = visible_body_line(raw)
        if not text:
            flush()
            continue
        if not buffer:
            start_line = line_no
            buffer_eligible = not is_v05_input or v05_content_started
        buffer.append(text)
        end_line = line_no
        if len(" ".join(buffer)) >= 900 or ("|" in raw and raw.strip().startswith("|")):
            flush()
    flush()
    return chunks


def public_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in chunk.items() if not key.startswith("_")}


def normalized_path(path: str | Path) -> str:
    return str(Path(path).resolve()).casefold()
