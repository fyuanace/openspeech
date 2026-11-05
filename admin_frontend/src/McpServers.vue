<script setup>
import { computed, ref } from 'vue'
import { api } from './api'

const props = defineProps({ servers: Array })
const emit = defineEmits(['update'])
const dialogOpen = ref(false)
const draft = ref(null)
const discovered = ref([])
const busy = ref('')
const notice = ref('')
const error = ref('')
const current = computed(() => props.servers?.find(server => server.id === draft.value?.id))
const connectionChanged = computed(() => !current.value || draft.value.url.trim() !== current.value.url
  || draft.value.token.trim() || draft.value.clearToken)
const catalog = computed(() => {
  const entries = new Map(discovered.value.map(tool => [tool.name, tool]))
  for (const name of draft.value?.tools ?? []) if (!entries.has(name)) entries.set(name, { name })
  return [...entries.values()]
})

function edit(server = null) {
  draft.value = {
    id: server?.id, name: server?.name ?? '', url: server?.url ?? '',
    enabled: server?.enabled ?? true, tools: [...(server?.tools ?? [])], token: '', clearToken: false,
  }
  discovered.value = []
  notice.value = ''
  error.value = ''
  dialogOpen.value = true
}

async function perform(action, handler) {
  if (busy.value) return
  busy.value = action
  error.value = ''
  notice.value = ''
  try {
    await handler()
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = ''
  }
}

function save() {
  return perform('save', async () => {
    const { id, name, url, enabled, tools, token, clearToken } = draft.value
    const needsDiscovery = Boolean(connectionChanged.value)
    const result = await api('mcp/servers' + (id ? '/' + id : ''), id ? 'PUT' : 'POST',
      { name, url, enabled, tools: tools.join(' '), token, clear_token: clearToken })
    emit('update', result.servers)
    const server = result.servers.find(item => item.id === result.id)
    if (needsDiscovery) {
      edit(server)
      notice.value = '连接已保存，可以发现工具。'
    } else {
      dialogOpen.value = false
      notice.value = '已保存，新对话将使用此配置。'
    }
  })
}

function discover() {
  return perform('discover', async () => {
    const result = await api('mcp/servers/' + draft.value.id + '/tools', 'POST')
    discovered.value = result.tools
    notice.value = result.tools.length ? '已发现 ' + result.tools.length + ' 个工具。' : '该服务器没有返回可用工具。'
  })
}

function remove() {
  return perform('delete', async () => {
    const result = await api('mcp/servers/' + draft.value.id, 'DELETE')
    emit('update', result.servers)
    dialogOpen.value = false
    notice.value = '已移除 MCP 服务器。'
  })
}
</script>

<template>
  <section v-if="servers" class="stack">
    <el-card shadow="never">
      <template #header>
        <div class="row"><h2>已配置服务器 <span class="count">{{ servers.length }}</span></h2>
          <el-button type="primary" :disabled="Boolean(busy)" @click="edit()">添加服务器</el-button>
        </div>
      </template>
      <p v-if="!servers.length" class="empty muted">还没有配置 MCP 服务器。</p>
      <div v-else class="server-list">
        <button v-for="server in servers" :key="server.id" type="button" class="server-row"
                :disabled="Boolean(busy)" aria-haspopup="dialog" @click="edit(server)">
          <span class="server-icon" aria-hidden="true">◇</span>
          <span class="server-identity"><strong>{{ server.name }}</strong><span class="muted">{{ server.url }}</span></span>
          <el-tag :type="server.enabled ? 'success' : 'info'" effect="light">
            {{ server.enabled ? server.tools.length + ' 个工具' : '已停用' }}
          </el-tag>
        </button>
      </div>
    </el-card>
    <el-alert v-if="notice && !dialogOpen" :title="notice" type="success" show-icon :closable="false" />
    <el-dialog v-model="dialogOpen" class="mcp-dialog" append-to-body align-center destroy-on-close
               width="min(680px, calc(100% - 24px))" :title="draft?.id ? '编辑服务器' : '添加 MCP 服务器'"
               :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy">
      <el-form v-if="draft" id="mcp-server-form" label-position="top" :disabled="Boolean(busy)" @submit.prevent="save">
        <div class="fields">
          <el-form-item label="名称"><el-input v-model="draft.name" maxlength="80" /></el-form-item>
          <el-form-item label="服务地址"><el-input v-model="draft.url" type="url" placeholder="https://服务地址/mcp" /></el-form-item>
          <el-form-item label="Bearer 令牌">
            <el-input v-model="draft.token" type="password" autocomplete="new-password"
                      :placeholder="current?.has_token ? '已保存，留空保留' : '选填'" />
            <el-checkbox v-if="current?.has_token" v-model="draft.clearToken">清除已保存的令牌</el-checkbox>
          </el-form-item>
          <el-form-item label="启用服务器"><el-switch v-model="draft.enabled" aria-label="启用服务器" /></el-form-item>
        </div>
        <div v-if="draft.id" class="tool-section">
          <div class="row">
            <h3>工具 <span class="count">{{ draft.tools.length }} 已选</span></h3>
            <el-button :disabled="Boolean(connectionChanged)" :loading="busy === 'discover'" @click="discover">发现工具</el-button>
          </div>
          <p v-if="connectionChanged" class="muted">保存连接设置后可重新发现工具。</p>
          <p v-else-if="!catalog.length" class="muted">发现并勾选要开放给小美的工具。</p>
          <div v-for="tool in (connectionChanged ? [] : catalog)" :key="tool.name" class="tool-row">
            <el-checkbox :model-value="draft.tools.includes(tool.name)" :aria-label="tool.name"
                         :disabled="draft.tools.length >= 16 && !draft.tools.includes(tool.name)"
                         @change="checked => draft.tools = checked ? [...draft.tools, tool.name] : draft.tools.filter(name => name !== tool.name)">
              <strong>{{ tool.name }}</strong><span v-if="tool.description" class="muted">{{ tool.description }}</span>
            </el-checkbox>
          </div>
        </div>
        <el-alert v-if="error || notice" class="form-notice" :title="error || notice" :type="error ? 'error' : 'success'" show-icon :closable="false" />
      </el-form>
      <template #footer>
        <div class="mcp-dialog-actions">
          <el-button v-if="draft?.id" type="danger" text :loading="busy === 'delete'"
                     :disabled="Boolean(busy)" @click="remove">移除服务器</el-button>
          <div class="mcp-dialog-primary">
            <el-button :disabled="Boolean(busy)" @click="dialogOpen = false">取消</el-button>
            <el-button type="primary" native-type="submit" form="mcp-server-form" :loading="busy === 'save'"
                       :disabled="Boolean(busy)">保存服务器</el-button>
          </div>
        </div>
      </template>
    </el-dialog>
  </section>
  <el-alert v-else title="当前服务端需要重启以加载新版 MCP 配置接口。" type="warning" show-icon :closable="false" />
</template>
