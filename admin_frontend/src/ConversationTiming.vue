<script setup>
import { computed } from 'vue'

const props = defineProps({ item: { type: Object, required: true } })
const timing = computed(() => props.item.timings ?? {})
const device = computed(() => timing.value.device ?? {})
const seconds = value => value === undefined ? '—' : (value / 1000).toFixed(2) + ' 秒'
const hasCapture = computed(() => device.value.speech_ms !== undefined)
const wait = computed(() => ['endpoint_ms', 'submit_ms', 'reply_wait_ms'].every(key => device.value[key] !== undefined)
  ? device.value.endpoint_ms + device.value.submit_ms + device.value.reply_wait_ms : undefined)
const stages = computed(() => [
  { label: device.value.entry === 'wake' ? '说话 · 含唤醒词' : '说话', value: device.value.speech_ms, estimated: true },
  { label: '静音判停', value: device.value.endpoint_ms, estimated: true },
  { label: '提交音频', value: device.value.submit_ms },
  { label: '等待回复', value: device.value.reply_wait_ms },
  { label: '播放回复', value: device.value.playback_ms },
])
</script>

<template>
  <div class="log-timings">
    <template v-if="hasCapture">
      <ol class="audio-flow" aria-label="语音处理流程">
        <li v-for="(stage, index) in stages" :key="stage.label">
          <span>{{ index + 1 }} · {{ stage.label }}</span>
          <strong>{{ stage.value === undefined ? '未采集' : (stage.estimated ? '约 ' : '') + seconds(stage.value) }}</strong>
        </li>
        <li v-if="wait !== undefined"><span>说完到开播</span><strong>约 {{ seconds(wait) }}</strong></li>
        <li v-if="device.total_ms !== undefined"><span>完整交互</span><strong>约 {{ seconds(device.total_ms) }}</strong></li>
        <li v-if="device.wake_ms !== undefined"><span>开口到检测到唤醒</span><strong>约 {{ seconds(device.wake_ms) }}</strong></li>
        <li v-else-if="device.entry === 'follow_up'">连续追问 · 无需唤醒</li>
      </ol>
    </template>
    <template v-else>
      <span>未采集设备端时序</span>
      <span>服务端本轮耗时 {{ seconds(item.duration_ms) }}</span>
    </template>
  </div>
</template>
