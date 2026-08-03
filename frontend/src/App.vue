<script setup lang="ts">
import { computed, ref } from 'vue'
import MarkdownRenderer from './components/MarkdownRenderer.vue'
import ProgressBar from './components/ProgressBar.vue'
import ResearchPlan from './components/ResearchPlan.vue'
import { useResearchStream } from './composables/useResearchStream'
import type { ResearchEvent, ResearchTask, TaskSummary } from './types'

const exampleTopic = 'Datawhale 是如何组织开源学习社区并持续运营的？'
const topic = ref('')
const stage = ref('idle')
const percentage = ref(0)
const message = ref('准备好后，研究进展会显示在这里。')
const tasks = ref<ResearchTask[]>([])
const summaries = ref<TaskSummary[]>([])
const activeTaskId = ref<string | null>(null)
const sources = ref<Record<string, number>>({})
const markdown = ref('')
const formError = ref('')
const { isRunning, error, start, cancel } = useResearchStream()

const completedTasks = computed(() => summaries.value.length)
const sourceTotal = computed(() => Object.values(sources.value).reduce((total, count) => total + count, 0))
const activeTask = computed(() => tasks.value.find((task) => task.id === activeTaskId.value))
const connectionLabel = computed(() => {
  if (error.value) return '研究服务连接异常'
  if (isRunning.value) return '正在同步研究进展'
  if (stage.value === 'completed') return '本轮研究已完成'
  return '可以开始新研究'
})

function resetResult() {
  stage.value = 'planning'
  percentage.value = 0
  message.value = '正在理解问题并准备研究计划…'
  tasks.value = []
  summaries.value = []
  activeTaskId.value = null
  sources.value = {}
  markdown.value = ''
}

function handleEvent(event: ResearchEvent) {
  stage.value = event.stage || stage.value
  percentage.value = event.percentage ?? percentage.value
  message.value = event.text || message.value

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
  const normalizedTopic = topic.value.trim()
  formError.value = ''

  if (normalizedTopic.length < 3) {
    formError.value = '请至少输入 3 个字符，让研究问题更具体一些。'
    return
  }

  resetResult()
  await start(normalizedTopic, handleEvent)
}

function useExampleTopic() {
  topic.value = exampleTopic
  formError.value = ''
}

function downloadReport() {
  if (!markdown.value) return
  const blob = new Blob([markdown.value], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'CiteTrail-研究报告.md'
  anchor.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <a class="skip-link" href="#research-form">跳到研究问题</a>
  <main class="app-shell">
    <div class="app-frame">
      <header class="topbar">
        <div class="brand" aria-label="CiteTrail 深度研究助手">
          <span class="brand-mark">研</span>
          <div class="brand-copy">
            <strong>CiteTrail</strong>
            <span>公开资料研究助手</span>
          </div>
        </div>
        <div class="topbar-status" role="status">
          <span class="status-dot" :class="{ running: isRunning, done: stage === 'completed', failed: Boolean(error) }"></span>
          <span>{{ connectionLabel }}</span>
        </div>
      </header>

      <section class="page-intro" aria-labelledby="page-title">
        <div class="intro-copy">
          <p class="section-label">研究工作台</p>
          <h1 id="page-title">把一个问题，<em>研究得更明白。</em></h1>
          <p class="hero-copy">提出你真正想解决的问题。我们会拆分研究步骤、检索公开资料，并把结论与来源整理成一份便于核验的报告。</p>
        </div>
        <dl class="intro-facts" aria-label="研究输出说明">
          <div>
            <dt>研究过程</dt>
            <dd>规划、检索、归纳</dd>
          </div>
          <div>
            <dt>最终交付</dt>
            <dd>结论、来源与 Markdown 报告</dd>
          </div>
        </dl>
      </section>

      <section id="research-form" class="command-section" aria-labelledby="command-title">
        <div class="command-heading">
          <div>
            <p class="section-label">开始研究</p>
            <h2 id="command-title">你想弄清楚什么？</h2>
            <p>尽量写明研究对象、关注范围，以及你希望回答的问题。</p>
          </div>
          <span class="character-count">{{ topic.length }} / 500</span>
        </div>

        <form class="topic-form" @submit.prevent="runResearch">
          <label class="sr-only" for="topic">研究问题</label>
          <textarea
            id="topic"
            v-model="topic"
            :disabled="isRunning"
            :aria-invalid="Boolean(formError)"
            aria-describedby="topic-help"
            maxlength="500"
            rows="3"
            placeholder="例如：远程办公对小型研发团队的协作效率有哪些长期影响？"
            @input="formError = ''"
          ></textarea>
          <div class="command-actions">
            <button class="primary" type="submit" :disabled="isRunning">
              <span>{{ isRunning ? '研究正在进行' : '生成研究计划' }}</span>
              <span aria-hidden="true">→</span>
            </button>
            <button v-if="isRunning" type="button" class="secondary" @click="cancel">停止研究</button>
          </div>
        </form>

        <div id="topic-help" class="topic-help">
          <span>没有想好具体写法？</span>
          <button type="button" class="example-button" :disabled="isRunning" @click="useExampleTopic">使用示例问题</button>
        </div>
        <p v-if="formError || error" class="error-banner" role="alert">{{ formError || error }}</p>
      </section>

      <ProgressBar
        :percentage="percentage"
        :stage="stage"
        :message="message"
        :task-count="tasks.length"
        :completed-tasks="completedTasks"
        :source-count="sourceTotal"
      />

      <section class="workspace-grid" aria-label="研究工作区">
        <ResearchPlan :tasks="tasks" :active-task-id="activeTaskId" :summaries="summaries" :source-counts="sources" />

        <section class="report-panel" aria-labelledby="report-title">
          <header class="report-header">
            <div>
              <p class="section-label">研究结果</p>
              <h2 id="report-title">研究报告</h2>
            </div>
            <div class="report-tools">
              <span class="report-state" :class="{ ready: Boolean(markdown) }">{{ markdown ? '报告已生成' : '等待生成' }}</span>
              <button class="download-button" :disabled="!markdown" @click="downloadReport">
                <span aria-hidden="true">↓</span>
                <span>下载 Markdown</span>
              </button>
            </div>
          </header>

          <div class="report-meta">
            <template v-if="tasks.length">
              <span>已整理 {{ completedTasks }} / {{ tasks.length }} 个步骤</span>
              <span>找到 {{ sourceTotal }} 条可引用来源</span>
              <span v-if="activeTask" class="report-current">正在处理：{{ activeTask.title }}</span>
            </template>
            <span v-else>提交问题后，报告会在研究过程中逐步生成。</span>
          </div>
          <MarkdownRenderer :markdown="markdown" />
        </section>
      </section>
    </div>
  </main>
</template>
