"""从命令行运行文档分析。默认使用内置资料和离线逻辑。"""

import argparse
import sys
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, StateSnapshot
from langsmith import tracing_context

from .checkpoints import checkpoint_serializer, mysql_checkpointer
from .database_query import query_sources
from .directories import load_directory
from .documents import evidence_from_text, load_evidence
from .payments import payment_scope
from .tasks import (
    finish_with_current_evidence,
    initial_state,
    input_receipt,
    load_snapshot,
    normalize_project,
    report_from_state,
    resume_with_document,
    task_graph,
)


SAMPLE_DOCUMENT = """# 试点项目说明
试点工期为 30 天，从立项次日开始计算。
验收材料包括测试记录和部署说明。
生产系统上线由后续阶段负责。
"""


def _supplement_from_file(path: Path) -> list[dict[str, object]]:
    evidence = load_evidence(path)
    if not evidence:
        raise ValueError("补充文档没有可读内容。")
    return [asdict(item) for item in evidence]


def _read_supplement(prompt: str) -> list[dict[str, object]]:
    print(prompt)
    while True:
        try:
            path = input("请输入补充文档路径（UTF-8 .md 或 .txt，回车结束）：").strip()
        except (EOFError, KeyboardInterrupt):
            return []
        if not path:
            return []
        try:
            return _supplement_from_file(Path(path.strip('"')))
        except (OSError, ValueError) as error:
            print(f"无法读取补充文档：{error}", file=sys.stderr)


def _show_waiting_task(snapshot: StateSnapshot) -> None:
    thread_id = snapshot.config["configurable"]["thread_id"]
    pending = snapshot.interrupts[0].value
    if isinstance(pending, dict):
        print(pending["prompt"])
        print("请在本地网页控制台完成需求澄清。")
    else:
        print(pending)
    print(f"恢复回执：{thread_id}:{input_receipt(snapshot)}")


def _resume_saved_task(graph, config, receipt: str, supplement: Path | None, mode: str, *, finish: bool):
    snapshot = load_snapshot(graph, config)
    if snapshot.values["mode"] != mode:
        raise ValueError("续跑时的模式须与任务启动时一致（是否使用 --live）。")

    if supplement is not None:
        return resume_with_document(graph, config, snapshot, receipt, lambda: load_evidence(supplement))
    if finish:
        return finish_with_current_evidence(graph, config, snapshot, receipt)
    if snapshot.interrupts:
        _show_waiting_task(snapshot)
        return None
    if snapshot.tasks:
        return graph.invoke(None, config, durability="sync")
    return snapshot.values


def main() -> int:
    parser = argparse.ArgumentParser(description="运行文档分析 Agent 示例")
    parser.add_argument("--file", type=Path, help="UTF-8 编码的 Markdown 或文本文件")
    parser.add_argument("--directory", help="本机资料目录的绝对路径")
    parser.add_argument("--include-subdirectories", action="store_true", help="扫描资料目录下的子目录")
    parser.add_argument("--question", help="要检索的问题或关键词")
    parser.add_argument("--live", action="store_true", help="显式启用 DeepSeek 官方 API")
    parser.add_argument("--hybrid", action="store_true", help="结合关键词和本地向量检索，需要 --live")
    parser.add_argument("--multistep", action="store_true", help="生成按依赖执行的多步计划，需要 --live")
    parser.add_argument("--adaptive", action="store_true", help="根据检索和复核结果选择下一步动作，需要 --live")
    parser.add_argument("--web", action="store_true", help="允许搜索和读取公开网页，需要 --live")
    parser.add_argument("--query-source", help="选择已配置的 MySQL 只读查询表")
    parser.add_argument("--project", help="启用同名项目的持久记忆，需要 --live 和 --question")
    parser.add_argument("--payment-source", help="使用配置中的 MySQL 付款台账核对合同余额")
    parser.add_argument("--contract-id", help="付款核对的合同编号")
    parser.add_argument("--billing-period", help="付款核对的计费周期")
    run_mode = parser.add_mutually_exclusive_group()
    run_mode.add_argument("--interactive", action="store_true", help="证据不足时等待一份补充文档")
    run_mode.add_argument("--persist", action="store_true", help="将新任务的检查点保存到 MySQL")
    run_mode.add_argument("--resume", metavar="任务编号或恢复回执", help="读取或恢复已有 MySQL 任务")
    resume_action = parser.add_mutually_exclusive_group()
    resume_action.add_argument("--supplement", type=Path, help="使用恢复回执提交一份 UTF-8 补充文档")
    resume_action.add_argument("--finish", action="store_true", help="使用恢复回执以已有证据结束多步任务")
    args = parser.parse_args()

    if args.hybrid and not args.live:
        parser.error("--hybrid 需要同时使用 --live 复核语义候选。")
    if args.multistep and not args.live:
        parser.error("--multistep 需要同时使用 --live。")
    if (args.adaptive or args.web or args.query_source) and not args.live:
        parser.error("按证据补查、网页和 MySQL 查询需要同时使用 --live。")
    if args.payment_source and (args.adaptive or args.web or args.query_source):
        parser.error("固定付款核对与开放检索需要分别创建任务。")
    if args.include_subdirectories and args.directory is None:
        parser.error("--include-subdirectories 需要和 --directory 一起使用。")
    if args.payment_source is None and (args.contract_id or args.billing_period):
        parser.error("请同时选择 --payment-source。")
    if args.payment_source is not None and not args.live:
        parser.error("--payment-source 需要同时使用 --live。")
    try:
        project = normalize_project(args.project)
    except ValueError as error:
        parser.error(str(error))
    if project is not None:
        if not args.live or args.question is None or args.interactive or args.resume is not None:
            parser.error("新项目任务请使用 --project、--live 和 --question。续跑沿用保存的项目名。")
    persistent = args.persist or args.resume is not None or project is not None
    thread_id = uuid4().hex
    receipt = ""
    if args.resume is not None:
        task_id, separator, receipt = args.resume.partition(":")
        try:
            thread_id = UUID(task_id).hex
        except ValueError:
            parser.error("--resume 需要有效的任务编号或恢复回执。")
        if (args.file is not None or args.directory is not None or args.question is not None
                or args.hybrid or args.multistep or args.payment_source is not None
                or args.adaptive or args.web or args.query_source):
            parser.error("续跑沿用已保存的问题、原始文档和检索方式。")
        if (separator and not receipt) or bool(receipt) != (args.supplement is not None or args.finish):
            parser.error("请同时提供完整恢复回执和 --supplement 或 --finish。")
    elif args.supplement is not None or args.finish:
        parser.error("--supplement 和 --finish 均需要和 --resume 的完整恢复回执一起使用。")

    if (args.file is not None or args.directory is not None) and args.question is None:
        parser.error("使用 --file 或 --directory 时请同时提供 --question。")
    question = "试点工期" if args.question is None else args.question.strip()
    if not question:
        parser.error("问题不能为空。")

    try:
        mode = "live" if args.live else "offline"
        payment = payment_scope(args.payment_source, args.contract_id, args.billing_period) if args.payment_source else None
        query_source = query_sources().get(args.query_source) if args.query_source else None
        if args.query_source and query_source is None:
            raise ValueError("请选择已配置的 MySQL 查询表。")
        if args.hybrid:
            from .retrieval import prepare_embedder

            prepare_embedder()
        config = None
        if persistent or args.interactive:
            config = {"configurable": {"thread_id": thread_id}}
        if persistent:
            checkpoint_context = mysql_checkpointer(thread_id)
        elif args.interactive:
            checkpoint_context = nullcontext(InMemorySaver(serde=checkpoint_serializer()))
        else:
            checkpoint_context = nullcontext()

        with tracing_context(enabled=False), checkpoint_context as checkpointer:
            graph = task_graph(mode, checkpointer)
            if args.resume is not None:
                state = _resume_saved_task(graph, config, receipt, args.supplement, mode, finish=args.finish)
                if state is None:
                    return 0
            else:
                directory = None
                directory_evidence = ()
                if args.directory is not None:
                    directory, directory_evidence = load_directory(args.directory, recursive=args.include_subdirectories)
                if args.file is not None:
                    document = load_evidence(args.file)
                elif directory is not None or project is not None or payment is not None or args.web or query_source:
                    document = ()
                else:
                    document = evidence_from_text(SAMPLE_DOCUMENT, "内置示例")
                if persistent:
                    print(f"任务编号：{thread_id}", flush=True)
                state = graph.invoke(
                    initial_state(
                        question, (*document, *directory_evidence), mode=mode, hybrid=args.hybrid,
                        project=project, multistep=args.multistep or payment is not None, directory=directory,
                        payment=payment,
                        adaptive=args.adaptive, web_enabled=args.web, query_source=query_source,
                    ),
                    config,
                    durability="sync" if persistent else None,
                )
            while pending := state.get("__interrupt__"):
                if persistent:
                    _show_waiting_task(graph.get_state(config))
                    return 0
                reply = _read_supplement(pending[0].value)
                print()
                state = graph.invoke(Command(resume=reply), config)
        print(report_from_state(state))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"运行失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
