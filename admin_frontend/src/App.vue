<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from './api'
import McpServers from './McpServers.vue'
import ModelSettings from './ModelSettings.vue'

const tabs = [
  { id: 'overview', label: '运行概览', icon: '◫' },
  { id: 'mcp', label: 'MCP 服务器', icon: '◇' },
  { id: 'configuration', label: '模型配置', icon: '⚙' },
  { id: 'history', label: '对话日志', icon: '☷' },
]
const config = ref(null)
const status = ref(null)
const error = ref('')
const view = ref('overview')
const history = ref([])
const cursor = ref(null)
const historyLoading = ref(false)
const statusError = ref('')
const historyError = ref('')
let timer
const active = computed(() => config.value?.settings[config.value.mode === 's2s' ? 's2s' : 'llm'])
const currentMode = computed(() => (status.value?.mode ?? config.value?.mode) === 's2s' ? '实时语音 S2S' : '串联模式')
const ready = computed(() => (status.value?.missing ?? config.value?.missing)?.length === 0)
const uptime = computed(() => {
  const seconds = status.value?.uptime_seconds
  if (seconds === undefined) return '—'
  if (seconds < 60) return seconds + ' 秒'
  const minutes = Math.floor(seconds / 60)
  return minutes < 60 ? minutes + ' 分钟' : Math.floor(minutes / 60) + ' 小时 ' + minutes % 60 + ' 分钟'
})
const outcome = { completed: '完成', partial: '部分完成', ended: '已结束对话', failed: '失败', interrupted: '中断' }
const formatter = new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'medium' })
const seconds = value => (value / 1000).toFixed(2) + ' 秒'

async function loadStatus() {
  try {
    status.value = await api('status')
    statusError.value = ''
  } catch {
    statusError.value = '服务端状态暂时不可用。'
  }
}

async function loadHistory(older = false) {
  if (historyLoading.value || (older && cursor.value === null)) return
  historyLoading.value = true
  historyError.value = ''
  try {
    const result = await api('conversations' + (older ? '?before=' + cursor.value : ''))
    history.value = older ? [...history.value, ...result.items] : result.items
    cursor.value = result.next_before
  } catch (err) {
    historyError.value = err.message
  } finally {
    historyLoading.value = false
  }
}

function navigate(id) {
  view.value = id
  if (id === 'overview') loadStatus()
  if (id === 'history') loadHistory()
}

function saved(result) {
  config.value = result
  loadStatus()
}

onMounted(async () => {
  try {
    config.value = await api('config')
    navigate(config.value.missing.length ? 'configuration' : 'overview')
    timer = window.setInterval(() => { if (view.value === 'overview') loadStatus() }, 5000)
  } catch (err) {
    error.value = err.message
  }
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="workspace">
    <aside class="sidebar">
      <div class="brand"><span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></span>小美语音</div>
      <nav aria-label="管理台">
        <button v-for="tab in tabs" :key="tab.id" type="button" :aria-current="view === tab.id ? 'page' : undefined"
                @click="navigate(tab.id)">
          <span aria-hidden="true" class="nav-icon">{{ tab.icon }}</span>{{ tab.label }}
        </button>
      </nav>
    </aside>
    <main>
      <header class="page-header">
        <h1>{{ tabs.find(tab => tab.id === view).label }}</h1>
        <el-tag v-if="config" round effect="light" :type="ready ? 'success' : 'warning'">
          {{ currentMode }} · {{ ready ? '配置齐全' : '待配置' }}
        </el-tag>
      </header>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
      <p v-else-if="!config" role="status">正在读取配置…</p>
      <template v-else>
        <section v-if="view === 'overview'" class="stack">
          <el-alert v-if="statusError" :title="statusError" type="error" show-icon :closable="false" />
          <div class="metrics">
            <el-card shadow="never" v-for="[label, value] in [
              ['运行时长', uptime], ['实时会话', status?.active_sessions ?? '—'],
              ['处理中', status?.active_turns ?? '—'], ['日志存储', status ? (status.journal_ok ? '正常' : '写入失败') : '—'],
            ]" :key="label">
              <div class="muted">{{ label }}</div><strong>{{ value }}</strong>
            </el-card>
          </div>
          <el-card shadow="never">
            <template #header><h2>当前配置</h2></template>
            <dl class="details-grid">
              <div><dt>模型</dt><dd>{{ active?.model || '未配置' }}</dd></div>
              <div><dt>API Key</dt><dd>{{ config.saved_credentials[config.mode === 's2s' ? 's2s.api_key' : 'llm.api_key'] ? '已配置' : '未配置' }}</dd></div>
              <div><dt>服务地址</dt><dd>{{ active?.url || (config.mode === 's2s' && active?.workspace_id ? active.workspace_id + ' · ' + active.region : '未配置') }}</dd></div>
              <div><dt>MCP 服务器</dt><dd>{{ config.mcp_servers?.filter(server => server.enabled).length ?? 0 }} 台已启用</dd></div>
            </dl>
          </el-card>
        </section>
        <McpServers v-if="view === 'mcp'" :servers="config.mcp_servers" @update="config.mcp_servers = $event" />
        <ModelSettings v-if="view === 'configuration'" :config="config" @saved="saved" />
        <el-card v-if="view === 'history'" shadow="never">
          <template #header>
            <div class="row"><h2>会话记录</h2><el-button :loading="historyLoading" @click="loadHistory()">刷新</el-button></div>
          </template>
          <el-alert v-if="historyError" :title="historyError" type="error" show-icon :closable="false" />
          <p v-if="!history.length && !historyLoading && !historyError" class="muted">还没有对话记录。</p>
          <article v-for="item in history" :key="item.id" class="log">
            <div class="row log-meta">
              <span>{{ formatter.format(new Date(item.started_at)) }} · {{ item.device_id }} · {{ item.mode === 's2s' ? 'S2S' : '串联' }} · 服务端处理 {{ seconds(item.duration_ms) }}</span>
              <el-tag size="small" :type="item.outcome === 'failed' ? 'danger' : 'info'">{{ outcome[item.outcome] || item.outcome }}</el-tag>
            </div>
            <div v-if="item.timings && Object.keys(item.timings).length" class="log-timings">
              <span v-if="item.timings.first_audio_ms !== undefined">首段语音 {{ seconds(item.timings.first_audio_ms) }}</span>
              <span v-if="item.timings.mcp_calls">MCP {{ seconds(item.timings.mcp_ms) }} · {{ item.timings.mcp_calls }} 次调用</span>
              <details>
                <summary>分段耗时</summary>
                <dl class="details-grid">
                  <div><dt>会话准备</dt><dd>{{ item.timings.session_setup_ms === 0 ? '复用已有连接' : seconds(item.timings.session_setup_ms) }}</dd></div>
                  <div><dt>接收本轮音频</dt><dd>{{ seconds(item.timings.input_ms) }}</dd></div>
                  <div><dt>输入音频长度</dt><dd>{{ seconds(item.timings.input_audio_ms) }}</dd></div>
                  <div v-if="item.timings.first_text_ms !== undefined"><dt>首段文字</dt><dd>{{ seconds(item.timings.first_text_ms) }}</dd></div>
                </dl>
              </details>
            </div>
            <p><strong>用户</strong> {{ item.user_text || '无转写' }}</p>
            <p v-if="item.reply"><strong>小美</strong> {{ item.reply }}</p>
            <p v-if="item.stage" class="muted">{{ item.stage }}</p>
          </article>
          <el-button v-if="cursor !== null" :loading="historyLoading" @click="loadHistory(true)">加载更早记录</el-button>
        </el-card>
      </template>
    </main>
  </div>
</template>
