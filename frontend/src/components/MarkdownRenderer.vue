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
  let inCode = false
  let codeLines: string[] = []
  const rendered: string[] = []
  for (const line of (value || fallback).split(/\r?\n/)) {
    if (line.startsWith('```')) {
      if (inCode) rendered.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
      codeLines = []
      inCode = !inCode
      continue
    }
    if (inCode) {
      codeLines.push(line)
      continue
    }
    if (line.startsWith('### ')) rendered.push(`<h3>${renderInline(line.slice(4))}</h3>`)
    else if (line.startsWith('## ')) rendered.push(`<h2>${renderInline(line.slice(3))}</h2>`)
    else if (line.startsWith('# ')) rendered.push(`<h1>${renderInline(line.slice(2))}</h1>`)
    else if (line.startsWith('- ')) rendered.push(`<li>${renderInline(line.slice(2))}</li>`)
    else if (/^\d+\. /.test(line)) rendered.push(`<li>${renderInline(line.replace(/^\d+\. /, ''))}</li>`)
    else if (!line.trim()) rendered.push('<br />')
    else rendered.push(`<p>${renderInline(line)}</p>`)
  }
  if (codeLines.length) rendered.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`)
  return rendered.join('')
}

const rendered = computed(() => renderMarkdown(props.markdown))
</script>

<template>
  <article class="markdown-body" :class="{ 'is-empty': !markdown }" v-html="rendered"></article>
</template>
