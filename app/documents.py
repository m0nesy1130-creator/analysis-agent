"""读取 UTF-8 文档，保留原文行号和内容版本。"""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from .contracts import Evidence


MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
DOCUMENT_SUFFIXES = {".md", ".txt"}


class DocumentLineLimitError(ValueError):
    """文件行数超过本次加载的剩余配额。"""


def _make_evidence(text: str, source: str, version: str) -> tuple[Evidence, ...]:
    return tuple(
        Evidence(evidence_id=f"L{number}", source=source, line=number, text=line, version=version)
        for number, line in enumerate(text.splitlines(), start=1)
        if line.strip()
    )


def evidence_from_text(text: str, source: str) -> tuple[Evidence, ...]:
    """为内置示例生成和本地文档相同的证据结构。"""
    version = sha256(text.encode("utf-8")).hexdigest()
    return _make_evidence(text, source, version)


def evidence_from_bytes(raw: bytes, source: str, *, max_lines: int | None = None) -> tuple[Evidence, ...]:
    text = raw.decode("utf-8-sig")
    if max_lines is not None and sum(bool(line.strip()) for line in text.splitlines()) > max_lines:
        raise DocumentLineLimitError("目录中的有效文本行数超过上限。")
    return _make_evidence(text, source, sha256(raw).hexdigest())


def load_evidence(path: Path) -> tuple[Evidence, ...]:
    """读取 .txt 或 .md 文件，哈希基于读取到的原始字节。"""
    source = path.resolve(strict=True)
    if source.suffix.lower() not in DOCUMENT_SUFFIXES:
        raise ValueError("首版支持 UTF-8 编码的 .txt 和 .md 文档。")

    return evidence_from_bytes(source.read_bytes(), str(source))


def find_evidence(
    query: str, evidence: tuple[Evidence, ...], limit: int = 5
) -> tuple[Evidence, ...]:
    """按字面匹配检索，保留命中顺序和原始证据。"""
    keyword = query.strip().casefold()
    if not keyword or limit < 1:
        return ()
    return tuple(item for item in evidence if keyword in item.text.casefold())[:limit]


def search_documents(
    keyword: str, question: str, document: tuple[Evidence, ...], *, hybrid: bool
) -> tuple[Evidence, ...]:
    if not hybrid:
        return find_evidence(keyword, document)
    from .retrieval import find_hybrid_evidence

    return find_hybrid_evidence(keyword, question, document)


def supplement_evidence(
    document: tuple[Evidence, ...], reply: list[dict], number: int
) -> tuple[Evidence, ...]:
    known = {(item.source, item.version) for item in document}
    incoming = (Evidence(**row) for row in reply)
    return tuple(
        replace(item, evidence_id=f"S{number}-{item.evidence_id}")
        for item in incoming
        if (item.source, item.version) not in known
    )
