<template>
  <pre class="block json" v-html="html"></pre>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ value: { default: null } })

const escape = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
// 轻量语法高亮：先转义再着色，避免 XSS
const html = computed(() =>
  escape(JSON.stringify(props.value, null, 2) ?? 'null').replace(
    /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?)/g,
    (m, str, colon, lit, num) => {
      if (str) return colon ? `<span class="k">${str}</span>${colon}` : `<span class="s">${str}</span>`
      if (lit) return `<span class="l">${lit}</span>`
      return `<span class="n">${num}</span>`
    }
  )
)
</script>

<style scoped>
.json :deep(.k) { color: #1b67b3; }
.json :deep(.s) { color: #2f7a52; }
.json :deep(.n) { color: var(--orange); }
.json :deep(.l) { color: #8a5bb0; }
</style>
