"""命令行和 HTTP 接口共用的任务状态与补证操作。"""

from collections.abc import Callable
from dataclasses import asdict
from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command, StateSnapshot

from .contracts import Evidence, TaskRequest
from .database_query import QuerySource
from .deepseek_client import DeepSeekClient
from .directories import DirectorySource
from .graphs import AnalysisState, build_graph
from .payments import PaymentScope
from .planning import latest_results
from .report import render_report, render_step_report


Mode = Literal["offline", "live"]


class TaskNotFoundError(ValueError):
    """没有对应任务的检查点。"""


class TaskConflictError(ValueError):
    """请求与任务的最新状态不符。"""


class TaskInputError(ValueError):
    """提交的内容不足以完成当前操作。"""


def normalize_project(project: str | None) -> str | None:
    if project is None:
        return None
    project = project.strip()
    if not project or not project.isprintable() or len(project) > 128:
        raise ValueError("项目名必须为 1 至 128 个可打印字符。")
    return project


def task_graph(mode: Mode, checkpointer: BaseCheckpointSaver | None):
    client = DeepSeekClient.from_environment() if mode == "live" else None
    return build_graph(client, checkpointer=checkpointer)


def initial_state(
    question: str,
    document: tuple[Evidence, ...],
    *,
    mode: Mode,
    hybrid: bool,
    project: str | None,
    clarify: bool = False,
    multistep: bool = False,
    directory: DirectorySource | None = None,
    payment: PaymentScope | None = None,
    adaptive: bool = False,
    web_enabled: bool = False,
    query_source: QuerySource | None = None,
) -> AnalysisState:
    state = AnalysisState(request=TaskRequest(question), document=document, mode=mode, hybrid=hybrid)
    if project is not None:
        state["project"] = project
    if clarify:
        state["clarify"] = True
    if multistep:
        state["multistep"] = True
    if directory is not None:
        state["directory"] = directory
    if payment is not None:
        state["payment"] = payment
    if adaptive or web_enabled or query_source is not None:
        state["adaptive"] = True
        state["multistep"] = True
    if web_enabled:
        state["web_enabled"] = True
    if query_source is not None:
        state["query_source"] = query_source
    return state


def load_snapshot(graph, config) -> StateSnapshot:
    snapshot = graph.get_state(config)
    if "request" not in snapshot.values:
        raise TaskNotFoundError("找不到该任务。请核对任务编号和 MySQL 库。")
    return snapshot


def pending_kind(snapshot: StateSnapshot) -> Literal["clarification", "supplement"] | None:
    if not snapshot.interrupts:
        return None
    value = snapshot.interrupts[0].value
    return "clarification" if isinstance(value, dict) and value.get("kind") == "clarification" else "supplement"


def input_receipt(snapshot: StateSnapshot) -> str:
    checkpoint_id = snapshot.config["configurable"]["checkpoint_id"]
    return f"{checkpoint_id}:{snapshot.interrupts[0].id}"


def _validate_input(snapshot: StateSnapshot, receipt: str, kind: str) -> None:
    if pending_kind(snapshot) != kind or receipt != input_receipt(snapshot):
        raise TaskConflictError("回执已处理、过期或与当前操作不符。请查询最新任务状态。")


def resume_with_clarification(
    graph,
    config,
    snapshot: StateSnapshot,
    receipt: str,
    *,
    action: Literal["answer", "confirm"],
    answer: str | None,
    direction: int | None,
):
    _validate_input(snapshot, receipt, "clarification")
    proposal = snapshot.values["clarification"]
    answer = answer.strip() if answer else None
    selected = None
    if action == "confirm":
        if not proposal["scope"]:
            raise TaskInputError("分析范围仍待明确，请先回答当前问题。")
        if direction is not None or answer:
            raise TaskInputError("请先提交补充内容，再确认新的范围。")
    else:
        if direction is not None:
            if not 0 <= direction < len(proposal["directions"]):
                raise TaskInputError("请选择本轮提供的分析方向。")
            selected = proposal["directions"][direction]
        if not answer and selected is None:
            raise TaskInputError("请输入补充信息或选择分析方向。")
    reply = {"action": action, "answer": answer or None, "direction": selected}
    return graph.invoke(Command(resume={snapshot.interrupts[0].id: reply}), config, durability="sync")


def resume_with_document(
    graph,
    config,
    snapshot: StateSnapshot,
    receipt: str,
    read_document: Callable[[], tuple[Evidence, ...]],
):
    # 用最新快照验证回执。恢复始终使用仅包含任务编号的 config。
    _validate_input(snapshot, receipt, "supplement")
    evidence = read_document()
    if not evidence:
        raise ValueError("补充文档没有可读内容。")
    first = evidence[0]
    if any(
        item.version == first.version
        and (snapshot.values.get("multistep") or item.source == first.source)
        for item in snapshot.values["document"]
    ):
        raise TaskConflictError("该版本文档已在任务中。请提供新的补充材料。")
    reply = [asdict(item) for item in evidence]
    return graph.invoke(Command(resume={snapshot.interrupts[0].id: reply}), config, durability="sync")


def finish_with_current_evidence(graph, config, snapshot: StateSnapshot, receipt: str):
    _validate_input(snapshot, receipt, "supplement")
    if not snapshot.values.get("plan_steps"):
        raise TaskConflictError("当前任务不支持提前汇总。")
    return graph.invoke(Command(resume={snapshot.interrupts[0].id: []}), config, durability="sync")


def report_from_state(state: AnalysisState) -> str:
    if steps := state.get("plan_steps"):
        return render_step_report(
            state["request"], steps, latest_results(state.get("step_results", [])), state["review"],
            mode=state["mode"], hybrid=state.get("hybrid", False), project=state.get("project"),
            original_question=state.get("original_question"),
            directory=state.get("directory"),
            payment=state.get("payment"),
            observations=state.get("tool_observations", []), planning_evidence=state.get("planning_evidence", ()),
            planning_rejected=state.get("planning_rejected", ()),
        )
    return render_report(
        state["request"],
        state["plan"],
        state["claim"],
        state["review"],
        state["candidates"],
        mode=state["mode"],
        hybrid=state.get("hybrid", False),
        project=state.get("project"),
        original_question=state.get("original_question"),
        directory=state.get("directory"),
    )
