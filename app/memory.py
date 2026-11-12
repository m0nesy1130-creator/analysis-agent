"""按项目保存可回溯的记忆，召回时使用现存的文档原文。"""

import json
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

from .checkpoints import mysql_connection
from .contracts import Claim, Evidence
from .documents import load_evidence


SourceRef = tuple[str, str, int]
_WEIGHTS = {"working": 1, "abstract": 3}
_DAYS_PER_WEIGHT = 7
_SENSITIVE = re.compile(
    r"(?:密码|口令|密钥|令牌|password|api[_ -]?key|access[_ -]?token|secret)"
    r"\s*[`\"']?\s*[:=：]|\bbearer\s+[\w.-]+|\bsk-[\w-]{12,}|-----BEGIN [^-]*PRIVATE KEY-----",
    re.IGNORECASE,
)
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS analysis_memories (
    project VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    memory_id CHAR(64) CHARACTER SET ascii NOT NULL,
    kind VARCHAR(16) NOT NULL,
    weight TINYINT UNSIGNED NOT NULL,
    summary TEXT NOT NULL,
    source_refs JSON NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    PRIMARY KEY (project, memory_id),
    INDEX project_expiration (project, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _source_ref(item: Evidence) -> SourceRef:
    return item.source, item.version, item.line


def _read_original(ref: SourceRef, loaded: dict[str, dict[int, Evidence]]) -> Evidence | None:
    source, version, line = ref
    if source not in loaded:
        path = Path(source)
        try:
            document = load_evidence(path) if path.is_absolute() else ()
        except (OSError, ValueError, RuntimeError):
            document = ()
        loaded[source] = {item.line: item for item in document}
    item = loaded[source].get(line)
    if item is not None and item.source == source and item.version == version:
        return item
    return None


def recall_evidence(
    project: str,
    document: tuple[Evidence, ...],
    search: Callable[[tuple[Evidence, ...]], tuple[Evidence, ...]],
) -> tuple[Evidence, ...]:
    with mysql_connection() as (connection, _), connection.cursor() as cursor:
        cursor.execute(_CREATE_TABLE)
        cursor.execute(
            "SELECT summary, source_refs FROM analysis_memories "
            "WHERE project = %s AND expires_at > UTC_TIMESTAMP(6) ORDER BY memory_id",
            (project,),
        )
        rows = cursor.fetchall()

    current_sources = {item.source for item in document}
    loaded: dict[str, dict[int, Evidence]] = {}
    originals: dict[SourceRef, Evidence] = {}
    hints: dict[SourceRef, set[str]] = {}
    for summary, encoded_refs in rows:
        refs = json.loads(encoded_refs)
        verified = []
        for source, version, line in refs:
            item = _read_original((source, version, line), loaded)
            if item is None or _SENSITIVE.search(item.text):
                break
            verified.append(item)
        if len(verified) != len(refs):
            continue
        for item in verified:
            if item.source in current_sources:
                continue
            ref = _source_ref(item)
            if ref not in originals:
                originals[ref] = replace(item, evidence_id=f"M-{len(originals) + 1}")
            if summary:
                hints.setdefault(ref, set()).add(summary)

    by_id = {item.evidence_id: item for item in originals.values()}
    # 摘要只提供检索线索，返回给分析和复核节点的是重读的原文。
    candidates = tuple(
        replace(item, text="\n".join((item.text, *sorted(hints.get(ref, ())))))
        for ref, item in originals.items()
    )
    return tuple(by_id[item.evidence_id] for item in search(candidates)) if candidates else ()


def remember_supported(
    project: str,
    question: str,
    claim: Claim,
    cited: tuple[Evidence, ...],
    document: tuple[Evidence, ...],
    reviewed_at: str,
) -> None:
    # 纯召回任务不延长已有记忆的有效期。
    document_ids = {item.evidence_id for item in document}
    if not any(item.evidence_id in document_ids for item in cited):
        return
    if _SENSITIVE.search("\n".join((question, claim.text, *(item.text for item in cited)))):
        return

    loaded: dict[str, dict[int, Evidence]] = {}
    refs = sorted({_source_ref(item) for item in cited})
    if not all(_read_original(ref, loaded) is not None for ref in refs):
        return

    records = [
        ("working", "", [ref])
        for ref in sorted({_source_ref(item) for item in cited if item.evidence_id in document_ids})
    ]
    records.append(("abstract", claim.text, refs))
    reviewed = datetime.fromisoformat(reviewed_at)
    now = datetime.now(timezone.utc)
    values = []
    for kind, summary, source_refs in records:
        encoded_refs = json.dumps(source_refs, ensure_ascii=False)
        identity = json.dumps((kind, summary, source_refs), ensure_ascii=False)
        memory_id = sha256(identity.encode("utf-8")).hexdigest()
        weight = _WEIGHTS[kind]
        expires_at = reviewed + timedelta(days=_DAYS_PER_WEIGHT * weight)
        if expires_at > now:
            values.append(
                (project, memory_id, kind, weight, summary, encoded_refs, expires_at.replace(tzinfo=None))
            )

    if not values:
        return
    with mysql_connection() as (connection, _), connection.cursor() as cursor:
        cursor.execute(_CREATE_TABLE)
        cursor.execute(
            "DELETE FROM analysis_memories WHERE project = %s AND expires_at <= UTC_TIMESTAMP(6)",
            (project,),
        )
        connection.begin()
        cursor.executemany(
            "INSERT INTO analysis_memories (project, memory_id, kind, weight, summary, source_refs, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) AS incoming "
            "ON DUPLICATE KEY UPDATE expires_at = GREATEST(analysis_memories.expires_at, incoming.expires_at)",
            values,
        )
        connection.commit()
