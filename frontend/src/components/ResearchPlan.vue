<script setup lang="ts">
import type { ResearchTask, TaskSummary } from '../types'

defineProps<{
  tasks: ResearchTask[]
  activeTaskId: string | null
  summaries: TaskSummary[]
}>()
</script>

<template>
  <aside class="research-plan">
    <div class="panel-title-row">
      <div>
        <span class="eyebrow">任务规划</span>
        <h2>研究计划</h2>
      </div>
      <span class="count">{{ tasks.length }}</span>
    </div>
    <ol v-if="tasks.length" class="task-list">
      <li v-for="(task, index) in tasks" :key="task.id" :class="{ active: task.id === activeTaskId, complete: summaries.some((item) => item.task.id === task.id) }">
        <span class="task-number">{{ index + 1 }}</span>
        <div>
          <strong>{{ task.title }}</strong>
          <p>{{ task.intent }}</p>
          <code>{{ task.query }}</code>
        </div>
      </li>
    </ol>
    <p v-else class="empty-state">规划完成后，3–5 个可检索的子任务会出现在这里。</p>
  </aside>
</template>
