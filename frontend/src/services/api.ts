/// <reference types="vite/client" />
import axios from 'axios'

export const API_BASE = import.meta.env.VITE_API_URL || '/api/v1'

export const api = axios.create({
  baseURL: API_BASE,
  headers: { 'Content-Type': 'application/json' },
})

export function errorMessage(err: any, fallback: string): string {
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((d: any) => d.msg).join('; ')
  return fallback
}

export interface Repository {
  id: string
  name: string
  url: string
  branch: string
  graph_name: string
  status: 'pending' | 'ingesting' | 'ready' | 'error'
  progress: string | null
  stats: Record<string, number>
  error_message: string | null
  created_at: string
  updated_at: string
  ingested_at: string | null
}

export interface RepositoryStats {
  nodes: Record<string, number>
  edges: Record<string, number>
  languages: Record<string, number>
  total_nodes: number
  total_edges: number
}

export interface AnalysisStep {
  step: number
  action: string
  actor: 'agent' | 'llm'
  tool: string | null
  args: Record<string, any> | null
  query: string
  result_summary: string
  reasoning: string
  data: any
  timestamp: string
}

export interface Commit {
  sha: string
  message: string
  timestamp: number
  author: string
  email: string
}

export interface Suspect {
  rank: number
  id: string
  name: string
  file_path: string
  start_line: number | null
  suspicion_score: number
  breakdown: Record<string, number>
  caller_count: number
  test_count: number
  recent_commits: number
  tests: string[]
  commits: Commit[]
  reasoning: string
}

export interface Verdict {
  id: string
  name: string
  file_path: string
  explanation: string
  confidence: number
  source: 'graph' | 'llm'
}

export interface Fix {
  target: { id: string; name: string; file_path: string; start_line: number; end_line: number; code: string }
  diff: string
  cause?: string
  test_suggestion?: string
  explanation: string
  confidence: number
  files_changed: string[]
  reviewers: { name: string; email: string; fn_commits: number; file_commits: number }[]
  blast_radius: { affected_functions: number; affected_files: number; covering_tests: string[]; sample_callers: string[] }
  tests: string[]
  history?: CodeHistory
  owners?: Ownership | null
}

export interface InvestigationResult {
  id: string
  bug_report_id: string
  repository_id: string
  repository_name?: string
  title: string
  description: string
  status: 'running' | 'completed' | 'confirmed' | 'failed'
  mode: 'graph' | 'graph+llm'
  steps: AnalysisStep[]
  suspects: Suspect[]
  verdict: Verdict
  fix: Fix
  confirmed?: { id: string; name: string; note: string }[]
  error?: string
  created_at: string
  completed_at: string
}

export interface InvestigationSummary {
  id: string
  title: string
  status: string
  created_at: string
  summary: string
  confirmed: string[]
}

export interface GraphNode {
  id: string
  type: string
  label: string
  properties: Record<string, any>
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  type: string
  properties: Record<string, any>
}

export interface GraphResponse {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface Hotspot {
  id: string
  name: string
  file_path: string
  score: number
  complexity: number
  tests: number
}

export interface RiskyChange {
  id: string
  function: string
  file: string
  changed_by: string[]
  commits: string[]
  last_changed: number
  callers: number
}

export interface NamedRef {
  name: string
  file_path: string
}

export interface TestSuggestion {
  function: { id: string; name: string; file_path: string; start_line: number }
  language: string
  framework: string
  test_path: string
  code: string
  covers: string
  model: string | null
  error: string | null
  context: {
    summary: string
    caller_count: number
    callers: NamedRef[]
    callees: NamedRef[]
    example_tests: NamedRef[]
    examples_are_nearby: boolean
    cypher: string[]
  }
}

export interface Health {
  status: string
  falkordb: boolean
  llm: { enabled: boolean; host: string; models: string[]; agent_model: string | null }
}

// ---------------------------------------------------------------- company brain

export interface PullRequestRef {
  id: string
  number: number
  title: string
  url: string
  state: string
  merged_ms: number | null
  opened_by: string | null
  reviews: { name: string; state: string }[]
  fixes: { id: string; number: number; title: string; url: string; state: string; opened_by: string | null }[]
  comments: number
}

export interface Drift {
  sha: string
  message: string
  timestamp: number
  author: string
  pr: number | null
  reviewers: string[]
  functions: string[]
}

export interface Decision {
  id: string
  text: string
  url: string
  at_ms: number | null
  author: string | null
  item_type: 'Issue' | 'PullRequest'
  item_id: string
  item_number: number
  item_title: string
  functions: { id: string; name: string }[]
  drift: Drift[]
}

export interface CodeHistory {
  function: { id: string; name: string; file_path: string }
  changes: { sha: string; message: string; timestamp: number; author: string; email: string; pull_request: PullRequestRef | null }[]
  decisions: Decision[]
  discussions: { type: string; id: string; number: number; title: string; url: string; state: string; opened_by: string | null }[]
}

export interface Ownership {
  file: string
  teams: { id: string; team: string; pattern: string; members: string[] }[]
  owners: { name: string; email: string; pattern: string }[]
  ask: { name: string; email: string | null; score: number; reasons: string[] }[]
}

export interface BrainSource {
  n: number
  key: string
  type: 'Issue' | 'PullRequest' | 'Comment' | 'Decision' | 'Team'
  number?: number
  title?: string
  url?: string
  author?: string | null
  text?: string
  why: string
}

export interface BrainAnswer {
  question: string
  answer: string
  summary: string | null
  mode: 'graph' | 'graph+llm'
  model: string | null
  subjects: { functions: { id: string; name: string; file_path: string }[]; path: string | null }
  sources: BrainSource[]
  steps: { tool: string; args: Record<string, any>; summary: string; query: string }[]
  graph: GraphResponse
}

export interface BrainOverview {
  status: { source: string | null; synced_at: string | null; issues: number; pull_requests: number; decisions: number; teams: number }
  ownership: { kind: string; id: string | null; owner: string; pattern: string; files: number; members: string[] }[]
  decisions: Decision[]
  knowledge_gaps: { path: string; only_author: string; owners: number; callers: number }[]
  summaries: { decisions: string; knowledge_gaps: string }
}
