"""Shared deterministic parsing for article Faithfulness artifacts."""

from __future__ import annotations

import re
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


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Cannot decode text file: {path}")


def normalize_inline_markdown(text: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
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


def is_table_rule(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*", line))


def visible_body_line(line: str) -> str:
    if is_table_rule(line):
        return ""
    if "|" in line and line.strip().startswith("|"):
        cells = [normalize_inline_markdown(cell) for cell in line.strip().strip("|").split("|")]
        return " | ".join(cell for cell in cells if cell)
    return normalize_inline_markdown(line)


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


def extract_article(path: Path) -> dict[str, Any]:
    lines = read_text(path).splitlines()
    title = path.stem
    start = 0
    for index, raw in enumerate(lines):
        heading = markdown_heading(raw)
        if heading and heading[0] == 1 and title == path.stem:
            title = heading[1]
        if heading and "最终正文" in heading[1]:
            start = index + 1
            break

    article_lines: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    body_title_found = False
    for index in range(start, len(lines)):
        raw = lines[index]
        heading = markdown_heading(raw)
        if heading:
            lowered = heading[1].lower().replace(" ", "")
            if any(marker in lowered for marker in ADMIN_END_HEADINGS):
                break
            if heading[0] == 1 and not body_title_found:
                title = heading[1]
                body_title_found = True
            continue
        text = visible_body_line(raw)
        if not text or not CONTENT_RE.search(text):
            continue
        line_no = index + 1
        article_lines.append({"line": line_no, "text": text})
        for part in split_units(text):
            units.append({"unit_id": f"U{len(units) + 1:03d}", "line": line_no, "text": part})

    if not article_lines:
        raise ValueError(f"No article body found in {path}")
    return {
        "title": title,
        "metadata_article_id": metadata_article_id(lines),
        "article_lines": article_lines,
        "article_units": units,
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
