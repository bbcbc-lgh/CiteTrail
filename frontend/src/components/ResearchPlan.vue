<script setup lang="ts">
import { computed } from 'vue'
import type { ResearchTask, TaskSummary } from '../types'

const props = defineProps<{
  tasks: ResearchTask[]
  activeTaskId: string | null
  summaries: TaskSummary[]
  sourceCounts: Record<string, number>
}>()

const completedCount = computed(() => props.summaries.length)

function taskState(task: ResearchTask): string {
  if (props.summaries.some((item) => item.task.id === task.id)) return '已完成'
  if (task.id === props.activeTaskId) return '研究中'
  return '待开始'
}
</script>

<template>
  <aside class="research-plan" aria-labelledby="plan-title">
    <header class="panel-heading">
      <div>
        <p class="section-label">研究路径</p>
        <h2 id="plan-title">研究步骤</h2>
      </div>
      <div class="panel-count" aria-label="完成任务数">
        <strong>{{ completedCount }}</strong>
        <span>/ {{ tasks.length || '—' }}</span>
      </div>
    </header>
    <p class="plan-caption">研究会按顺序推进，当前正在处理的步骤会突出显示。</p>

    <ol v-if="tasks.length" class="task-list">
      <li
        v-for="(task, index) in tasks"
        :key="task.id"
        :class="{ active: task.id === activeTaskId, complete: summaries.some((item) => item.task.id === task.id) }"
        :aria-current="task.id === activeTaskId ? 'step' : undefined"
      >
        <span class="task-number">{{ String(index + 1).padStart(2, '0') }}</span>
        <div class="task-content">
          <div class="task-row">
            <strong>{{ task.title }}</strong>
            <span class="task-state">{{ taskState(task) }}</span>
          </div>
          <p>{{ task.intent }}</p>
          <div class="task-query">
            <span>检索关键词</span>
            <code>{{ task.query }}</code>
          </div>
          <span v-if="sourceCounts[task.id]" class="task-source">已找到 {{ sourceCounts[task.id] }} 条来源</span>
        </div>
      </li>
    </ol>

    <div v-else class="empty-state plan-empty">
      <strong>研究步骤将在这里生成</strong>
      <p>提交问题后，系统会先把它拆分成几个可以独立检索的方向。</p>
    </div>
  </aside>
</template>
