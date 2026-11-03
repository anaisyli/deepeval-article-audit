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
    STRUCTURAL_UNIT_TYPES,
    TERM_RE,
    extract_article,
    article_content_sha256,
    extract_knowledge_chunks,
    is_v05_writing_material,
    parse_field,
    public_chunk,
    read_text,
)
from audit_compact import write_views
from evidence_identity import contexts_for_files
from result_layout import work_dir
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
        if unit.get("unit_type") in STRUCTURAL_UNIT_TYPES:
            unit["candidate_evidence"] = []
            continue
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


MANAGED_TASK_STATE = "30_等待Faithfulness"


HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
RESTORED_NOTE = "30只有换行符与写作许可绑定的字节不同，已恢复为写作许可绑定的原字节；文字内容未变"


def managed_task_problem(article: Path, knowledge: Path, *, article_id: str = "", article_version: str = "") -> str:
    """v0.6 managed audits read the knowledge base's own task files, never copies.

    A copy (for instance with its version field edited by hand) can never be
    imported and breaks the writing permit's byte binding, so it is refused
    before any extraction work is spent on it.  When the task's 30 differs from the
    permit only in line endings, the permitted bytes are restored (see
    restore_permitted_material); every other difference is refused.
    """
    problem, _ = managed_task_check(article, knowledge, article_id=article_id, article_version=article_version)
    return problem


def managed_task_check(article: Path, knowledge: Path, *, article_id: str = "",
                       article_version: str = "") -> tuple[str, str]:
    """Return (problem, note); the note says when the permitted 30 bytes were restored."""
    article, knowledge = article.resolve(), knowledge.resolve()
    task = article.parent
    if (article.name != "40_最终文章.md" or knowledge != task / "30_本篇知识库资料.md"
            or task.parent.name != MANAGED_TASK_STATE or task.parent.parent.name != "04_文章任务"):
        return ("v0.6 managed mode reads only the knowledge base's own task files: "
                f"04_文章任务/{MANAGED_TASK_STATE}/<task>/40_最终文章.md and the sibling 30_本篇知识库资料.md. "
                "Never copy them or edit their version fields; ask the knowledge base to issue a new version, "
                "or audit a copy in generic mode with --output"), ""
    permit = task / "writing_ready.json"
    if not permit.is_file():
        return "", ""
    try:
        return "", restore_permitted_material(task, permit, knowledge, article_id=article_id,
                                              article_version=article_version)
    except ValueError as exc:
        return str(exc), ""


def restore_permitted_material(task: Path, permit_path: Path, material: Path, *, article_id: str = "",
                               article_version: str = "") -> str:
    """Make the task's 30 the exact bytes writing_ready.json bound for the writer, or refuse.

    Agent file tools rewrite CRLF/LF (P-005-ART-20261022-001: the 30 became LF two hours after
    its permit); the knowledge base's import restores those bytes, and before this prepare
    refused the same file.  The restore source is the permit's own knowledge file when it still
    exists with the bound hash, otherwise the current file with only its line terminators
    changed; either is written only when its SHA-256 equals the bound value, i.e. it is
    byte-for-byte the permitted file.  Any other difference (text, BOM, encoding, a lone CR)
    raises, as does a permit that does not identify this task's 30.
    """
    hand_back = "；交回知识库处理，不要改30、35或版本字段，也不要另建结果目录"
    try:
        permit = json.loads(read_text(permit_path))
    except ValueError as exc:
        raise ValueError(f"input_snapshot_changed: writing_ready.json 无法读取（{exc}）{hand_back}") from exc
    if not isinstance(permit, dict):
        raise ValueError(f"input_snapshot_changed: writing_ready.json 不是写作许可对象{hand_back}")
    bound = str(permit.get("knowledge_sha256", "")).strip().lower()
    if not HEX_SHA256.fullmatch(bound):
        raise ValueError(f"input_snapshot_changed: writing_ready.json 缺少有效的 knowledge_sha256{hand_back}")
    for field, expected in (("article_id", article_id), ("article_version", article_version)):
        recorded = str(permit.get(field, "")).strip()
        if expected and recorded and recorded.lstrip("vV") != expected.strip().lstrip("vV"):
            raise ValueError(f"input_snapshot_changed: writing_ready.json 的 {field} 为 {recorded}，"
                             f"与当前文章 {expected} 不同{hand_back}")
    named = str(permit.get("knowledge_file", "")).strip()
    if named:
        # The task folder moves between states (20_等待终稿 → 30_等待Faithfulness); its name and the file must not.
        named_path = Path(named)
        if named_path.name != material.name or named_path.parent.name != task.name:
            raise ValueError(f"input_snapshot_changed: writing_ready.json 指向的不是本任务的30（{named}）{hand_back}")
    if not material.is_file():
        raise ValueError(f"input_snapshot_changed: 任务目录中没有 {material.name}{hand_back}")
    current = material.read_bytes()
    if hashlib.sha256(current).hexdigest() == bound:
        return ""
    candidates = []
    if named and Path(named).is_file() and Path(named).resolve() != material.resolve():
        candidates.append(Path(named).read_bytes())  # the permit's own copy, when it still exists
    lf = re.sub(rb"\r+\n", b"\n", current)
    candidates += [lf, lf.replace(b"\n", b"\r\n")]
    for candidate in candidates:
        same_text = re.sub(rb"\r+\n", b"\n", candidate) == lf
        if same_text and hashlib.sha256(candidate).hexdigest() == bound:
            staging = material.with_name(material.name + ".restoring")
            staging.write_bytes(candidate)
            staging.replace(material)
            if hashlib.sha256(material.read_bytes()).hexdigest() != bound:
                raise ValueError(f"input_snapshot_changed: 30 恢复后仍与写作许可不一致{hand_back}")
            return RESTORED_NOTE
    raise ValueError("input_snapshot_changed: 30_本篇知识库资料.md is not the byte sequence writing_ready.json bound "
                     "for the writer, and the difference is more than line endings (text, BOM or encoding changed)"
                     + hand_back)


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
    restored_note = ""
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
        task_problem, restored_note = managed_task_check(args.article, args.knowledge[0], article_id=article_id,
                                                         article_version=args.article_version)
        if task_problem:
            raise SystemExit(task_problem)
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
        "article_content_sha256": article_content_sha256(article["article_lines"]),
        "knowledge_sha256": [sha256_file(path) for path in args.knowledge],
        "integration_mode": "manage-article-knowledge-v0.6" if managed else "generic",
        "handoff_contract_version": contract_version,
        "handoff_contract_file": contract_path,
        "article_lines": article["article_lines"],
        "article_units": article["article_units"],
        "knowledge_chunks": [public_chunk(chunk) for chunk in chunks],
        "identity_contexts": contexts_for_files(args.knowledge),
        "rules": {
            "headings_excluded": True,
            "structured_headings_preserved": True,
            "markdown_images_excluded": True,
            "homogeneous_enumerations": "one set-valued claim when subject, predicate, scope, condition, time and modality are shared",
            "denominator": "all atomic factual claims in article body",
            "numerator": "claims with semantic_status=entailed",
            "score": "strict_support: supported / total factual claims",
            "deepeval_reproduction": "yes/no/idk; default DeepEval idk behavior is retained for diagnostic comparison",
            "evaluation_protocol_version": "2.0",
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # Model-facing reading view: allowed evidence appears once instead of per candidate.
    view = write_views(payload, work_dir(output.parent),
                       output.name.removesuffix("-prepared.json").removesuffix(".json"))
    print(
        json.dumps(
            {
                "article_id": article_id,
                "article_units": len(article["article_units"]),
                "knowledge_chunks": len(chunks),
                "output": str(output.resolve()),
                "audit_view": str(view.resolve()),
                **({"knowledge_restored": restored_note} if restored_note else {}),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    from audit_common import utf8_stdio

    utf8_stdio()
    main()
