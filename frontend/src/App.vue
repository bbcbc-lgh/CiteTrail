<script setup lang="ts">
import { computed, ref } from 'vue'
import MarkdownRenderer from './components/MarkdownRenderer.vue'
import ProgressBar from './components/ProgressBar.vue'
import ResearchPlan from './components/ResearchPlan.vue'
import { useResearchStream } from './composables/useResearchStream'
import type { ResearchEvent, ResearchTask, TaskSummary } from './types'

const topic = ref('Datawhale 是什么样的组织？')
const visible = ref(true)
const stage = ref('idle')
const percentage = ref(0)
const message = ref('输入一个开放主题，系统将规划、检索、总结并生成可追溯的报告。')
const tasks = ref<ResearchTask[]>([])
const summaries = ref<TaskSummary[]>([])
const activeTaskId = ref<string | null>(null)
const sources = ref<Record<string, number>>({})
const markdown = ref('')
const logs = ref<string[]>([])
const { isRunning, error, start, cancel } = useResearchStream()

const completedTasks = computed(() => summaries.value.length)
const status = computed(() => {
  if (error.value) return '连接异常'
  if (isRunning.value) return '研究中'
  if (stage.value === 'completed') return '已完成'
  return '等待开始'
})

function resetResult() {
  stage.value = 'planning'
  percentage.value = 0
  message.value = '正在创建新的研究会话…'
  tasks.value = []
  summaries.value = []
  activeTaskId.value = null
  sources.value = {}
  markdown.value = ''
  logs.value = []
}

function handleEvent(event: ResearchEvent) {
  stage.value = event.stage || stage.value
  percentage.value = event.percentage ?? percentage.value
  message.value = event.text || message.value
  logs.value.unshift(`${event.type}: ${event.text || ''}`)
  logs.value = logs.value.slice(0, 8)

  if (event.type === 'plan') tasks.value = (event.data.tasks || []) as ResearchTask[]
  if (event.type === 'task_started') activeTaskId.value = (event.data.task as ResearchTask).id
  if (event.type === 'search_results') {
    const taskId = event.data.task_id as string
    sources.value[taskId] = ((event.data.sources || []) as unknown[]).filter((item) => Boolean((item as { url?: string }).url)).length
  }
  if (event.type === 'task_summary') {
    const summary = event.data as unknown as TaskSummary
    summaries.value = [...summaries.value.filter((item) => item.task.id !== summary.task.id), summary]
  }
  if (event.type === 'report') markdown.value = String(event.data.markdown || '')
  if (event.type === 'done') activeTaskId.value = null
}

async function runResearch() {
  if (topic.value.trim().length < 3) {
    message.value = '研究主题至少需要 3 个字符。'
    return
  }
  resetResult()
  await start(topic.value.trim(), handleEvent)
}

function downloadReport() {
  if (!markdown.value) return
  const blob = new Blob([markdown.value], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = '深度研究报告.md'
  anchor.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <main class="app-shell">
    <section class="hero">
      <div class="brand"><span class="brand-mark">R</span><span>DEEP RESEARCH</span></div>
      <p class="eyebrow">自动化深度研究智能体</p>
      <h1>把开放问题变成<br /><em>可追溯的研究报告。</em></h1>
      <p class="hero-copy">研究规划智能体负责拆解，SearchTool 采集证据，任务总结智能体提炼要点，报告生成智能体汇总为带来源的 Markdown 报告。</p>
      <button class="primary" @click="visible = true">启动研究工作台 <span>→</span></button>
    </section>

    <section class="architecture-strip">
      <span>Vue 3</span><i>→</i><span>FastAPI + SSE</span><i>→</i><span>研究智能体</span><i>→</i><span>搜索与笔记工具</span>
    </section>

    <section v-if="visible" class="modal-overlay">
      <div class="modal-container">
        <header class="modal-header">
          <div><span class="eyebrow">深度研究工作台</span><h2>深度研究工作台</h2></div>
          <button class="icon-button" title="关闭工作台" @click="visible = false">×</button>
        </header>

        <form class="topic-form" @submit.prevent="runResearch">
          <label for="topic">研究主题</label>
          <div class="input-row">
            <input id="topic" v-model="topic" :disabled="isRunning" placeholder="例如：Datawhale 是什么样的组织？" />
            <button class="primary" :disabled="isRunning">{{ isRunning ? '研究进行中…' : '开始深度研究' }}</button>
            <button v-if="isRunning" type="button" class="secondary" @click="cancel">停止</button>
          </div>
        </form>

        <ProgressBar :percentage="percentage" :stage="stage" :message="message" :task-count="tasks.length" :completed-tasks="completedTasks" />
        <p v-if="error" class="error-banner">{{ error }}</p>

        <div class="workspace-grid">
          <ResearchPlan :tasks="tasks" :active-task-id="activeTaskId" :summaries="summaries" />
          <section class="report-panel">
            <div class="panel-title-row">
              <div><span class="eyebrow">报告生成智能体</span><h2>研究报告</h2></div>
              <button class="text-button" :disabled="!markdown" @click="downloadReport">下载 .md</button>
            </div>
            <MarkdownRenderer :markdown="markdown" />
          </section>
        </div>

        <footer class="modal-footer">
          <span class="status-dot" :class="{ running: isRunning, done: stage === 'completed' }"></span>
          <strong>{{ status }}</strong>
          <span v-if="activeTaskId">· 当前任务已返回 {{ sources[activeTaskId] || 0 }} 条可引用来源</span>
          <span v-else-if="logs.length">· {{ logs[0] }}</span>
        </footer>
      </div>
    </section>
  </main>
</template>
