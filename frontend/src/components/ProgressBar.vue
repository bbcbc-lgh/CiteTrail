<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  percentage: number
  stage: string
  message: string
  taskCount: number
  completedTasks: number
  sourceCount: number
}>()

const stages = ['制定计划', '检索资料', '整理结论', '完成报告']
const currentStageIndex = computed(() => ({
  idle: -1,
  planning: 0,
  researching: 1,
  reporting: 2,
  completed: 3,
  cancelled: -1,
  failed: -1,
}[props.stage] ?? -1))

const stageLabel = computed(() => ({
  planning: '正在制定研究计划',
  researching: '正在检索和整理资料',
  reporting: '正在汇总研究报告',
  completed: '本轮研究已完成',
  failed: '研究未能完成',
  cancelled: '研究已停止',
}[props.stage] || '等待开始研究'))

function stageState(index: number): string {
  if (props.stage === 'completed' || index < currentStageIndex.value) return 'complete'
  if (index === currentStageIndex.value) return 'active'
  return 'pending'
}
</script>

<template>
  <section class="progress-card" :class="{ failed: stage === 'failed' }" aria-live="polite">
    <div class="progress-heading">
      <div>
        <p class="section-label">本轮进展</p>
        <h2>{{ stageLabel }}</h2>
        <p>{{ message || '提交研究问题后，这里会显示当前进展。' }}</p>
      </div>
      <div class="progress-value" aria-label="总体进度">
        <strong>{{ percentage }}</strong><span>%</span>
      </div>
    </div>

    <div class="track" role="progressbar" :aria-label="stageLabel" :aria-valuenow="percentage" aria-valuemin="0" aria-valuemax="100">
      <div class="fill" :style="{ width: `${percentage}%` }"></div>
    </div>

    <ol class="stage-list" aria-label="研究阶段">
      <li v-for="(item, index) in stages" :key="item" :class="stageState(index)">
        <span class="stage-marker" aria-hidden="true"></span>
        <span>{{ item }}</span>
      </li>
    </ol>

    <dl class="progress-stats">
      <div>
        <dt>研究步骤</dt>
        <dd>{{ completedTasks }} / {{ taskCount || '—' }} 已完成</dd>
      </div>
      <div>
        <dt>参考来源</dt>
        <dd>{{ sourceCount }} 条</dd>
      </div>
    </dl>
  </section>
</template>
