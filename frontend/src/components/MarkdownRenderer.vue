<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const props = defineProps<{ markdown: string }>()

const FALLBACK_REPORT = '## 报告还没有生成\n\n研究开始后，结论和引用来源会随着各个步骤完成逐步整理到这里。'

// raw HTML is disabled so report text can never introduce markup on its own;
// anything the model emits is rendered as text unless it matches Markdown syntax.
const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  typographer: false,
})

// Turn the report's [n] citation markers into links to the reference list, so a
// claim in the prose can be traced to its source with one click. Done at the
// token level rather than by rewriting HTML afterwards, which would risk
// corrupting code blocks or attributes.
md.core.ruler.push('citation_links', (state) => {
  for (const token of state.tokens) {
    if (token.type !== 'inline' || !token.children) continue
    const output: typeof token.children = []
    for (const child of token.children) {
      if (child.type !== 'text' || !/\[\d+\]/.test(child.content)) {
        output.push(child)
        continue
      }
      const parts = child.content.split(/(\[\d+\])/)
      for (const part of parts) {
        const match = /^\[(\d+)\]$/.exec(part)
        if (!match) {
          if (part) {
            const text = new state.Token('text', '', 0)
            text.content = part
            output.push(text)
          }
          continue
        }
        const number = match[1]
        const open = new state.Token('link_open', 'a', 1)
        open.attrs = [['href', `#ref-${number}`], ['class', 'citation-ref']]
        open.meta = { citation: number }
        const label = new state.Token('text', '', 0)
        label.content = number
        const close = new state.Token('link_close', 'a', -1)
        output.push(open, label, close)
      }
    }
    token.children = output
  }
})

// External links open in a new tab with rel hardening. In-page citation anchors
// (#ref-n) must stay in the same tab, so they are excluded.
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A' && !(node.getAttribute('href') || '').startsWith('#')) {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

const rendered = computed(() =>
  DOMPurify.sanitize(md.render(props.markdown || FALLBACK_REPORT), {
    FORBID_TAGS: ['style', 'form', 'input', 'button', 'iframe', 'object', 'embed', 'base'],
    FORBID_ATTR: ['style'],
    ALLOW_DATA_ATTR: false,
    ADD_ATTR: ['target', 'rel'],
    USE_PROFILES: { html: true },
  }),
)
</script>

<template>
  <article class="markdown-body" :class="{ 'is-empty': !markdown }" v-html="rendered"></article>
</template>
