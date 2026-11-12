"""以固定语法生成 MySQL 单表只读查询。"""

import json
import math
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path

import pymysql
from typing_extensions import TypedDict

from .contracts import Evidence
from .credentials import LOCAL_MYSQL, read_credential


CONFIG = Path(__file__).resolve().parents[1] / "runtime/query_sources.json"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_OPERATORS = {"eq": "=", "ne": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}
_AGGREGATES = {"count", "sum", "avg", "min", "max"}
_NUMERIC = {"tinyint", "smallint", "mediumint", "int", "bigint", "decimal", "float", "double"}


class QuerySource(TypedDict):
    id: str
    label: str
    database: str
    table: str
    user: str
    port: int
    key: str
    columns: list[str]


def _name(value: object) -> bool:
    return isinstance(value, str) and _NAME.fullmatch(value) is not None


def query_sources() -> dict[str, QuerySource]:
    path = Path(os.environ.get("ANALYSIS_AGENT_QUERY_CONFIG", CONFIG))
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise RuntimeError("无法读取 MySQL 查询数据源配置。") from error
    if not isinstance(raw, list):
        raise RuntimeError("MySQL 查询配置须为列表。")

    blocked = {name.casefold() for name in (
        LOCAL_MYSQL[3], os.environ.get("ANALYSIS_AGENT_MYSQL_DATABASE", LOCAL_MYSQL[3]),
        "mysql", "sys", "information_schema", "performance_schema",
    )}
    sources = {}
    for row in raw:
        if not isinstance(row, dict):
            raise RuntimeError("MySQL 查询数据源格式无效。")
        source_id, label = row.get("id"), row.get("label")
        database, table, user, key = (row.get(field) for field in ("database", "table", "user", "key"))
        port, columns = row.get("port", 3306), row.get("columns")
        if (
            not isinstance(source_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", source_id)
            or source_id in sources or not isinstance(label, str) or not label.strip()
            or not all(_name(value) for value in (database, table, user, key)) or database.casefold() in blocked
            or type(port) is not int or not 1 <= port <= 65535 or not isinstance(columns, list)
            or not 1 <= len(columns) <= 32 or any(not _name(column) for column in columns) or key not in columns
        ):
            raise RuntimeError("MySQL 查询数据源的表、账号或字段配置无效。")
        sources[source_id] = QuerySource(
            id=source_id, label=label.strip(), database=database, table=table, user=user,
            port=port, key=key, columns=list(dict.fromkeys(columns)),
        )
    return sources


@contextmanager
def _read_connection(source: QuerySource):
    if query_sources().get(source["id"]) != source:
        raise RuntimeError("MySQL 数据源配置已变更，请重新选择。")
    password = os.environ.get(f"ANALYSIS_AGENT_QUERY_{source['id'].upper()}_PASSWORD")
    password = password or read_credential(f"analysis-agent/MySQL/query/{source['id']}")
    if not password:
        raise RuntimeError("MySQL 查询源缺少只读账号凭据。")
    try:
        with pymysql.connect(
            host="127.0.0.1", port=source["port"], user=source["user"], password=password.encode("utf-8"),
            database=source["database"], charset="utf8mb4", connect_timeout=5, read_timeout=8, write_timeout=5,
        ) as connection, connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            yield cursor
    except pymysql.MySQLError as error:
        code = error.args[0] if error.args and isinstance(error.args[0], int) else "未知"
        raise RuntimeError(f"MySQL 只读查询失败（错误码 {code}）。") from None


def _columns(cursor, source: QuerySource) -> dict[str, str]:
    cursor.execute(
        "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
        (source["database"], source["table"]),
    )
    actual = dict(cursor.fetchall())
    if not set(source["columns"]) <= actual.keys():
        raise RuntimeError("实际 MySQL 表缺少已配置字段或只读账号无法查看。")
    return {column: actual[column] for column in source["columns"]}


def _trace(source: QuerySource, record: str, text: str, version: str, suffix: str) -> Evidence:
    origin = f"MySQL {source['database']}.{source['table']}"
    return Evidence(f"DB-{version[:14]}-{suffix}", origin, None, text, version, record,
                    retrieved_at=datetime.now(timezone.utc).date().isoformat())


def schema_evidence(source: QuerySource) -> tuple[tuple[Evidence, ...], str]:
    with _read_connection(source) as cursor:
        fields = _columns(cursor, source)
    text = json.dumps(fields, ensure_ascii=False)
    version = sha256(f"{source['database']}.{source['table']} {text}".encode()).hexdigest()
    return (_trace(source, "字段结构", text, version, "S"),), f"读取 {len(fields)} 个获准字段及实际类型。"


def compile_query(source: QuerySource, fields: dict[str, str], spec: dict) -> tuple[str, tuple, tuple[str, ...]]:
    columns, filters, group = (spec.get(key, []) for key in ("columns", "filters", "group_by"))
    aggregate = spec.get("aggregate")
    if not isinstance(columns, list) or len(columns) > 12 or any(not _name(col) or col not in fields for col in columns):
        raise ValueError("查询列超出授权范围。")
    if not isinstance(group, list) or len(group) > 3 or any(not _name(col) or col not in fields for col in group):
        raise ValueError("分组列超出授权范围。")
    if not isinstance(filters, list) or len(filters) > 8:
        raise ValueError("查询条件必须为至多八项的列表。")

    where, values = [], []
    for condition in filters:
        if not isinstance(condition, dict):
            raise ValueError("查询条件格式无效。")
        column, op, value = condition.get("column"), condition.get("op"), condition.get("value")
        if not _name(column) or column not in fields or not isinstance(op, str) or op not in _OPERATORS:
            raise ValueError("查询条件包含未授权字段或运算符。")
        if type(value) not in {str, int, float} or len(str(value)) > 240 or (type(value) is float and not math.isfinite(value)):
            raise ValueError("查询条件值格式无效。")
        if fields[column] in _NUMERIC:
            try:
                value = Decimal(str(value))
                if not value.is_finite() or abs(value.adjusted()) > 100:
                    raise InvalidOperation
            except InvalidOperation:
                raise ValueError("数值字段需要有效的数值条件。") from None
        elif not isinstance(value, str):
            raise ValueError("文本和日期字段需要使用字符串条件。")
        where.append(f"`{column}` {_OPERATORS[op]} %s")
        values.append(value)

    if aggregate is None:
        if group or not columns:
            raise ValueError("明细查询需要指定获准列，分组须搭配聚合。")
        output = tuple(dict.fromkeys((source["key"], *columns)))
        select = ", ".join(f"`{column}`" for column in output)
        suffix = f" ORDER BY `{source['key']}`"
    else:
        if not isinstance(aggregate, dict) or columns:
            raise ValueError("聚合查询使用 group_by 指定输出维度。")
        function, column = aggregate.get("function"), aggregate.get("column", "*")
        if not isinstance(function, str) or function not in _AGGREGATES:
            raise ValueError("聚合函数超出允许范围。")
        if column == "*" and function == "count":
            argument = "*"
        elif _name(column) and column in fields and (function not in {"sum", "avg"} or fields[column] in _NUMERIC):
            argument = f"`{column}`"
        else:
            raise ValueError("聚合字段未获准或类型不匹配。")
        group = list(dict.fromkeys(group))
        group_sql = ", ".join(f"`{column}`" for column in group)
        select = ", ".join(filter(None, (group_sql, f"{function.upper()}({argument})")))
        suffix = f" GROUP BY {group_sql} ORDER BY {group_sql}" if group else ""
        output = (*group, f"{function}({column})")

    condition_sql = f" WHERE {' AND '.join(where)}" if where else ""
    sql = f"SELECT /*+ MAX_EXECUTION_TIME(5000) */ {select} FROM `{source['table']}`{condition_sql}{suffix} LIMIT 51"
    return sql, tuple(values), output


def run_query(source: QuerySource, spec: dict) -> tuple[tuple[Evidence, ...], str]:
    with _read_connection(source) as cursor:
        fields = _columns(cursor, source)
        sql, values, output = compile_query(source, fields, spec)
        cursor.execute(sql, values)
        records = cursor.fetchall()
    text = f"SQL {sql}\n绑定参数 {json.dumps(values, ensure_ascii=False, default=str)}\n返回 {len(records)} 行。"
    version = sha256(json.dumps([source["database"], text, records], ensure_ascii=False, default=str).encode()).hexdigest()
    if len(records) > 50:
        return (_trace(source, "查询记录", text + " 命中超过 50 行，请缩小条件或改用聚合。", version, "Q"),), "命中超限，请缩小条件或聚合。"
    trace = _trace(source, "查询记录", text, version, "Q")
    rows = tuple(
        _trace(source, str(row[0]) if spec.get("aggregate") is None else f"聚合结果 {index}",
               json.dumps(dict(zip(output, row)), ensure_ascii=False, default=str), version, str(index))
        for index, row in enumerate(records, start=1)
    )
    return (trace, *rows), f"只读查询返回 {len(rows)} 行。"
