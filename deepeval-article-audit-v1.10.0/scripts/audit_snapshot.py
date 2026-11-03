"""Compare prepared inputs by parsed content while retaining raw hashes for traceability."""
from __future__ import annotations

import re
from pathlib import Path

from audit_common import (
    article_content_sha256,
    extract_article,
    extract_knowledge_chunks,
    normalized_path,
    parse_field,
    public_chunk,
    read_text,
)
from evidence_identity import contexts_for_files
from prepare_article_audit import sha256_file


def _article_matches(prepared: dict, path: Path) -> bool:
    recorded_hash = str(prepared.get("article_sha256", "")).strip().lower()
    if recorded_hash and sha256_file(path) == recorded_hash:
        return True
    if not all(key in prepared for key in ("article_lines", "article_units", "article_content_sha256")):
        return False
    try:
        current = extract_article(path)
    except (OSError, UnicodeError, ValueError):
        return False
    if current["article_lines"] != prepared["article_lines"]:
        return False
    if article_content_sha256(current["article_lines"]) != prepared["article_content_sha256"]:
        return False
    expected_units = [
        {key: value for key, value in unit.items() if key != "candidate_evidence"}
        for unit in prepared["article_units"]
    ]
    if current["article_units"] != expected_units:
        return False
    if current["title"] != prepared.get("article_title", current["title"]):
        return False
    metadata_id = current.get("metadata_article_id")
    if metadata_id and metadata_id != prepared.get("article_id"):
        return False
    metadata_version = current.get("metadata_article_version")
    prepared_version = str(prepared.get("article_version", "")).strip()
    if metadata_version and prepared_version and metadata_version != prepared_version:
        return False
    return True


def _normalized_chunks(chunks: list[dict], source_path: Path, offset: int) -> list[dict]:
    output = []
    for index, chunk in enumerate(chunks, 1):
        item = public_chunk(chunk)
        item["source_file"] = normalized_path(source_path)
        item["chunk_id"] = f"K{offset + index:04d}"
        output.append(item)
    return output


def _normalized_contexts(contexts: list[dict], source_path: Path) -> list[dict]:
    output = []
    for context in contexts:
        item = dict(context)
        item["source_file"] = normalized_path(source_path)
        output.append(item)
    return output


def _knowledge_matches(
    prepared: dict,
    path: Path,
    source_aliases: dict[str, Path],
    offset: int,
) -> tuple[bool, int, list[str]]:
    expected_chunks = prepared.get("knowledge_chunks")
    if not isinstance(expected_chunks, list):
        hashes = prepared.get("knowledge_sha256", [])
        index = next((i for i, name in enumerate(prepared.get("knowledge_files", []))
                      if normalized_path(name) == normalized_path(path)), None)
        return bool(index is not None and index < len(hashes) and sha256_file(path) == hashes[index]), 0, []

    try:
        current_chunks = extract_knowledge_chunks(path)
    except (OSError, UnicodeError, ValueError):
        return False, 0, []

    old_name = next((name for name in prepared.get("knowledge_files", [])
                     if normalized_path(source_aliases.get(normalized_path(name), Path(name)))
                     == normalized_path(path)), str(path))
    expected = []
    for item in expected_chunks:
        if normalized_path(item.get("source_file", "")) != normalized_path(old_name):
            continue
        canonical = dict(item)
        canonical["source_file"] = normalized_path(path)
        expected.append(canonical)

    actual = _normalized_chunks(current_chunks, path, offset)
    if expected != actual:
        return False, len(current_chunks), []

    if "identity_contexts" in prepared:
        expected_contexts = [
            {**context, "source_file": normalized_path(path)}
            for context in prepared.get("identity_contexts", [])
            if normalized_path(context.get("source_file", "")) == normalized_path(old_name)
        ]
        try:
            current_contexts = _normalized_contexts(contexts_for_files([path]), path)
        except (OSError, UnicodeError, ValueError):
            return False, len(current_chunks), []
        if expected_contexts != current_contexts:
            return False, len(current_chunks), []

    lines = read_text(path).splitlines()
    article_id = parse_field(lines, "文章ID")
    article_version = parse_field(lines, "文章版本")
    if article_id and article_id != prepared.get("article_id"):
        return False, len(current_chunks), []
    prepared_version = str(prepared.get("article_version", "")).strip()
    if article_version and prepared_version and article_version != prepared_version:
        return False, len(current_chunks), []

    eligible_ids = [
        f"K{offset + index:04d}"
        for index, chunk in enumerate(current_chunks, 1)
        if chunk.get("_evidence_eligible", True)
    ]
    return True, len(current_chunks), eligible_ids


def parsed_input_conflicts(
    prepared: dict | None,
    *,
    article_path: Path | None = None,
    knowledge_paths: list[Path] | None = None,
    view_path: Path | None = None,
) -> list[str]:
    """Return conflicts that change parsed article units or allowed evidence anchors."""
    if not prepared:
        return []
    conflicts: list[str] = []
    original_article = str(prepared.get("article_file", ""))
    current_article = article_path or (Path(original_article) if original_article else None)
    if current_article is not None:
        if not current_article.is_file():
            conflicts.append(f"{current_article.name}: 文章文件不存在")
        elif not _article_matches(prepared, current_article):
            conflicts.append(f"{current_article.name}: 解析后的文章单元或行号与prepared不同")

    original_knowledge = [str(name) for name in prepared.get("knowledge_files", [])]
    current_knowledge = knowledge_paths or [Path(name) for name in original_knowledge]
    if len(current_knowledge) != len(original_knowledge):
        conflicts.append("知识文件数量与prepared不同")
        return conflicts

    aliases = {
        normalized_path(old): current.resolve()
        for old, current in zip(original_knowledge, current_knowledge)
    }
    offset = 0
    current_eligible_ids: list[str] = []
    for path in current_knowledge:
        if not path.is_file():
            conflicts.append(f"{path.name}: 知识文件不存在")
            continue
        matches, chunk_count, eligible_ids = _knowledge_matches(prepared, path, aliases, offset)
        if not matches:
            conflicts.append(f"{path.name}: 解析后的证据块、行号或身份上下文与prepared不同")
        current_eligible_ids.extend(eligible_ids)
        offset += chunk_count

    if view_path is not None and view_path.is_file() and not conflicts:
        view_ids = re.findall(r"^###\s+(K\d{4})\s+·", read_text(view_path), re.M)
        if view_ids != current_eligible_ids:
            conflicts.append(f"{view_path.name}: 允许证据范围与当前知识文件解析结果不同")
    return conflicts
