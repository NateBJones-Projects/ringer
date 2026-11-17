import type { Run, RunData, Snapshot, Task } from '../types'

export type Config = { port: number; pollMs: number; paneColumns: number; openOnStart: boolean; statusLine: boolean }
export type Piece = { text: string; color?: string; href?: string }
export type Line = Piece[]
export const EMPTY: Snapshot = { runs: [], active: {}, lastSuccess: null, error: null }

export function config(options: Readonly<Record<string, unknown>>): Config {
  const integer = (key: string, fallback: number, min: number, max: number) => {
    const value = options[key]
    return typeof value === 'number' && Number.isFinite(value) ? Math.max(min, Math.min(max, Math.round(value))) : fallback
  }
  return {
    port: integer('port', 8700, 1, 65535), pollMs: integer('pollMs', 2000, 100, 3600000),
    paneColumns: integer('paneColumns', 60, 24, 500),
    openOnStart: options.openOnStart !== false, statusLine: options.statusLine !== false,
  }
}

export function sanitize(value: string): string {
  return value
    .replace(/(?:\x1b\]|\x9d)[\s\S]*?(?:\x07|\x1b\\|\x9c)/g, '')
    .replace(/(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]/g, '')
    .replace(/\x1b[ -/]*[@-~]/g, '')
    .replace(/[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]/g, '')
}

// Conservative cell width: overestimating a joined emoji is safer than overflow.
export function cellWidth(value: string): number {
  return Array.from(value).reduce((sum, char) => {
    const cp = char.codePointAt(0)!
    if (/\p{Mark}/u.test(char) || cp === 0x200d || cp === 0xfe0f) return sum
    const wide = cp >= 0x1100 && (cp <= 0x115f || cp === 0x2329 || cp === 0x232a ||
      (cp >= 0x2e80 && cp <= 0xa4cf) || (cp >= 0xac00 && cp <= 0xd7a3) ||
      (cp >= 0xf900 && cp <= 0xfaff) || (cp >= 0xfe10 && cp <= 0xfe6f) ||
      (cp >= 0xff01 && cp <= 0xff60) || (cp >= 0xffe0 && cp <= 0xffe6) ||
      (cp >= 0x1f000 && cp <= 0x1ffff) || cp >= 0x20000)
    return sum + (wide ? 2 : 1)
  }, 0)
}

export function fit(line: Line, columns: number): Line {
  const clean = line.map(part => ({ ...part, text: sanitize(part.text) }))
  const clipped = clean.reduce((n, p) => n + cellWidth(p.text), 0) > columns
  let room = Math.max(0, Math.floor(columns) - (clipped ? 1 : 0))
  const result: Line = []
  for (const part of clean) {
    let text = ''
    for (const char of part.text) {
      const width = cellWidth(char)
      if (width > room) { if (text) result.push({ ...part, text }); return [...result, { text: '…' }] }
      text += char
      room -= width
    }
    if (text) result.push({ ...part, text })
  }
  return result
}

export function parseRuns(text: string): RunData {
  const data = JSON.parse(text)
  const object = (x: unknown): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x)
  const string = (x: unknown) => typeof x === 'string' ? x : undefined
  const number = (x: unknown) => typeof x === 'number' && Number.isFinite(x) && x >= 0 ? x : undefined
  const integer = (x: unknown) => Number.isSafeInteger(x) ? number(x) : undefined
  if (!object(data) || !object(data.active) || !Array.isArray(data.runs)) throw new Error('Ringside contract unavailable')
  const runs: Run[] = []
  for (const run of data.runs) {
    if (!object(run) || typeof run.run_id !== 'string' || typeof run.run_name !== 'string' ||
      typeof run.state !== 'string' || !Array.isArray(run.tasks) ||
      !run.tasks.every(t => object(t) && typeof t.key === 'string' && typeof t.status === 'string')) continue
    runs.push({
      run_id: run.run_id, run_name: run.run_name, state: run.state,
      started_at: string(run.started_at), elapsed_s: number(run.elapsed_s), tokens: number(run.tokens),
      tasks: run.tasks.map(t => ({
        key: t.key, status: t.status, engine: string(t.engine), model: string(t.model),
        attempts: integer(t.attempts), max_attempts: integer(t.max_attempts),
        elapsed_s: number(t.elapsed_s), tokens: number(t.tokens), check_output_tail: string(t.check_output_tail),
      })),
    })
  }
  return { runs, active: data.active, skippedRuns: data.runs.length - runs.length }
}

export function runState(run: Run, active: RunData['active']): string {
  if (run.state === 'died' || (run.state === 'live' && !Object.prototype.hasOwnProperty.call(active, run.run_id))) return 'STOPPED'
  return run.state === 'finished' ? 'FINISHED' : run.state === 'live' ? 'LIVE' : sanitize(run.state)
}

export function selectRun(data: RunData, now: number): { run?: Run; more: number; last: boolean } {
  const started = (r: Run) => Date.parse(r.started_at ?? '')
  const newest = (a: Run, b: Run) => (started(b) || 0) - (started(a) || 0)
  const live = data.runs.filter(r => r.state === 'live' && Object.prototype.hasOwnProperty.call(data.active, r.run_id)).sort(newest)
  if (live.length) return { run: live[0], more: live.length - 1, last: false }
  // No finish timestamp is supplied; elapsed_s is the final run duration.
  const ended = (r: Run) => started(r) + (r.elapsed_s ?? NaN) * 1000
  const finished = data.runs.filter(r => r.state === 'finished' && now >= ended(r) && now - ended(r) <= 1800000).sort((a, b) => ended(b) - ended(a))
  if (finished.length) return { run: finished[0], more: 0, last: true }
  // Retain a stopped run only when it is the most recently written state file.
  const latest = data.runs[0]
  return { run: latest && runState(latest, data.active) === 'STOPPED' ? latest : undefined, more: 0, last: false }
}

export function duration(seconds: number | undefined): string {
  if (seconds === undefined) return '-'
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export function tokenText(tokens: number | null | undefined): string {
  return tokens == null ? '-' : String(Math.round(tokens)).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

export function counts(run: Run, includeRunning = true): string {
  const count = (status: string) => run.tasks.filter(t => t.status === status).length
  const base = `${count('pass')} pass ${count('fail')} fail`
  return !includeRunning ? base : `${base} ${run.tasks.filter(t => ['running', 'verifying', 'retrying'].includes(t.status)).length} running`
}

export function isStale(snapshot: Snapshot, now: number, pollMs: number): boolean {
  return snapshot.lastSuccess !== null && now - snapshot.lastSuccess > 3 * pollMs
}

export function summary(snapshot: Snapshot, now: number, cfg: Config): string {
  const selected = selectRun(snapshot, now)
  const run = selected.run
  const message = snapshot.error === 'unreachable' ? `Ringside not reachable on :${cfg.port}` :
    snapshot.error === 'contract' ? 'Ringside response missing required fields' : ''
  const state = run ? `${selected.last ? 'last run · ' : ''}${runState(run, snapshot.active)} · ${sanitize(run.run_name)} · ${counts(run, runState(run, snapshot.active) !== 'STOPPED')} · ${duration(run.elapsed_s)}${selected.more ? ` · +${selected.more} more live` : ''}` : 'no live run'
  return `referee · ${isStale(snapshot, now, cfg.pollMs) ? 'STALE · ' : ''}${message ? `${message} · ` : ''}${state}`
}

export function attemptCells(task: Task, stopped: boolean): Line {
  const attempts = task.attempts ?? 0
  return Array.from({ length: attempts }, (_, i) => {
    if (i < attempts - 1 || task.status === 'fail') return { text: '■', color: 'error' }
    if (task.status === 'pass') return { text: '■', color: 'success' }
    return { text: '□', color: stopped ? 'error' : 'warning' }
  })
}

export function lane(task: Task, stopped: boolean): Line {
  const color = stopped || task.status === 'fail' ? 'error' : task.status === 'pass' ? 'success' : 'warning'
  return [{ text: `${task.status === 'pass' ? '+' : task.status === 'fail' ? 'x' : '·'} ${task.key} ${stopped ? 'saved: ' : ''}${task.status} `, color },
    ...attemptCells(task, stopped), { text: ` ${task.engine ?? '-'}${task.model ? `/${task.model}` : ''} ${task.attempts ?? '-'}${task.max_attempts === undefined ? '' : `/${task.max_attempts}`} ${duration(task.elapsed_s)} tok ${tokenText(task.tokens)}`, color: stopped ? 'error' : undefined }]
}

export function layout(snapshot: Snapshot, now: number, cfg: Config, placement: string, columns: number, rows?: number): Line[] {
  const selected = selectRun(snapshot, now)
  const run = selected.run
  const stale = isStale(snapshot, now, cfg.pollMs)
  const plain = (text: string, color?: string): Line => [{ text, color }]
  const lines: Line[] = []
  const budget = Math.max(0, Math.floor(rows ?? 8))
  if (placement !== 'dock' && placement !== 'inline') return [fit(plain(summary(snapshot, now, cfg), run && runState(run, snapshot.active) === 'STOPPED' ? 'error' : undefined), columns)]
  if (snapshot.error) {
    lines.push(plain(snapshot.error === 'unreachable' ? `Ringside not reachable on :${cfg.port}` : 'Ringside response missing required fields', 'error'))
    lines.push(plain('./ringer.py hud --no-open', 'warning'))
  }
  if (!run) lines.push(plain(`${stale ? 'STALE · ' : ''}no live run`))
  else {
    const stopped = runState(run, snapshot.active) === 'STOPPED'
    const heading = `${stale ? 'STALE · ' : snapshot.error ? 'CACHED · ' : ''}${selected.last ? 'last run · ' : ''}${runState(run, snapshot.active)}`
    lines.push(plain(`${heading} · ${run.run_name}`, stopped ? 'error' : 'success'))
    if (placement === 'dock') lines.push(plain(`${duration(run.elapsed_s)} · ${counts(run, runState(run, snapshot.active) !== 'STOPPED')} · tokens ${tokenText(run.tokens)}`))
    if (selected.more) lines.push(plain(`+${selected.more} more live`))
    const tasks = placement === 'inline' ? [...run.tasks].sort((a, b) => {
      const priority = (t: Task) => ['running', 'verifying', 'retrying', 'fail'].includes(t.status) ? 0 : 1
      return priority(a) - priority(b)
    }) : run.tasks
    // Even when notices fill the body, reserve a row to account for hidden tasks.
    if (placement === 'inline' && tasks.length) lines.splice(Math.max(0, budget - 1))
    const available = placement === 'inline' ? Math.max(0, budget - lines.length) : tasks.length
    const shown = tasks.length > available ? Math.max(0, available - 1) : available
    for (const task of tasks.slice(0, shown)) lines.push(lane(task, stopped))
    if (tasks.length > shown) lines.push(plain(`+${tasks.length - shown} more`))
    if (placement === 'dock') {
      if (run.state === 'finished') {
        lines.push(plain(`Receipt ${duration(run.elapsed_s)} · ${counts(run, false)}`))
        const retries = run.tasks.some(t => t.attempts === undefined) ? '-' : run.tasks.reduce((n, t) => n + Math.max(0, t.attempts! - 1), 0)
        lines.push(plain(`${retries} retries · tokens ${tokenText(run.tokens)}`))
      }
      for (const task of run.tasks.filter(t => t.status === 'fail')) {
        const excerpt = task.check_output_tail?.split(/\r?\n/).map(sanitize).filter(line => line.trim()).pop()
        if (excerpt) lines.push(plain(`${task.key}: ${excerpt}`, 'error'))
      }
    }
  }
  if (placement === 'dock') {
    if (snapshot.skippedRuns) lines.push(plain(`${snapshot.skippedRuns} unreadable runs skipped`, 'warning'))
    lines.push([{ text: `http://127.0.0.1:${cfg.port}`, href: `http://127.0.0.1:${cfg.port}` }])
  }
  return (placement === 'inline' ? lines.slice(0, budget) : lines).map(line => fit(line, columns))
}
