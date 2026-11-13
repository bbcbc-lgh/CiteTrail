<script setup lang="ts">
import { computed } from 'vue'
import type { Citation, TaskSummary } from '../types'

const props = defineProps<{
  summaries: TaskSummary[]
  citations: Citation[]
}>()

// Map source id -> global citation number so a claim can show "[3]" instead of a
// uuid, using the same numbering as the report body.
const numberOf = computed(() => {
  const map = new Map<string, number>()
  for (const citation of props.citations) map.set(citation.source_id, citation.index)
  return map
})

const tasks = computed(() =>
  props.summaries
    .filter((summary) => summary.claims.length > 0)
    .map((summary) => ({
      id: summary.task.id,
      title: summary.task.title,
      claims: summary.claims.map((claim) => {
        const score = claim.support_score
        const state = score === null ? 'unchecked' : score >= 0.7 ? 'strong' : score >= 0.5 ? 'partial' : 'weak'
        return {
          id: claim.id,
          text: claim.text,
          score,
          state,
          reason: claim.support_reason,
          refs: claim.cited_source_ids
            .map((sourceId) => numberOf.value.get(sourceId))
            .filter((index): index is number => typeof index === 'number'),
        }
      }),
    })),
)

const stateLabel: Record<string, string> = {
  strong: '已核验',
  partial: '部分支持',
  weak: '支持较弱',
  unchecked: '未核验',
}

function scrollToReference(index: number) {
  document.getElementById(`ref-${index}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}
</script>

<template>
  <section v-if="tasks.length" class="evidence-panel" aria-labelledby="evidence-title">
    <header class="panel-heading">
      <div>
        <p class="section-label">引用核查</p>
        <h2 id="evidence-title">结论与来源</h2>
      </div>
    </header>
    <p class="plan-caption">每条结论都标注了它引用了哪些来源，以及核查智能体判断这些来源是否真的支持它。</p>

    <div v-for="task in tasks" :key="task.id" class="evidence-task">
      <h3>{{ task.title }}</h3>
      <ul class="claim-list">
        <li v-for="claim in task.claims" :key="claim.id" :class="['claim', claim.state]">
          <div class="claim-text">{{ claim.text }}</div>
          <div class="claim-meta">
            <span class="claim-badge" :title="claim.reason || ''">
              {{ stateLabel[claim.state] }}<template v-if="claim.score !== null"> · {{ claim.score.toFixed(2) }}</template>
            </span>
            <button
              v-for="ref in claim.refs"
              :key="ref"
              type="button"
              class="claim-ref"
              @click="scrollToReference(ref)"
            >[{{ ref }}]</button>
            <span v-if="!claim.refs.length" class="claim-noref">未标注来源</span>
          </div>
          <p v-if="claim.reason && claim.state === 'weak'" class="claim-reason">{{ claim.reason }}</p>
        </li>
      </ul>
    </div>

    <div v-if="citations.length" class="reference-block">
      <h3>来源清单</h3>
      <ol class="reference-list">
        <li v-for="citation in citations" :id="`ref-${citation.index}`" :key="citation.index">
          <span class="reference-index">[{{ citation.index }}]</span>
          <a :href="citation.url" target="_blank" rel="noopener noreferrer">{{ citation.title || citation.url }}</a>
        </li>
      </ol>
    </div>
  </section>
</template>
