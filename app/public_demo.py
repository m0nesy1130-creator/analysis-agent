"""面向访客的固定资料演示，每次请求在独立检查点中执行。"""

from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.memory import InMemorySaver
from langsmith import tracing_context

from .checkpoints import checkpoint_serializer
from .contracts import PlanStep
from .documents import evidence_from_text
from .graphs import build_graph
from .planning import latest_results
from .tasks import (
    finish_with_current_evidence,
    initial_state,
    input_receipt,
    load_snapshot,
    pending_kind,
    report_from_state,
    resume_with_document,
)


QUESTION = "核对维护年费、服务范围和巡检频率。"
PLAN = (
    PlanStep("s1", "维护年费是多少？", (), ("维护年费",)),
    PlanStep("s2", "服务范围包括哪些项目？", (), ("服务范围",)),
    PlanStep("s3", "已确认的巡检服务多久执行一次？", ("s2",), ("巡检频率",)),
)
CONTRACT = evidence_from_text(
    "云帆服务台维护合同（虚构）\n"
    "维护年费为 12,000 元。\n"
    "实际提供的服务项目以签署的附件为准。",
    "维护合同.txt",
)
ATTACHMENT = evidence_from_text(
    "服务清单（虚构）\n"
    "服务范围包括定期巡检和故障排查。\n"
    "巡检频率为每月一次。",
    "服务清单.txt",
)

WEB_DIR = Path(__file__).with_name("public_web")
app = FastAPI(title="analysis-agent 公开演示", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="demo-assets")


@app.get("/")
def home():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/demo/{action}")
def analyze(action: Literal["start", "supplement", "finish"]):
    # 每次从同一份合同复现待补证状态，访客之间没有共享任务。
    graph = build_graph(
        checkpointer=InMemorySaver(serde=checkpoint_serializer()), preset_plan=PLAN,
    )
    config = {"configurable": {"thread_id": "public-demo"}}
    with tracing_context(enabled=False):
        state = initial_state(QUESTION, CONTRACT, mode="offline", hybrid=False, project=None, multistep=True)
        graph.invoke(state, config, durability="sync")
        snapshot = load_snapshot(graph, config)
        if action == "supplement":
            resume_with_document(graph, config, snapshot, input_receipt(snapshot), lambda: ATTACHMENT)
            snapshot = load_snapshot(graph, config)
        elif action == "finish":
            finish_with_current_evidence(graph, config, snapshot, input_receipt(snapshot))
            snapshot = load_snapshot(graph, config)

    waiting = pending_kind(snapshot) == "supplement"
    results = latest_results(snapshot.values["step_results"])
    questions = {step.id: step.question for step in PLAN}
    steps = []
    for step in PLAN:
        result = results.get(step.id)
        steps.append({
            "question": step.question,
            "depends_on": [questions[key] for key in step.depends_on],
            "status": result.review.status if result else "blocked",
            "claim": asdict(result.claim) if result and result.claim else None,
        })
    return {
        "status": "awaiting_input" if waiting else "completed",
        "prompt": snapshot.interrupts[0].value if waiting else None,
        "steps": steps,
        "report": None if waiting else report_from_state(snapshot.values),
    }
