export interface ResearchTask {
  id: string
  title: string
  intent: string
  query: string
}

export interface Source {
  title: string
  url: string
  snippet: string
  provider: string
}

export interface TaskSummary {
  task: ResearchTask
  summary: string
  key_points: string[]
  sources: Source[]
}

export interface ResearchEvent {
  type: 'progress' | 'plan' | 'task_started' | 'search_results' | 'task_summary' | 'report' | 'error' | 'done'
  research_id: string
  stage?: string
  percentage?: number
  text?: string
  data: Record<string, unknown>
}
