"""只读扫描用户指定的目录，并为每个文件分配独立证据编号。"""

import os
import stat
from collections import Counter
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from typing_extensions import TypedDict

from .contracts import Evidence
from .documents import DOCUMENT_SUFFIXES, MAX_DOCUMENT_BYTES, DocumentLineLimitError, evidence_from_bytes


MAX_DIRECTORY_ENTRIES = 5_000
MAX_DIRECTORY_BYTES = 4 * 1024 * 1024
MAX_DIRECTORY_LINES = 8_000


class DirectorySource(TypedDict):
    path: str
    recursive: bool
    file_count: int
    skipped: dict[str, int]


class DirectoryInputError(ValueError):
    """指定的资料目录无法用于当前任务。"""


def _is_link(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _resolve_root(value: str) -> Path:
    path = Path(value.strip().strip('"'))
    if "\0" in value or not path.is_absolute() or (os.name == "nt" and len(path.drive) != 2):
        raise DirectoryInputError("请输入本机资料目录的绝对路径，例如 C:\\资料\\合同。")
    try:
        info = path.lstat()
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise DirectoryInputError("请选择实际存在的资料目录，目录入口须为普通文件夹。")
        return path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise DirectoryInputError("无法打开指定目录，请检查路径和读取权限。") from error


def _files(root: Path, recursive: bool, skipped: Counter[str]) -> Iterator[tuple[Path, int]]:
    pending = [root]
    visited = 0
    while pending:
        current = pending.pop()
        children = []
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    visited += 1
                    if visited > MAX_DIRECTORY_ENTRIES:
                        raise DirectoryInputError("目录条目超过 5,000 项，请缩小资料目录或关闭包含子目录。")
                    children.append(entry)
        except OSError as error:
            if current == root:
                raise DirectoryInputError("无法读取指定目录，请检查读取权限。") from error
            skipped["无法读取"] += 1
            continue

        subdirectories = []
        for entry in sorted(children, key=lambda item: (item.name.casefold(), item.name)):
            try:
                info = entry.stat(follow_symlinks=False)
                if _is_link(info):
                    skipped["链接或连接点"] += 1
                    continue
                is_directory = stat.S_ISDIR(info.st_mode)
                if is_directory and not recursive:
                    continue
                path = Path(entry.path)
                if not is_directory and (
                    not stat.S_ISREG(info.st_mode) or path.suffix.lower() not in DOCUMENT_SUFFIXES
                ):
                    skipped["不支持的格式"] += 1
                    continue
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(root):
                    skipped["超出指定目录"] += 1
                    continue
                if is_directory:
                    subdirectories.append(resolved)
                else:
                    yield resolved, info.st_size
            except (OSError, RuntimeError):
                skipped["无法读取"] += 1
        pending.extend(reversed(subdirectories))


def load_directory(value: str, *, recursive: bool = False) -> tuple[DirectorySource, tuple[Evidence, ...]]:
    root = _resolve_root(value)
    skipped: Counter[str] = Counter()
    evidence = []
    file_count = 0
    bytes_read = 0
    for path, size in _files(root, recursive, skipped):
        if size > MAX_DOCUMENT_BYTES:
            skipped["单文件超过 2 MiB"] += 1
            continue
        try:
            with path.open("rb") as stream:
                raw = stream.read(MAX_DOCUMENT_BYTES + 1)
        except OSError:
            skipped["无法读取"] += 1
            continue
        if len(raw) > MAX_DOCUMENT_BYTES:
            skipped["单文件超过 2 MiB"] += 1
            continue
        bytes_read += len(raw)
        if bytes_read > MAX_DIRECTORY_BYTES:
            raise DirectoryInputError("目录内待解析文档超过 4 MiB，请缩小资料范围。")
        try:
            rows = evidence_from_bytes(raw, str(path), max_lines=MAX_DIRECTORY_LINES - len(evidence))
        except UnicodeDecodeError:
            skipped["非 UTF-8"] += 1
            continue
        except DocumentLineLimitError as error:
            raise DirectoryInputError("目录内有效文本超过 8,000 行，请缩小资料范围。") from error
        if not rows:
            skipped["空文档"] += 1
            continue
        file_count += 1
        evidence.extend(replace(item, evidence_id=f"D{file_count}-{item.evidence_id}") for item in rows)

    if not evidence:
        details = "、".join(f"{reason} {count} 项" for reason, count in sorted(skipped.items()))
        suffix = f"（{details}）" if details else ""
        raise DirectoryInputError(f"指定范围内没有可读取的 UTF-8 Markdown 或 TXT 文档{suffix}。")
    return DirectorySource(path=str(root), recursive=recursive, file_count=file_count, skipped=dict(skipped)), tuple(evidence)
