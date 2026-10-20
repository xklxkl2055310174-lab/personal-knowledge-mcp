<template>
  <div class="stack">
    <div class="row">
      <span class="badge" :class="result?.ok ? 'green' : 'red'">{{ result?.ok ? '✓ ok' : '✕ failed' }}</span>
      <template v-if="err">
        <span class="badge" :class="cat.tone">{{ err.category }} · {{ cat.label }}</span>
        <span class="badge mono">{{ err.code }}</span>
        <span class="badge" :class="err.retryable ? 'orange' : ''">{{ err.retryable ? '可重试' : '不可重试' }}</span>
      </template>
      <span v-if="result?.meta?.verified" class="badge blue">已通过结果校验</span>
      <span class="spacer"></span>
      <span v-if="duration != null" class="muted small">{{ duration }} ms</span>
    </div>

    <div v-if="err" class="error-box">
      <div>{{ err.message }}</div>
      <div class="small" style="margin-top:4px;opacity:.85">Agent 处理建议：{{ cat.hint }}</div>
    </div>

    <div v-if="attempts.length" class="attempts">
      <div class="small muted">Verification Loop · 尝试记录</div>
      <ol>
        <li v-for="a in attempts" :key="a.number">
          <span class="badge" :class="a.outcome === 'verified' ? 'green' : 'orange'">#{{ a.number }} {{ a.outcome }}</span>
          <span class="muted small">{{ a.duration_ms }} ms</span>
        </li>
      </ol>
    </div>

    <JsonView :value="result" />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import JsonView from './JsonView.vue'
import { ERROR_CATEGORY } from '../meta'

const props = defineProps({ result: Object, duration: Number })
const err = computed(() => props.result?.error || null)
const cat = computed(() => ERROR_CATEGORY[err.value?.category] || { label: '未知', tone: '', hint: '-' })
const attempts = computed(() => props.result?.meta?.attempts || [])
</script>

<style scoped>
.attempts ol { list-style: none; padding: 0; margin: 6px 0 0; display: flex; gap: 12px; flex-wrap: wrap; }
.attempts li { display: flex; align-items: center; gap: 6px; }
</style>
