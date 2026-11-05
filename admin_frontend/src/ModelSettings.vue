<script setup>
import { computed, ref, watch } from 'vue'
import { api } from './api'

const props = defineProps({ config: Object })
const emit = defineEmits(['saved'])
const qwenUrl = 'wss://maas.qianwenaiapi.com/api-ws/v1/realtime'
const mode = ref('s2s')
const platform = ref('qwen')
const draft = ref({})
const busy = ref('')
const message = ref('')
const error = ref('')
const dirty = ref(false)
const canCheck = computed(() => !dirty.value && props.config.mode === 's2s' && !props.config.missing.length)
const missingNames = {
  dashscope_api_key: '千问 API Key',
  dashscope_workspace_id: '实时服务地址或百炼业务空间 ID',
  doubao_credentials: '豆包凭据',
  llm_credentials: '对话模型 API Key',
}

function render(config) {
  mode.value = config.mode
  draft.value = Object.fromEntries(Object.entries(config.settings).map(([name, settings]) => [name, { ...settings }]))
  const s2s = draft.value.s2s
  platform.value = s2s.url && s2s.url !== qwenUrl ? 'custom' : !s2s.url && s2s.workspace_id ? 'bailian' : 'qwen'
  s2s.model ||= 'qwen3.8-omni-flash-realtime'
  s2s.voice ||= 'Tina'
  s2s.region ||= 'cn-beijing'
  draft.value.stt.auth_mode ||= config.saved_credentials['stt.access_token'] ? 'legacy' : 'key'
  dirty.value = false
}

watch(() => props.config, render, { immediate: true })
function managed(field) { return props.config.environment_fields.includes(field) }
function keyHint(field) {
  return managed(field) ? '由环境变量管理' : props.config.saved_credentials[field] ? '已保存，留空保留' : '请输入'
}
function changed() {
  dirty.value = true
  error.value = ''
  message.value = ''
}

async function save() {
  if (busy.value) return
  busy.value = 'save'
  const before = props.config.mode
  error.value = ''
  try {
    const names = mode.value === 's2s' ? ['s2s'] : ['stt', 'llm', 'tts']
    const settings = Object.fromEntries(names.map(name => [name, { ...draft.value[name] }]))
    if (settings.s2s) {
      if (platform.value === 'custom' && !settings.s2s.url?.trim()) throw new Error('请输入自定义实时 WebSocket 地址。')
      if (platform.value !== 'custom') settings.s2s.url = platform.value === 'qwen' ? qwenUrl : ''
    }
    for (const field of props.config.environment_fields) {
      const [section, name] = field.split('.')
      if (settings[section]) delete settings[section][name]
    }
    const result = await api('config', 'PUT', { mode: mode.value, settings })
    emit('saved', result)
    message.value = before === result.mode ? '已保存，新对话将使用此配置。' : '已保存。请在设备托盘暂停并继续监听，以应用新模式。'
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = ''
  }
}

async function check() {
  if (busy.value) return
  busy.value = 'check'
  error.value = ''
  try {
    await api('check', 'POST')
    message.value = '实时模型连接成功。'
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = ''
  }
}
</script>

<template>
  <el-card shadow="never">
    <template #header><h2>对话模型</h2></template>
    <el-form label-position="top" :disabled="Boolean(busy)" @submit.prevent="save" @input="changed">
      <div class="fields">
        <el-form-item label="对话模式"><el-select v-model="mode" @change="changed">
          <el-option label="实时语音 S2S" value="s2s" /><el-option label="串联模式" value="cascade" />
        </el-select></el-form-item>
      </div>
      <div v-if="mode === 's2s'" class="fields">
        <el-form-item label="接入平台"><el-select v-model="platform" @change="changed">
          <el-option label="千问 AI 平台" value="qwen" /><el-option label="阿里云百炼" value="bailian" />
          <el-option label="自定义 WebSocket" value="custom" />
        </el-select></el-form-item>
        <el-form-item label="API Key"><el-input v-model="draft.s2s.api_key" type="password" autocomplete="new-password"
          :disabled="managed('s2s.api_key')" :placeholder="keyHint('s2s.api_key')" /></el-form-item>
        <template v-if="platform === 'bailian'">
          <el-form-item label="地域"><el-select v-model="draft.s2s.region" @change="changed">
            <el-option label="北京" value="cn-beijing" /><el-option label="新加坡" value="ap-southeast-1" />
          </el-select></el-form-item>
          <el-form-item label="业务空间 ID"><el-input v-model="draft.s2s.workspace_id" :disabled="managed('s2s.workspace_id')" /></el-form-item>
        </template>
        <el-form-item v-if="platform === 'custom'" label="实时 WebSocket 地址" class="full"><el-input v-model="draft.s2s.url" /></el-form-item>
        <el-form-item label="模型 ID"><el-input v-model="draft.s2s.model" /></el-form-item>
        <el-form-item label="音色"><el-input v-model="draft.s2s.voice" /></el-form-item>
        <el-form-item label="对话提示词" class="full"><el-input v-model="draft.s2s.instructions" type="textarea" :rows="3" /></el-form-item>
        <el-checkbox v-model="draft.s2s.log_input_transcript" class="full" @change="changed">记录用户语音转写</el-checkbox>
      </div>
      <template v-else>
        <h3>语音识别</h3>
        <div class="fields">
          <el-form-item label="识别提供方"><el-select v-model="draft.stt.provider" @change="changed">
            <el-option label="豆包语音" value="doubao" /><el-option label="本地 Vosk" value="vosk" />
          </el-select></el-form-item>
          <template v-if="draft.stt.provider === 'doubao'">
            <el-form-item label="鉴权方式"><el-select v-model="draft.stt.auth_mode" @change="changed">
              <el-option label="API Key" value="key" /><el-option label="App ID 与 Access Token" value="legacy" />
            </el-select></el-form-item>
            <template v-for="[field, label] in (draft.stt.auth_mode === 'key' ? [['api_key', '豆包 API Key']] : [['app_id', 'App ID'], ['access_token', 'Access Token']])" :key="field">
              <el-form-item :label="label"><el-input v-model="draft.stt[field]" type="password" autocomplete="new-password"
                :disabled="managed('stt.' + field)" :placeholder="keyHint('stt.' + field)" /></el-form-item>
            </template>
            <el-form-item label="识别服务地址"><el-input v-model="draft.stt.url" /></el-form-item>
            <el-form-item label="资源 ID"><el-input v-model="draft.stt.resource_id" /></el-form-item>
          </template>
        </div>
        <h3>对话与播报</h3>
        <div class="fields">
          <el-form-item label="对话模型 ID"><el-input v-model="draft.llm.model" /></el-form-item>
          <el-form-item label="对话模型 API Key"><el-input v-model="draft.llm.api_key" type="password" autocomplete="new-password"
            :disabled="managed('llm.api_key')" :placeholder="keyHint('llm.api_key')" /></el-form-item>
          <el-form-item label="对话服务地址"><el-input v-model="draft.llm.url" /></el-form-item>
          <el-form-item label="Windows 播报音色"><el-input v-model="draft.tts.voice" /></el-form-item>
        </div>
      </template>
      <el-alert v-if="error || message || config.missing.length" class="form-notice"
        :title="error || message || ('待配置 · ' + config.missing.map(key => missingNames[key] || key).join('、'))"
        :type="error ? 'error' : message ? 'success' : 'warning'" show-icon :closable="false" />
      <div class="actions">
        <el-button type="primary" native-type="submit" :loading="busy === 'save'">保存配置</el-button>
        <el-button v-if="canCheck" :loading="busy === 'check'" @click="check">测试 S2S 连接</el-button>
      </div>
    </el-form>
  </el-card>
</template>
