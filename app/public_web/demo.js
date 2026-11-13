const start = document.querySelector("#start");
const supplement = document.querySelector("#supplement");
const finish = document.querySelector("#finish");
const buttons = [start, supplement, finish];
const statusLabel = document.querySelector("#status");
const stepsList = document.querySelector("#steps");
const promptBox = document.querySelector("#prompt");
const attachment = document.querySelector("#attachment");
const errorBox = document.querySelector("#error");
const reportBox = document.querySelector("#report");
const reportContent = document.querySelector("#report-content");
const download = document.querySelector("#download");
let downloadUrl;

const labels = {
  supported: "直接支持",
  insufficient: "证据不足",
  blocked: "依赖未完成",
};

function renderStep(step) {
  const row = document.createElement("li");
  const question = document.createElement("strong");
  question.textContent = step.question;
  const state = document.createElement("span");
  state.className = "step-status";
  state.dataset.state = step.status;
  state.textContent = labels[step.status] || step.status;
  row.append(question, state);
  if (step.claim || step.depends_on.length) {
    const detail = document.createElement("p");
    detail.className = "step-detail";
    detail.textContent = step.claim
      ? step.claim.text + "（" + step.claim.evidence_ids.join("、") + "）"
      : "依赖：" + step.depends_on.join("、");
    row.append(detail);
  }
  return row;
}

function render(result, action) {
  const waiting = result.status === "awaiting_input";
  statusLabel.textContent = waiting ? "等待补证" : action === "finish" ? "部分报告已生成" : "分析完成";
  stepsList.replaceChildren(...result.steps.map(renderStep));
  stepsList.hidden = false;
  promptBox.textContent = result.prompt || "";
  promptBox.hidden = !waiting;
  attachment.hidden = action !== "supplement";
  supplement.hidden = !waiting;
  finish.hidden = !waiting;
  start.textContent = "重新开始";
  reportBox.hidden = !result.report;
  reportBox.open = false;
  reportContent.textContent = result.report || "";
  download.hidden = !result.report;
  if (downloadUrl) URL.revokeObjectURL(downloadUrl);
  downloadUrl = result.report
    ? URL.createObjectURL(new Blob([result.report], { type: "text/markdown;charset=utf-8" }))
    : undefined;
  if (downloadUrl) download.href = downloadUrl;
  else download.removeAttribute("href");
}

async function analyze(action) {
  const previousStatus = statusLabel.textContent;
  statusLabel.textContent = "分析中…";
  errorBox.hidden = true;
  buttons.forEach(button => { button.disabled = true; });
  try {
    const response = await fetch("/demo/" + action, { signal: AbortSignal.timeout(90000) });
    if (!response.ok) throw new Error("Request failed");
    render(await response.json(), action);
  } catch {
    statusLabel.textContent = previousStatus;
    errorBox.textContent = "暂时无法完成分析。请稍后重试。";
    errorBox.hidden = false;
  } finally {
    buttons.forEach(button => { button.disabled = false; });
  }
}

start.addEventListener("click", () => analyze("start"));
supplement.addEventListener("click", () => analyze("supplement"));
finish.addEventListener("click", () => analyze("finish"));
