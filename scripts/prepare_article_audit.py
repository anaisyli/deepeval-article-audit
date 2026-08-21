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

from audit_common import (
    TERM_RE,
    extract_article,
    extract_knowledge_chunks,
    is_v05_writing_material,
    public_chunk,
    read_text,
)


STOP_WORDS = set(
    "a an and are as at be been by can could for from had has have if in into is it its may might "
    "of on or should than that the their then there these this those to was were when which will with would".split()
)


def terms(text: str) -> set[str]:
    return {word.lower() for word in TERM_RE.findall(text) if word.lower() not in STOP_WORDS}


def attach_candidates(units: list[dict], chunks: list[dict], limit: int) -> None:
    eligible_indexes = [index for index, chunk in enumerate(chunks) if chunk.get("_evidence_eligible", True)]
    chunk_terms = {index: terms(chunks[index]["text"]) for index in eligible_indexes}
    frequency: Counter[str] = Counter()
    for term_set in chunk_terms.values():
        frequency.update(term_set)
    total = max(1, len(eligible_indexes))

    for unit in units:
        unit_terms = terms(unit["text"])
        scored: list[tuple[float, int]] = []
        for index in eligible_indexes:
            source_terms = chunk_terms[index]
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


def resolve_article_id(article: dict, article_path: Path, explicit_id: str | None) -> str:
    metadata_id = article.get("metadata_article_id")
    if explicit_id and metadata_id and explicit_id != metadata_id:
        raise ValueError(
            f"--article-id {explicit_id!r} conflicts with article metadata ID {metadata_id!r}"
        )
    if explicit_id:
        return explicit_id
    if metadata_id:
        return metadata_id
    match = re.search(r"(?:ART|Article)[-_ ]?\d+", article_path.stem, re.IGNORECASE)
    if match:
        return match.group(0).upper().replace("_", "-").replace(" ", "-")
    return article_path.stem


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
    v05_inputs = [
        path for path in args.knowledge
        if is_v05_writing_material(read_text(path).splitlines(), path)
    ]
    if v05_inputs and (len(args.knowledge) != 1 or len(v05_inputs) != 1):
        raise SystemExit(
            "manage-article-knowledge v0.5 requires exactly one factual input: 30_本篇知识库资料.md"
        )

    article = extract_article(args.article)
    chunks: list[dict] = []
    for knowledge_path in args.knowledge:
        file_chunks = extract_knowledge_chunks(knowledge_path)
        offset = len(chunks)
        for index, chunk in enumerate(file_chunks, 1):
            chunk["chunk_id"] = f"K{offset + index:04d}"
        chunks.extend(file_chunks)
    if not chunks:
        raise SystemExit("No usable knowledge context found")

    attach_candidates(article["article_units"], chunks, max(1, args.candidate_limit))
    try:
        article_id = resolve_article_id(article, args.article, args.article_id)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    payload = {
        "schema_version": "1.0",
        "article_id": article_id,
        "article_title": article["title"],
        "article_file": str(args.article.resolve()),
        "knowledge_files": [str(path.resolve()) for path in args.knowledge],
        "article_lines": article["article_lines"],
        "article_units": article["article_units"],
        "knowledge_chunks": [public_chunk(chunk) for chunk in chunks],
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
