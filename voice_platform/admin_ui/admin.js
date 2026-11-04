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
      if (field) field.value = value ?? "";
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
  byId("status").textContent = (data.mode === "s2s" ? "S2S" : "串联") + " · "
    + (data.missing.length ? "待配置" : "配置齐全");
  byId("status").className = "status " + (data.missing.length ? "pending" : "ready");
  form.hidden = false;
  updateFields();
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
    (settings[section] ||= {})[key] = field.value.trim();
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

api("config").then(data => {
  render(data);
  notice(missingText());
}).catch(() => {
  byId("status").textContent = "读取失败";
  byId("load-error").textContent = "无法读取配置。请确认服务端已启动，然后刷新页面。";
  byId("load-error").hidden = false;
});
