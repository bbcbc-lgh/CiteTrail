<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  percentage: number
  stage: string
  message: string
  taskCount: number
  completedTasks: number
}>()

const stageLabel = computed(() => ({
  planning: '规划研究任务',
  researching: '检索与证据总结',
  reporting: '整合最终报告',
  completed: '研究已完成',
  failed: '研究失败',
}[props.stage] || '准备中'))
</script>

<template>
  <section class="progress-card" aria-live="polite">
    <div class="progress-heading">
      <div>
        <span class="eyebrow">{{ stageLabel }}</span>
        <strong>{{ message || '等待开始研究…' }}</strong>
      </div>
      <strong class="percentage">{{ percentage }}%</strong>
    </div>
    <div class="track" role="progressbar" :aria-valuenow="percentage" aria-valuemin="0" aria-valuemax="100">
      <div class="fill" :style="{ width: `${percentage}%` }"></div>
    </div>
    <p class="progress-meta">已完成 {{ completedTasks }}/{{ taskCount || '—' }} 个研究任务</p>
  </section>
</template>
