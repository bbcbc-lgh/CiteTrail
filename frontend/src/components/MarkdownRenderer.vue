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
  const fallback = '## 报告还没有生成\n\n研究开始后，结论和引用来源会随着各个步骤完成逐步整理到这里。'
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
  <article class="markdown-body" :class="{ 'is-empty': !markdown }" v-html="rendered"></article>
</template>
