"use strict";

const createForm = document.querySelector("#create-form");
const lookupForm = document.querySelector("#lookup-form");
const clarificationForm = document.querySelector("#clarification-form");
const supplementForm = document.querySelector("#supplement-form");
const liveOptions = document.querySelector("#live-options");
const taskIdInput = document.querySelector("#task-id");
const statusBadge = document.querySelector("#status");
const errorMessage = document.querySelector("#error");
const retryButton = document.querySelector("#retry");
const downloadReport = document.querySelector("#download-report");
const reportPreview = document.querySelector("#report");
const reportSection = document.querySelector("#report-section");
const buttons = document.querySelectorAll("#workspace button");
const lastTaskKey = "analysis-agent.last-task";
const taskIdPattern = /^(?:[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})$/i;

const taskLabels = {
  clarifying: "等待确认范围",
  awaiting_input: "等待补证",
  incomplete: "待继续",
  completed: "已完成",
};
const reviewLabels = {
  supported: "直接支持",
  inference: "有依据的推断",
  insufficient: "证据不足",
  conflicting: "证据冲突",
};
const stepLabels = {
  ...reviewLabels,
  pending: "待执行",
  blocked: "等待依赖",
  partial: "已汇总部分结果",
};

let currentTask = null;
let busy = false;

function updateMode() {
  const live = createForm.elements.mode.value === "live";
  liveOptions.hidden = !live;
  liveOptions.disabled = !live;
}

function updatePaymentFields() {
  const enabled = Boolean(createForm.elements.payment_source.value);
  const fields = document.querySelector("#payment-fields");
  fields.hidden = !enabled;
  fields.disabled = !enabled;
  const adaptiveFields = document.querySelector("#adaptive-fields");
  adaptiveFields.hidden = enabled;
  adaptiveFields.disabled = enabled;
}

function updateDirectory() {
  createForm.elements.include_subdirectories.disabled = !createForm.elements.directory.value.trim();
}

function displayStatus(label = null) {
  statusBadge.dataset.state = label ? "busy" : currentTask?.status ?? "empty";
  statusBadge.textContent = label ?? (currentTask ? taskLabels[currentTask.status] : "未选择");
}

function taskPath(taskId) {
  return `/tasks/${encodeURIComponent(taskId)}`;
}

async function request(path, options) {
  let response;
  try {
    response = await fetch(path, {cache: "no-store", ...options});
  } catch {
    throw new Error("无法连接本地分析服务，请确认服务已启动后再试。");
  }
  if (response.ok) return response;

  const body = await response.json().catch(() => ({}));
  const detail = body.detail;
  const fallback = `请求失败（HTTP ${response.status}）。`;
  const message = typeof detail === "string" ? detail : detail?.message;
  const error = new Error(message ?? fallback);
  error.taskId = detail?.task_id;
  throw error;
}

async function loadSources(path, field, hintId, empty) {
  const hint = document.querySelector(hintId);
  try {
    const response = await request(path);
    const sources = await response.json();
    const select = createForm.elements[field];
    for (const source of sources) {
      const option = document.createElement("option");
      option.value = source.id;
      option.textContent = source.label;
      select.append(option);
    }
    if (sources.length === 0) {
      hint.textContent = empty;
      hint.hidden = false;
    }
  } catch (error) {
    hint.textContent = error.message;
    hint.hidden = false;
  }
}

function rememberTaskId(taskId) {
  const url = new URL(window.location.href);
  url.searchParams.set("task", taskId);
  window.history.replaceState(null, "", url);
  try {
    window.localStorage.setItem(lastTaskKey, taskId);
  } catch {
    // 任务编号仍可通过地址栏恢复。
  }
}

function showClarification(task, inputChanged) {
  const history = task.clarification_turns ?? [];
  document.querySelector("#clarification-history").hidden = history.length === 0;
  document.querySelector("#history-title").textContent = `澄清记录（${history.length} 轮）`;
  const historyItems = history.map((turn) => {
    const item = document.createElement("li");
    const prompt = document.createElement("p");
    prompt.textContent = turn.prompt;
    const answer = document.createElement("p");
    answer.textContent = [turn.direction, turn.answer].filter(Boolean).join(" · ");
    item.append(prompt, answer);
    return item;
  });
  document.querySelector("#history-turns").replaceChildren(...historyItems);

  document.querySelector("#confirmed-scope-section").hidden = !task.confirmed_scope;
  document.querySelector("#confirmed-scope").textContent = task.confirmed_scope ?? "";
  const proposal = task.clarification;
  clarificationForm.hidden = !proposal;
  if (!proposal) return;

  document.querySelector("#clarification-prompt").textContent = proposal.prompt;
  document.querySelector("#direction-field").hidden = proposal.directions.length === 0;
  document.querySelector("#proposed-scope-section").hidden = !proposal.scope;
  document.querySelector("#proposed-scope").textContent = proposal.scope ?? "";
  document.querySelector("#confirm-scope").hidden = !proposal.scope;
  if (!inputChanged) return;

  clarificationForm.reset();
  const labels = [...proposal.directions, "暂不选择"];
  const choices = labels.map((direction, index) => {
    const label = document.createElement("label");
    const radio = document.createElement("input");
    const skip = index === proposal.directions.length;
    radio.type = "radio";
    radio.name = "direction";
    radio.value = skip ? "" : String(index);
    radio.defaultChecked = skip;
    label.append(radio, document.createTextNode(direction));
    return label;
  });
  document.querySelector("#direction-options").replaceChildren(...choices);
}

function showSteps(steps) {
  document.querySelector("#steps-section").hidden = steps.length === 0;
  const items = steps.map((step) => {
    const item = document.createElement("li");
    item.dataset.state = step.status;
    const heading = document.createElement("div");
    heading.className = "step-line";
    const name = document.createElement("strong");
    name.textContent = step.id === "summary" ? step.question : `${step.id} · ${step.question}`;
    const badge = document.createElement("span");
    badge.className = "step-state";
    badge.textContent = step.id === "summary" && step.status === "supported" ? "已汇总" : stepLabels[step.status];
    heading.append(name, badge);
    item.append(heading);
    if (step.depends_on.length) {
      const dependencies = document.createElement("p");
      dependencies.className = "hint";
      dependencies.textContent = `依赖 ${step.depends_on.join("、")}`;
      item.append(dependencies);
    }
    if (step.claim) {
      const claim = document.createElement("p");
      claim.className = "step-claim";
      claim.textContent = `${step.claim} [${step.references.join(", ")}]`;
      item.append(claim);
    }
    return item;
  });
  document.querySelector("#steps").replaceChildren(...items);
}

async function showTask(task) {
  const inputChanged = currentTask?.task_id !== task.task_id || currentTask?.receipt !== task.receipt;
  if (inputChanged) {
    supplementForm.reset();
  }
  currentTask = task;
  rememberTaskId(task.task_id);
  taskIdInput.value = task.task_id;
  document.querySelector("#empty").hidden = true;
  document.querySelector("#task-detail").hidden = false;
  document.querySelector("#task-question").textContent = task.question;
  document.querySelector("#task-meta").textContent = [
    task.mode === "live" ? "DeepSeek" : "离线摘录",
    task.hybrid ? "混合检索" : "关键词检索",
    task.project ? `项目 ${task.project}` : "",
    reviewLabels[task.review_status],
  ].filter(Boolean).join(" · ");
  const directorySummary = document.querySelector("#directory-summary");
  const directory = task.directory;
  directorySummary.hidden = !directory;
  if (directory) {
    const scope = directory.recursive ? "包含子目录" : "仅当前目录";
    const skipped = Object.entries(directory.skipped).map(([reason, count]) => `${reason} ${count} 项`);
    directorySummary.textContent = [directory.path, `读取 ${directory.file_count} 份 · ${scope}`, ...skipped].join(" · ");
  }
  const paymentSummary = document.querySelector("#payment-summary");
  const payment = task.payment;
  paymentSummary.hidden = !payment;
  if (payment) {
    paymentSummary.textContent = `${payment.source.label} · ${payment.source.database}.${payment.source.table} · 合同 ${payment.contract_id} · 周期 ${payment.billing_period}`;
  }
  const adaptiveSummary = document.querySelector("#adaptive-summary");
  adaptiveSummary.hidden = !task.adaptive;
  const query = task.query_source;
  adaptiveSummary.textContent = [task.adaptive ? "根据证据补查" : "", task.web_enabled ? "公开网页" : "",
    query ? `${query.database}.${query.table}` : ""].filter(Boolean).join(" · ");
  const observations = task.observations ?? [];
  document.querySelector("#tool-history").hidden = observations.length === 0;
  document.querySelector("#tool-observations").replaceChildren(...observations.map((item) => {
    const row = document.createElement("li");
    const phase = item.phase === "plan" ? "规划" : `执行 ${item.step_id}`;
    row.textContent = `${phase} · ${item.action.tool} · ${item.result}`;
    return row;
  }));

  showClarification(task, inputChanged);
  showSteps(task.steps ?? []);
  supplementForm.hidden = task.status !== "awaiting_input";
  document.querySelector("#finish-with-evidence").hidden = !task.can_finish;
  document.querySelector("#supplement-prompt").textContent = task.prompt ?? "";
  document.querySelector("#retry-section").hidden = task.status !== "incomplete";
  reportSection.hidden = !task.report_url;
  downloadReport.removeAttribute("href");
  downloadReport.hidden = true;
  reportPreview.textContent = "";
  if (task.report_url) {
    const reportPath = `${taskPath(task.task_id)}/report`;
    downloadReport.href = reportPath;
    downloadReport.hidden = false;
    reportPreview.textContent = "正在加载报告…";
    try {
      const response = await request(reportPath);
      reportPreview.textContent = await response.text();
    } catch (error) {
      reportPreview.textContent = "报告预览加载失败。";
      throw error;
    }
  }
}

async function loadTask(taskId) {
  if (!taskIdPattern.test(taskId)) throw new Error("请输入有效的任务编号。");
  const response = await request(taskPath(taskId));
  await showTask(await response.json());
}

async function run(label, action) {
  if (busy) return;
  busy = true;
  errorMessage.hidden = true;
  buttons.forEach((button) => { button.disabled = true; });
  displayStatus(label);

  try {
    await action();
  } catch (error) {
    if (error.taskId) {
      try {
        await loadTask(error.taskId);
      } catch {
        taskIdInput.value = error.taskId;
      }
    }
    errorMessage.textContent = error.message;
    errorMessage.hidden = false;
  } finally {
    busy = false;
    buttons.forEach((button) => { button.disabled = false; });
    displayStatus();
  }
}

createForm.addEventListener("change", updateMode);
createForm.elements.payment_source.addEventListener("change", updatePaymentFields);
createForm.elements.directory.addEventListener("input", updateDirectory);
createForm.addEventListener("submit", (event) => {
  event.preventDefault();
  run("正在分析…", async () => {
    const question = createForm.elements.question.value.trim();
    if (!question) throw new Error("请输入分析问题。");
    const live = createForm.elements.mode.value === "live";
    const data = new FormData();
    data.set("question", question);
    data.set("live", String(live));
    data.set("clarify", String(createForm.elements.clarify.checked));
    const file = createForm.elements.file.files[0];
    if (file) data.set("file", file);
    const directory = createForm.elements.directory.value.trim();
    if (directory) {
      data.set("directory", directory);
      data.set("include_subdirectories", String(createForm.elements.include_subdirectories.checked));
    }
    if (live) {
      data.set("hybrid", String(createForm.elements.hybrid.checked));
      data.set("multistep", String(createForm.elements.multistep.checked));
      const project = createForm.elements.project.value.trim();
      if (project) data.set("project", project);
      const paymentSource = createForm.elements.payment_source.value;
      if (paymentSource) {
        data.set("payment_source", paymentSource);
        data.set("contract_id", createForm.elements.contract_id.value.trim());
        data.set("billing_period", createForm.elements.billing_period.value.trim());
      } else {
        data.set("adaptive", String(createForm.elements.multistep.checked && createForm.elements.adaptive.checked));
        data.set("web_enabled", String(createForm.elements.web_enabled.checked));
        const querySource = createForm.elements.query_source.value;
        if (querySource) data.set("query_source", querySource);
      }
    }
    const response = await request("/tasks", {method: "POST", body: data});
    await showTask(await response.json());
    if (window.matchMedia("(max-width: 680px)").matches) {
      document.querySelector(".task-panel").scrollIntoView();
    }
  });
});

lookupForm.addEventListener("submit", (event) => {
  event.preventDefault();
  run("正在查询…", () => loadTask(taskIdInput.value.trim()));
});

clarificationForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const action = event.submitter?.value ?? "answer";
  run(action === "confirm" ? "正在分析…" : "正在整理需求…", async () => {
    const data = new FormData(clarificationForm);
    const answer = String(data.get("answer") ?? "").trim();
    const choice = data.get("direction");
    const direction = choice ? Number(choice) : null;
    if (action === "confirm" && (answer || direction !== null)) {
      throw new Error("请先提交选中的方向或补充信息，再确认更新后的范围。");
    }
    const response = await request(`${taskPath(currentTask.task_id)}/clarification`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({receipt: currentTask.receipt, action, answer, direction}),
    });
    await showTask(await response.json());
  });
});

supplementForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const finish = event.submitter?.value === "finish";
  run(finish ? "正在汇总…" : "正在补证…", async () => {
    const data = finish ? new FormData() : new FormData(supplementForm);
    if (finish) data.set("finish", "true");
    data.set("receipt", currentTask.receipt);
    const response = await request(`${taskPath(currentTask.task_id)}/supplement`, {method: "POST", body: data});
    await showTask(await response.json());
  });
});

retryButton.addEventListener("click", () => {
  run("正在继续…", async () => {
    const response = await request(`${taskPath(currentTask.task_id)}/retry`, {method: "POST"});
    await showTask(await response.json());
  });
});

function startupTaskId() {
  const fromUrl = new URL(window.location.href).searchParams.get("task");
  if (fromUrl) return fromUrl;
  try {
    return window.localStorage.getItem(lastTaskKey);
  } catch {
    return null;
  }
}

updateMode();
updateDirectory();
updatePaymentFields();
loadSources("/payment-sources", "payment_source", "#payment-source-hint", "当前没有配置付款台账。");
loadSources("/query-sources", "query_source", "#query-source-hint", "当前没有配置业务表。");
const savedTask = startupTaskId();
if (savedTask) {
  taskIdInput.value = savedTask;
  run("正在恢复…", () => loadTask(savedTask));
}
