"""Prepare article and knowledge files for a Codex Faithfulness audit.

This script is deterministic. It does not make semantic judgments.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path


WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)*(?:[-–—/][A-Za-z0-9]+(?:['’][A-Za-z0-9]+)*)*%?")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=(?:[A-Z0-9\[]|[\"'“‘]))")
STOP_WORDS = set(
    "a an and are as at be been by can could for from had has have if in into is it its may might "
    "of on or should than that the their then there these this those to was were when which will with would".split()
)
ADMIN_END_HEADINGS = (
    "codex自动闭环结果",
    "codex 自动闭环结果",
    "自动审核结果",
    "引用率统计",
)


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


def extract_article(path: Path) -> dict:
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

    article_lines: list[dict] = []
    units: list[dict] = []
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
            # Titles and headings are intentionally excluded from the denominator.
            continue
        text = visible_body_line(raw)
        if not text or not WORD_RE.search(text):
            continue
        line_no = index + 1
        article_lines.append({"line": line_no, "text": text})
        for part in SENTENCE_RE.split(text):
            part = part.strip()
            if len(WORD_RE.findall(part)) < 3:
                continue
            units.append({"unit_id": f"U{len(units) + 1:03d}", "line": line_no, "text": part})

    if not article_lines:
        raise ValueError(f"No article body found in {path}")
    return {"title": title, "article_lines": article_lines, "article_units": units}


def source_scope(lines: list[str], path: Path) -> list[bool]:
    """Select source-evidence sections from writing-input files when identifiable."""
    writing_input = "写作输入" in path.name
    if not writing_input:
        return [True] * len(lines)

    has_source_blocks = any(re.match(r"^\s*#{3,5}\s+来源块[：:]", line) for line in lines)
    if not has_source_blocks:
        return [True] * len(lines)

    selected = [False] * len(lines)
    in_source_block = False
    in_evidence_body = False
    source_level = 7
    for index, raw in enumerate(lines):
        heading = markdown_heading(raw)
        if heading:
            level, name = heading
            if re.match(r"^来源块[：:]", name):
                in_source_block = True
                in_evidence_body = False
                source_level = level
                continue
            if in_source_block and level <= source_level:
                in_source_block = False
                in_evidence_body = False
            if in_source_block:
                key = name.replace(" ", "")
                if "完整正文" in key or "原文" in key or "表格" in key:
                    in_evidence_body = True
                elif level >= source_level + 1:
                    in_evidence_body = False
                continue
        if in_source_block and in_evidence_body:
            selected[index] = True
    return selected


def extract_knowledge(path: Path) -> list[dict]:
    lines = read_text(path).splitlines()
    allowed = source_scope(lines, path)
    chunks: list[dict] = []
    section = ""
    buffer: list[str] = []
    start_line = 0
    end_line = 0

    def flush() -> None:
        nonlocal buffer, start_line, end_line
        text = " ".join(buffer).strip()
        if text and WORD_RE.search(text):
            chunks.append(
                {
                    "chunk_id": "",
                    "source_file": str(path.resolve()),
                    "section": section,
                    "line_start": start_line,
                    "line_end": end_line,
                    "text": text,
                }
            )
        buffer = []
        start_line = 0
        end_line = 0

    for index, raw in enumerate(lines):
        line_no = index + 1
        heading = markdown_heading(raw)
        if heading:
            flush()
            section = heading[1]
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
        buffer.append(text)
        end_line = line_no
        if len(" ".join(buffer)) >= 900 or ("|" in raw and raw.strip().startswith("|")):
            flush()
    flush()

    for index, chunk in enumerate(chunks, 1):
        chunk["chunk_id"] = f"K{index:04d}"
    return chunks


def terms(text: str) -> set[str]:
    return {word.lower() for word in WORD_RE.findall(text) if word.lower() not in STOP_WORDS}


def attach_candidates(units: list[dict], chunks: list[dict], limit: int) -> None:
    chunk_terms = [terms(chunk["text"]) for chunk in chunks]
    frequency: Counter[str] = Counter()
    for term_set in chunk_terms:
        frequency.update(term_set)
    total = max(1, len(chunks))

    for unit in units:
        unit_terms = terms(unit["text"])
        scored: list[tuple[float, int]] = []
        for index, source_terms in enumerate(chunk_terms):
            overlap = unit_terms & source_terms
            if not overlap:
                continue
            score = sum(math.log((total + 1) / (frequency[word] + 1)) + 1 for word in overlap)
            score /= math.sqrt(max(1, len(unit_terms)))
            scored.append((score, index))
        unit["candidate_evidence"] = [
            {
                "chunk_id": chunks[index]["chunk_id"],
                "retrieval_score": round(score, 4),
                "source_file": chunks[index]["source_file"],
                "line_start": chunks[index]["line_start"],
                "line_end": chunks[index]["line_end"],
                "text": chunks[index]["text"],
            }
            for score, index in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]
        ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--article", type=Path, required=True)
    parser.add_argument("--knowledge", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--article-id")
    parser.add_argument("--candidate-limit", type=int, default=6)
    args = parser.parse_args()

    if not args.article.is_file():
        raise SystemExit(f"Article file not found: {args.article}")
    missing = [path for path in args.knowledge if not path.is_file()]
    if missing:
        raise SystemExit(f"Knowledge file not found: {missing[0]}")

    article = extract_article(args.article)
    chunks: list[dict] = []
    for knowledge_path in args.knowledge:
        file_chunks = extract_knowledge(knowledge_path)
        offset = len(chunks)
        for index, chunk in enumerate(file_chunks, 1):
            chunk["chunk_id"] = f"K{offset + index:04d}"
        chunks.extend(file_chunks)
    if not chunks:
        raise SystemExit("No usable knowledge context found")

    attach_candidates(article["article_units"], chunks, max(1, args.candidate_limit))
    article_id = args.article_id
    if not article_id:
        match = re.search(r"(?:ART|Article)[-_ ]?\d+", args.article.stem, re.IGNORECASE)
        article_id = match.group(0).upper().replace("_", "-").replace(" ", "-") if match else args.article.stem

    payload = {
        "schema_version": "1.0",
        "article_id": article_id,
        "article_title": article["title"],
        "article_file": str(args.article.resolve()),
        "knowledge_files": [str(path.resolve()) for path in args.knowledge],
        "article_lines": article["article_lines"],
        "article_units": article["article_units"],
        "knowledge_chunks": chunks,
        "rules": {
            "headings_excluded": True,
            "denominator": "all atomic factual claims in article body",
            "numerator": "claims supported by supplied knowledge context",
            "score": "supported / total factual claims",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "article_id": article_id,
                "article_units": len(article["article_units"]),
                "knowledge_chunks": len(chunks),
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
