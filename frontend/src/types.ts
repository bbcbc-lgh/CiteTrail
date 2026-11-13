export interface ResearchTask {
  id: string
  title: string
  intent: string
  query: string
}

export interface Source {
  id: string
  title: string
  url: string
  domain?: string
  snippet?: string
  provider?: string
  source_type?: 'official' | 'article' | 'community'
  code?: string
}

export interface Claim {
  id: string
  text: string
  cited_source_ids: string[]
  cited_chunk_ids: string[]
  support_score: number | null
  support_reason?: string | null
}

export interface TaskSummary {
  task: ResearchTask
  summary: string
  claims: Claim[]
  sources: Source[]
}

export interface Citation {
  index: number
  source_id: string
  title: string
  url: string
  claim_ids: string[]
}

export interface ResearchEvent {
  type: 'progress' | 'plan' | 'task_started' | 'search_results' | 'task_summary' | 'report' | 'error' | 'done' | 'cancelled'
  research_id: string
  stage?: string
  percentage?: number
  text?: string
  data: Record<string, unknown>
}
