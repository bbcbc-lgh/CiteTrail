<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ markdown: string }>()

function escapeHtml(value: string): string {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function renderInline(value: string): string {
  const escaped = escapeHtml(value)
  return escaped
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
}

function renderMarkdown(value: string): string {
  const fallback = '### 研究结果将在这里呈现\n\n提交一个主题后，系统会先展示研究计划，再流式更新各任务结论和最终 Markdown 报告。'
  return (value || fallback).split(/\r?\n/).map((line) => {
    if (line.startsWith('### ')) return `<h3>${renderInline(line.slice(4))}</h3>`
    if (line.startsWith('## ')) return `<h2>${renderInline(line.slice(3))}</h2>`
    if (line.startsWith('# ')) return `<h1>${renderInline(line.slice(2))}</h1>`
    if (line.startsWith('- ')) return `<li>${renderInline(line.slice(2))}</li>`
    if (/^\d+\. /.test(line)) return `<li>${renderInline(line.replace(/^\d+\. /, ''))}</li>`
    if (!line.trim()) return '<br />'
    return `<p>${renderInline(line)}</p>`
  }).join('')
}

const rendered = computed(() => renderMarkdown(props.markdown))
</script>

<template>
  <article class="markdown-body" v-html="rendered"></article>
</template>
