export type Task = {
  key: string
  status: string
  engine?: string
  model?: string
  attempts?: number
  max_attempts?: number
  activity?: string
  check_output_tail?: string
  elapsed_s?: number
  tokens?: number | null
}
export type Run = {
  run_id: string
  run_name: string
  max_parallel?: number
  started_at?: string
  state: string
  elapsed_s?: number
  tokens?: number | null
  tasks: Task[]
}
export type RunData = {
  runs: Run[]
  active: Record<string, unknown>
  skippedRuns?: number
}
export type Snapshot = RunData & {
  lastSuccess: number | null
  error: 'unreachable' | 'contract' | null
}

declare module 'claude-code' {
  interface PluginState {
    referee: { snapshot: Shaped<Snapshot> }
  }
}
