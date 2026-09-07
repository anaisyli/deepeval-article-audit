"""Prepare article and knowledge files for a Codex Faithfulness audit.

This script is deterministic. It does not make semantic judgments.
"""

from __future__ import annotations

import argparse
import hashlib
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
    parse_field,
    public_chunk,
    read_text,
)
from manage_handoff_contract import ManagedHandoffError, load_contract, validate_event


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact_stem(article_id: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", article_id).strip(" .-")
    return cleaned or "article"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--article", type=Path, required=True)
    parser.add_argument("--knowledge", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--result-dir",
        type=Path,
        help="v0.6受管模式：将prepared写入该文章版本专属目录。",
    )
    parser.add_argument("--article-id")
    parser.add_argument(
        "--article-version",
        help="v0.6受管模式必填，并必须与40/30中的文章版本一致。",
    )
    parser.add_argument("--project-id", help="v0.6受管模式必填的知识库项目ID。")
    parser.add_argument(
        "--handoff-contract-version",
        help="v0.6受管模式必填；必须与当前安装的知识库合同兼容。",
    )
    parser.add_argument(
        "--manage-skill-root",
        type=Path,
        help="仅在自动发现失败时显式指定已安装的manage-article-knowledge Skill根目录。",
    )
    parser.add_argument("--candidate-limit", type=int, default=6)
    args = parser.parse_args()

    if args.output and args.result_dir:
        raise SystemExit("Do not combine --output with --result-dir")
    if not args.output and not args.result_dir:
        raise SystemExit("Provide --output or --result-dir")
    if args.result_dir and not args.article_version:
        raise SystemExit("--article-version is required with --result-dir")
    if args.result_dir and not args.project_id:
        raise SystemExit("--project-id is required with --result-dir")
    if args.result_dir and not args.handoff_contract_version:
        raise SystemExit("--handoff-contract-version is required with --result-dir")
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
            "manage-article-knowledge v0.5/v0.6 requires exactly one factual input: 30_本篇知识库资料.md"
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

    metadata_version = article.get("metadata_article_version", "")
    if args.article_version and metadata_version and args.article_version != metadata_version:
        raise SystemExit(
            f"--article-version {args.article_version!r} conflicts with article metadata version {metadata_version!r}"
        )

    managed = args.result_dir is not None
    contract_version = ""
    contract_path = ""
    if managed:
        if not v05_inputs or len(args.knowledge) != 1:
            raise SystemExit(
                "manage-article-knowledge v0.6 requires exactly one factual input: 30_本篇知识库资料.md"
            )
        knowledge_lines = read_text(args.knowledge[0]).splitlines()
        knowledge_id = parse_field(knowledge_lines, "文章ID")
        knowledge_version = parse_field(knowledge_lines, "文章版本")
        if not knowledge_id or not knowledge_version:
            raise SystemExit("30_本篇知识库资料.md must contain 文章ID and 文章版本 in v0.6 managed mode")
        if knowledge_id and knowledge_id != article_id:
            raise SystemExit("Article ID mismatch between article and 30_本篇知识库资料.md")
        if knowledge_version and knowledge_version != args.article_version:
            raise SystemExit("Article version mismatch between article and 30_本篇知识库资料.md")
        expected_version_dir = "v" + args.article_version.strip().lstrip("vV")
        result_dir = args.result_dir.resolve()
        if (
            result_dir.name != expected_version_dir
            or result_dir.parent.name != article_id
            or result_dir.parent.parent.name != args.project_id
        ):
            raise SystemExit(
                "Managed result directory must be [result root]/[project_id]/[article_id]/v[version]"
            )
        try:
            resolved_contract_path, contract = load_contract(args.manage_skill_root)
            contract_version = str(args.handoff_contract_version).strip()
            validate_event(
                "faithfulness_request",
                {
                    "handoff_event": "faithfulness_request",
                    "handoff_contract_version": contract_version,
                    "faithfulness_skill": "deepeval-article-audit",
                    "article_file": str(args.article.resolve()),
                    "knowledge_file": str(args.knowledge[0].resolve()),
                    "project_id": args.project_id,
                    "article_id": article_id,
                    "article_version": args.article_version,
                    "result_root": str(result_dir.parents[2]),
                    "result_dir": str(result_dir),
                },
                contract,
            )
            contract_path = str(resolved_contract_path)
        except ManagedHandoffError as exc:
            raise SystemExit(str(exc)) from exc

    output = args.output
    if managed:
        output = args.result_dir.resolve() / f"{artifact_stem(article_id)}-prepared.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            raise SystemExit(f"Refusing to overwrite existing managed prepared artifact: {output}")
    assert output is not None

    payload = {
        "schema_version": "1.0",
        "article_id": article_id,
        "project_id": args.project_id or "",
        "article_version": args.article_version or metadata_version,
        "article_title": article["title"],
        "article_file": str(args.article.resolve()),
        "knowledge_files": [str(path.resolve()) for path in args.knowledge],
        "article_sha256": sha256_file(args.article),
        "knowledge_sha256": [sha256_file(path) for path in args.knowledge],
        "integration_mode": "manage-article-knowledge-v0.6" if managed else "generic",
        "handoff_contract_version": contract_version,
        "handoff_contract_file": contract_path,
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
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "article_id": article_id,
                "article_units": len(article["article_units"]),
                "knowledge_chunks": len(chunks),
                "output": str(output.resolve()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
