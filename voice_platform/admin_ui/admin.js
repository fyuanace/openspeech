"use strict";

const byId = id => document.getElementById(id);
const form = byId("config");
const input = name => form.elements.namedItem(name);
const qwenUrl = "wss://maas.qianwenaiapi.com/api-ws/v1/realtime";
const missingNames = {
  dashscope_api_key: "千问 API Key",
  dashscope_workspace_id: "实时服务地址或百炼业务空间 ID",
  doubao_credentials: "豆包凭据",
  llm_credentials: "对话模型 API Key",
};
let saved;
let busy = false;
let dirty = false;
let view = "overview";
let nextBefore = null;
let loadingHistory = false;

function notice(text, error = false) {
  byId("result").textContent = text;
  byId("result").classList.toggle("error", error);
}

function updateFields() {
  const mode = input("mode").value;
  byId("s2s-settings").hidden = mode !== "s2s";
  byId("cascade-settings").hidden = mode !== "cascade";
  byId("s2s-settings").disabled = mode !== "s2s";
  byId("cascade-settings").disabled = mode !== "cascade";
  byId("workspace").hidden = byId("platform").value !== "bailian";
  byId("custom-url").hidden = byId("platform").value !== "custom";
  byId("doubao").hidden = input("stt.provider").value !== "doubao";
  byId("stt-key").hidden = byId("stt-auth").value !== "key";
  byId("stt-legacy").hidden = byId("stt-auth").value !== "legacy";
  byId("check").hidden = mode !== "s2s" || saved.mode !== "s2s" || saved.missing.length !== 0 || dirty;
}

function render(data) {
  saved = data;
  dirty = false;
  input("mode").value = data.mode;
  for (const [section, fields] of Object.entries(data.settings)) {
    for (const [key, value] of Object.entries(fields)) {
      const field = input(section + "." + key);
      if (field?.type === "checkbox") field.checked = Boolean(value);
      else if (field) field.value = value ?? "";
    }
  }
  const s2s = data.settings.s2s;
  byId("platform").value = s2s.url && s2s.url !== qwenUrl ? "custom"
    : !s2s.url && s2s.workspace_id ? "bailian" : "qwen";
  input("s2s.model").value ||= "qwen3.8-omni-flash-realtime";
  input("s2s.voice").value ||= "Tina";
  input("s2s.region").value ||= "cn-beijing";
  input("stt.auth_mode").value ||= !data.saved_credentials["stt.api_key"]
    && data.saved_credentials["stt.access_token"] ? "legacy" : "key";

  for (const [name, configured] of Object.entries(data.saved_credentials)) {
    const field = input(name);
    if (!field) continue;
    field.value = "";
    field.disabled = data.environment_fields.includes(name);
    field.placeholder = field.disabled ? "由环境变量管理" : configured ? "已配置，留空保留" : "请输入";
  }
  input("s2s.workspace_id").disabled = data.environment_fields.includes("s2s.workspace_id");
  const active = data.mode === "s2s" ? s2s : data.settings.llm;
  const key = data.mode === "s2s" ? "s2s.api_key" : "llm.api_key";
  byId("active-model").textContent = active.model || "未配置";
  byId("active-key").textContent = data.environment_fields.includes(key) ? "由环境变量管理"
    : data.saved_credentials[key] ? "已配置" : "未配置";
  const workspaceUrl = data.mode === "s2s" && s2s.workspace_id
    ? `wss://${s2s.workspace_id}.${s2s.region || "cn-beijing"}.maas.aliyuncs.com/api-ws/v1/realtime`
    : "";
  byId("active-address").textContent = active.url || workspaceUrl || "未配置";
  byId("active-config").hidden = false;
  setStatus(data);
  form.hidden = false;
  updateFields();
}

function setStatus(data) {
  byId("status").textContent = (data.mode === "s2s" ? "S2S" : "串联") + " · "
    + (data.missing.length ? "待配置" : "配置齐全");
  byId("status").className = "status " + (data.missing.length ? "pending" : "ready");
}

async function api(path, options = {}) {
  const response = await fetch("/admin/api/" + path, { cache: "no-store", ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "请求失败，请检查服务端状态。");
  return data;
}

function missingText() {
  const usesPreset = saved.mode === "s2s" && byId("platform").value === "qwen";
  const missing = saved.missing.filter(key => !usesPreset || key !== "dashscope_workspace_id");
  if (missing.length) return "还需要填写：" + missing.map(key => missingNames[key] || key).join("、");
  if (saved.missing.length) return "请保存所选平台，以应用实时服务地址。";
  return "";
}

form.addEventListener("input", () => {
  dirty = true;
  notice("");
  updateFields();
});

function setBusy(value) {
  busy = value;
  byId("save").disabled = value;
  byId("check").disabled = value;
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (busy) return;
  const mode = input("mode").value;
  const settings = {};
  for (const field of form.querySelectorAll("[name]")) {
    if (!field.name.includes(".") || field.matches(":disabled") || field.closest("[hidden]")) continue;
    const [section, key] = field.name.split(".");
    (settings[section] ||= {})[key] = field.type === "checkbox" ? field.checked : field.value.trim();
  }
  if (mode === "s2s") {
    const platform = byId("platform").value;
    if (platform === "custom" && !settings.s2s.url) {
      notice("请输入自定义实时 WebSocket 地址。", true);
      input("s2s.url").focus();
      return;
    }
    if (platform !== "custom") settings.s2s.url = platform === "qwen" ? qwenUrl : "";
  }
  setBusy(true);
  const previousMode = saved.mode;
  try {
    const data = await api("config", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, settings }),
    });
    render(data);
    const message = missingText() || "新会话将使用这份配置。";
    notice("已保存。" + message + (previousMode !== data.mode ? "切换模式后，请在设备托盘暂停再继续监听。" : ""));
  } catch (error) {
    notice(error.message, true);
  } finally {
    setBusy(false);
  }
});

byId("check").addEventListener("click", async () => {
  if (busy) return;
  setBusy(true);
  notice("正在连接实时模型…");
  try {
    await api("check", { method: "POST" });
    notice("实时模型连接成功。现在可以在设备端唤醒小美。");
  } catch (error) {
    notice(error.message, true);
  } finally {
    setBusy(false);
  }
});

async function loadStatus() {
  try {
    const data = await api("status");
    setStatus(data);
    const minutes = Math.floor(data.uptime_seconds / 60);
    byId("uptime").textContent = minutes < 1 ? data.uptime_seconds + " 秒"
      : minutes < 60 ? minutes + " 分钟" : Math.floor(minutes / 60) + " 小时 " + minutes % 60 + " 分钟";
    byId("sessions").textContent = data.active_sessions;
    byId("turns").textContent = data.active_turns;
    byId("journal").textContent = data.journal_ok ? "正常" : "写入失败";
    byId("status-error").hidden = true;
  } catch (error) {
    byId("status-error").textContent = "服务端状态暂时不可用。";
    byId("status-error").hidden = false;
  }
}

function logEntry(item) {
  const article = document.createElement("article");
  article.className = "log-entry";
  const meta = document.createElement("div");
  meta.className = "log-meta";
  const outcome = { completed: "完成", partial: "部分完成", ended: "已结束对话", failed: "失败", interrupted: "中断" };
  const time = new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "medium" })
    .format(new Date(item.started_at));
  meta.textContent = [time, item.device_id, item.mode === "s2s" ? "S2S" : "串联",
    outcome[item.outcome] || item.outcome, item.stage, (item.duration_ms / 1000).toFixed(1) + " 秒"]
    .filter(Boolean).join(" · ");
  meta.title = "会话 " + item.session_id;
  article.append(meta);
  for (const [label, value] of [["用户", item.user_text || "无转写"], ["小美", item.reply]]) {
    if (!value) continue;
    const line = document.createElement("p");
    const name = document.createElement("strong");
    name.textContent = label + " ";
    line.append(name, value);
    article.append(line);
  }
  return article;
}

async function loadHistory(older = false) {
  if (loadingHistory || (older && nextBefore === null)) return;
  loadingHistory = true;
  byId("refresh-history").disabled = true;
  byId("more-history").disabled = true;
  try {
    const data = await api("conversations" + (older ? "?before=" + nextBefore : ""));
    const list = byId("history-items");
    if (!older) list.replaceChildren();
    list.append(...data.items.map(logEntry));
    byId("history-message").textContent = list.children.length ? "" : "还没有对话记录。";
    nextBefore = data.next_before;
    byId("more-history").hidden = nextBefore === null;
  } catch (error) {
    byId("history-message").textContent = error.message;
  } finally {
    loadingHistory = false;
    byId("refresh-history").disabled = false;
    byId("more-history").disabled = false;
  }
}

function showView(name) {
  view = name;
  for (const button of byId("views").querySelectorAll("[data-view]")) {
    const selected = button.dataset.view === name;
    button.setAttribute("aria-pressed", String(selected));
    byId(button.dataset.view).hidden = !selected;
  }
  if (name === "overview") loadStatus();
  if (name === "history") loadHistory();
}

for (const button of byId("views").querySelectorAll("[data-view]")) {
  button.addEventListener("click", () => showView(button.dataset.view));
}
byId("refresh-history").addEventListener("click", () => loadHistory());
byId("more-history").addEventListener("click", () => loadHistory(true));
setInterval(() => { if (view === "overview") loadStatus(); }, 5000);

api("config").then(data => {
  render(data);
  notice(missingText());
  byId("views").hidden = false;
  showView(data.missing.length ? "configuration" : "overview");
}).catch(() => {
  byId("status").textContent = "读取失败";
  byId("load-error").textContent = "无法读取配置。请确认服务端已启动，然后刷新页面。";
  byId("load-error").hidden = false;
});
