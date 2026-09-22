"""Resolve and create the managed Faithfulness result directory."""

from __future__ import annotations

import re
from pathlib import Path


def safe_segment(value: str, label: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{label}不能为空")
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", value).strip(" .-")
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError(f"{label}包含无效目录名：{value!r}")
    return cleaned


def normalize_version(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError("文章版本不能为空，无法建立Faithfulness结果目录")
    normalized = value.lstrip("vV")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", normalized):
        raise ValueError(f"文章版本包含无效目录字符：{value!r}")
    return f"v{normalized}"


def managed_result_dir(
    result_root: Path,
    project_id: str,
    article_id: str,
    article_version: str,
    *,
    create: bool = True,
) -> Path:
    if not result_root.is_absolute():
        raise ValueError(f"Faithfulness结果总目录必须是绝对路径：{result_root}")
    target = (
        result_root
        / safe_segment(project_id, "项目ID")
        / safe_segment(article_id, "文章ID")
        / normalize_version(article_version)
    )
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target
