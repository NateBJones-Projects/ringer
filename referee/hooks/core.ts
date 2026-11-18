import type { Run, RunData, Snapshot, Task } from '../types'

export type Config = { port: number; pollMs: number; paneColumns: number; openOnStart: boolean; statusLine: boolean }
export type Piece = { text: string; color?: string; bold?: boolean; href?: string }
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
  columns = Math.max(0, Math.floor(columns))
  if (!columns) return []
  const clean = line.map(part => ({ ...part, text: sanitize(part.text) }))
  const clipped = clean.reduce((n, p) => n + cellWidth(p.text), 0) > columns
  let room = Math.max(0, Math.floor(columns) - (clipped ? 1 : 0))
  const result: Line = []
  for (const part of clean) {
    let text = ''
    for (const char of part.text) {
      const width = cellWidth(char)
      if (width > room) { if (text) result.push({ ...part, text }); return [...result, { ...part, text: '…' }] }
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
      started_at: string(run.started_at), elapsed_s: number(run.elapsed_s), tokens: number(run.tokens), max_parallel: integer(run.max_parallel),
      tasks: run.tasks.map(t => ({
        key: t.key, status: t.status, activity: typeof t.activity === 'string' ? sanitize(t.activity) : undefined, engine: string(t.engine), model: string(t.model),
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
  return `${isStale(snapshot, now, cfg.pollMs) ? 'STALE · ' : ''}${message ? `${message} · ` : ''}${state}`
}

export function attemptCells(task: Task, stopped: boolean): Line {
  const attempts = task.attempts ?? 0
  return Array.from({ length: attempts }, (_, i) => {
    if (i < attempts - 1 || task.status === 'fail') return { text: '■', color: 'error' }
    if (task.status === 'pass') return { text: '■', color: 'success' }
    return { text: '□', color: stopped ? 'error' : 'warning' }
  })
}

const piece = (text: string, color = 'text', bold = false): Piece => ({ text, color, bold })
const width = (line: Line): number => line.reduce((n, p) => n + cellWidth(sanitize(p.text)), 0)
const space = (n = 1): Piece => piece(' '.repeat(Math.max(0, n)), 'subtle')
const separator = (): Piece => piece(' · ', 'subtle')
const running = (task: Task): boolean => ['running', 'verifying', 'retrying'].includes(task.status)
const unfinished = (task: Task): boolean => !['pass', 'fail'].includes(task.status)
const statusColor = (task: Task, stopped: boolean): string =>
  stopped && unfinished(task) || task.status === 'fail' ? 'error' :
    task.status === 'pass' ? 'success' : running(task) ? 'warning' : 'inactive'

function pad(line: Line, columns: number): Line {
  const fitted = fit(line, columns)
  return [...fitted, space(columns - width(fitted))]
}

function sides(left: Line, right: Line, columns: number): Line {
  const end = fit(right, columns)
  const start = fit(left, Math.max(0, columns - width(end) - 1))
  return [...start, space(columns - width(start) - width(end)), ...end]
}

function centered(line: Line, columns: number): Line {
  const fitted = fit(line, columns)
  return [space(Math.floor((columns - width(fitted)) / 2)), ...fitted]
}

function card(content: Line[], columns: number, color: string): Line[] {
  const inner = columns - 4
  return [
    [piece(`╭${'─'.repeat(columns - 2)}╮`, color)],
    ...content.map(line => [piece('│ ', color), ...pad(line, inner), piece(' │', color)]),
    [piece(`╰${'─'.repeat(columns - 2)}╯`, color)],
  ]
}

function totals(run: Run, stopped: boolean): number[] {
  const pass = run.tasks.filter(t => t.status === 'pass').length
  const fail = run.tasks.filter(t => t.status === 'fail').length
  const active = stopped ? 0 : run.tasks.filter(running).length
  return [pass, fail, active, run.tasks.length - pass - fail - active]
}
const statusColors = ['success', 'error', 'warning', 'inactive']
const statusLabels = ['pass', 'fail', 'running', 'queued']

// Reserve one cell per present status, then distribute the remaining cells proportionally.
function stacked(values: number[], columns: number): Line {
  const total = values.reduce((a, b) => a + b, 0)
  if (!total) return [piece('·'.repeat(columns), 'subtle')]
  const present = values.filter(Boolean).length
  const remaining = columns - present
  const sizes = values.map(n => n ? 1 + Math.floor(n / total * remaining) : 0)
  const fractions = values.map((n, i) => ({ i, fraction: n ? n / total * remaining % 1 : -1 }))
    .sort((a, b) => b.fraction - a.fraction)
  const spare = columns - sizes.reduce((a, b) => a + b, 0)
  for (let i = 0; i < spare; i++) sizes[fractions[i]!.i]!++
  return sizes.map((n, i) => piece((i === 3 ? '░' : '━').repeat(n), statusColors[i]))
}

function laneRows(task: Task, run: Run, stopped: boolean, columns: number): Line[] {
  const color = statusColor(task, stopped)
  const glyph = stopped && unfinished(task) ? '○' : task.status === 'pass' ? '✓' : task.status === 'fail' ? '✗' : running(task) ? '◐' : '·'
  const labelWidth = Math.min(18, columns - 20, Math.max(0, ...run.tasks.map(t => cellWidth(sanitize(t.key)))))
  let label = pad([piece(sanitize(task.key), color)], labelWidth)
  const clock = fit([piece(duration(task.elapsed_s), 'text', true)], Math.min(12, columns - 8))
  const attempts = fit([piece(`${task.attempts ?? '-'}/${task.max_attempts ?? '-'}`, 'text', true)], 11)
  const tail = [...attempts, space(2), ...clock]
  const widestCells = Math.min(columns, Math.max(0, ...run.tasks.map(t => t.attempts ?? 0)))
  let cells = pad(attemptCells({ ...task, attempts: Math.min(task.attempts ?? 0, columns) }, stopped), widestCells)
  // Below 30 columns omit duration bars first; oversized attempt histories lose cells next.
  let barWidth = columns >= 30 ? columns - (2 + width(label) + 1 + 1 + width(cells) + 1 + width(tail)) : 0
  if (barWidth < 4) {
    if (columns >= 30) {
      cells = []
      barWidth = columns - (2 + width(label) + 1 + 1 + width(tail))
    }
    if (barWidth < 4) barWidth = 0
  }
  if (!barWidth && 2 + width(label) + (cells.length ? width(cells) + 1 : 0) + 1 + width(tail) > columns) cells = []
  label = fit(label, Math.max(0, columns - width(tail) - 3 - (cells.length ? width(cells) + 1 : 0) - (barWidth ? barWidth + 1 : 0)))
  const longest = Math.max(0, ...run.tasks.map(t => t.elapsed_s ?? 0))
  // A lane that has not run yet (0s) draws an empty track, not a sliver of progress.
  const filled = !task.elapsed_s || !barWidth ? 0 : Math.min(barWidth, Math.max(1, Math.round((longest ? task.elapsed_s / longest : 0) * barWidth)))
  const start: Line = [piece(`${glyph} `, color), ...label]
  if (barWidth) start.push(space(), piece('━'.repeat(filled), color), piece('·'.repeat(barWidth - filled), 'subtle'))
  if (cells.length) start.push(space(), ...cells)
  const lines = [sides(start, tail, columns)]
  const excerpt = stopped && unfinished(task) ? 'stopped before it finished' : task.status === 'fail' ?
    task.check_output_tail?.split(/\r?\n/).map(sanitize).filter(line => line.trim()).pop() : running(task) ? sanitize(task.activity ?? '').trim() : ''
  if (excerpt) lines.push(fit([piece(`  └ ${excerpt}`, task.status === 'fail' || stopped ? 'error' : 'inactive')], columns))
  return lines
}

function omitted(tasks: Task[], stopped: boolean): Line {
  const groups: [string, Task[]][] = [
    ['failed', tasks.filter(t => t.status === 'fail')],
    [stopped ? 'stopped' : 'running', tasks.filter(t => stopped ? unfinished(t) : running(t))],
    ['passed', tasks.filter(t => t.status === 'pass')],
    ['queued', tasks.filter(t => !stopped && unfinished(t) && !running(t))],
  ]
  return [piece(`+${tasks.length} more`, 'inactive'), ...groups.filter(([, group]) => group.length).flatMap(([label, group]) =>
    [separator(), piece(`${group.length} ${label}: ${group.map(t => sanitize(t.key)).join(', ')}`, 'inactive')])]
}

export function layout(snapshot: Snapshot, now: number, cfg: Config, placement: string, columns: number, rows?: number): Line[] {
  const selected = selectRun(snapshot, now)
  const run = selected.run
  const stale = isStale(snapshot, now, cfg.pollMs)
  const state = run ? runState(run, snapshot.active) : ''
  const stopped = state === 'STOPPED'
  const stateColor = stopped ? 'error' : state === 'LIVE' ? 'claude' : 'success'
  const heading = `${stale ? 'STALE · ' : snapshot.error ? 'CACHED · ' : ''}${selected.last ? 'last run · ' : ''}${state}`
  const error = snapshot.error === 'unreachable' ? `Ringside not reachable on :${cfg.port}` : 'Ringside response missing required fields'
  const errors: Line[] = snapshot.error ? [[piece(error, 'error')], [piece('./ringer.py hud --no-open', 'warning')]] : []
  const budget = Math.max(0, Math.floor(rows ?? 8))
  if (placement !== 'dock' && placement !== 'inline') return [fit([piece(summary(snapshot, now, cfg), stopped ? 'error' : 'text')], columns)]
  const tasks = run ? [...run.tasks].sort((a, b) => {
    const priority = (t: Task) => t.status === 'fail' ? 0 : running(t) ? 1 : 2
    return priority(a) - priority(b)
  }) : []
  const values = run ? totals(run, stopped) : [0, 0, 0, 0]
  const idle: Line = [piece('REFEREE', 'claude', true), separator(), piece(`${stale ? 'STALE · ' : ''}no live run`, 'inactive')]

  if (placement === 'inline') {
    const lines = [...errors]
    if (!run) return [...lines, idle].slice(0, budget).map(line => fit(line, columns))
    const left = [piece(`● ${heading} `, stateColor), piece(run.run_name, 'text', true), separator(), piece(duration(run.elapsed_s), 'text', true)]
    const right: Line = values.slice(0, stopped ? 2 : 3).flatMap((n, i) => [
      ...(i ? [separator()] : []), piece(String(n), statusColors[i], true), piece(` ${statusLabels[i]}`, 'inactive'),
    ])
    const barWidth = Math.min(24, columns - width(left) - width(right) - 2)
    let header: Line
    if (barWidth >= 6) header = sides([...left, space(), ...stacked(values, barWidth)], right, columns)
    else if (width(left) + width(right) + 1 <= columns) header = sides(left, right, columns)
    else {
      const compact = [piece(`● ${heading} `, stateColor), ...fit([piece(run.run_name, 'text', true)], Math.max(0, columns - width(right) - cellWidth(heading) - 4))]
      header = width(right) + cellWidth(heading) + 6 <= columns ? sides(compact, right, columns) : fit(left, columns)
    }
    lines.push(header)
    if (selected.more) lines.push([piece(`+${selected.more} more live`, 'claude')])
    // Even when notices fill the body, reserve a row to account for hidden tasks.
    if (tasks.length) lines.splice(Math.max(0, budget - 1))
    const lanes = tasks.map(task => laneRows(task, run, stopped, columns))
    if (lines.length + lanes.reduce((n, lane) => n + lane.length, 0) < budget) lines.splice(errors.length + 1, 0, [])
    let shown = 0
    for (const lane of lanes) {
      const available = budget - lines.length - (shown < tasks.length - 1 ? 1 : 0)
      if (available < 1) break
      lines.push(lane[0]!)
      if (lane[1] && available >= 2) lines.push(lane[1])
      shown++
    }
    if (shown < tasks.length) lines.push(omitted(tasks.slice(shown), stopped))
    return lines.slice(0, budget).map(line => fit(line, columns))
  }

  const title = run ? [piece('REFEREE', 'claude', true), separator(), piece(heading, stateColor), separator(), piece(run.run_name, 'text', true)] : idle
  const lines: Line[] = [centered(title, columns)]
  if (columns >= 40) lines.push(centered(statusLabels.flatMap((label, i) => [
    ...(i ? [space(2)] : []), piece('■', statusColors[i]), piece(` ${label}`, 'subtle'),
  ]), columns))
  const split = columns >= 110
  const bodyWidth = split ? Math.floor((columns - 2) / 2) : columns
  const left: Line[] = errors.length ? card(errors, bodyWidth, 'error') : []
  const right: Line[] = []
  if (run) {
    const inner = bodyWidth - 4
    const badge = stopped ? '○ stopped' : state === 'LIVE' ? '● live' : '✓ finished'
    const metrics: Line[] = [
      [piece(duration(run.elapsed_s), 'text', true), piece(' elapsed', 'inactive')],
      [piece(String(tasks.length), 'text', true), piece(' lanes', 'inactive')],
      [piece(String(run.max_parallel ?? '-'), 'text', true), piece(' parallel', 'inactive')],
      [piece('tokens ', 'inactive'), piece(tokenText(run.tokens), 'text', true)],
    ]
    const stats: Line = []
    for (const metric of metrics) {
      const next = [...(stats.length ? [separator()] : []), ...metric]
      if (width(stats) + width(next) > inner) break
      stats.push(...next)
    }
    const content: Line[] = [
      sides([piece(run.run_name, 'claude', true)], [piece(badge, stateColor)], inner),
      stats,
      stacked(values, inner),
      values.flatMap((n, i) => [...(i ? [space(3)] : []), piece('■ ', statusColors[i]), piece(String(n), 'text', true), piece(` ${statusLabels[i]}`, 'inactive')]),
    ]
    if (run.state === 'finished') {
      const retries = tasks.some(t => t.attempts === undefined) ? '-' : tasks.reduce((n, t) => n + Math.max(0, t.attempts! - 1), 0)
      content.push([piece('receipt', 'inactive'), separator(), piece(String(retries), 'text', true), piece(' retries', 'inactive'), separator(), piece('tokens ', 'inactive'), piece(tokenText(run.tokens), 'text', true)])
    }
    left.push(...card(content, bodyWidth, stopped ? 'error' : 'claude'))
    if (selected.more) left.push([piece(`+${selected.more} more live`, 'claude')])
    const longest = tasks.reduce<number | undefined>((n, task) => task.elapsed_s === undefined ? n : Math.max(n ?? 0, task.elapsed_s), undefined)
    right.push(sides([piece('lanes · ', 'suggestion'), piece(String(values[2]), 'suggestion', true), piece(' running · ', 'suggestion'), piece(String(tasks.length), 'suggestion', true), piece(' total', 'suggestion')],
      [piece('longest ', 'inactive'), piece(duration(longest), 'text', true)], bodyWidth))
    right.push(...tasks.flatMap(task => laneRows(task, run, stopped, bodyWidth)))
  }
  if (split) {
    for (let i = 0; i < Math.max(left.length, right.length); i++) lines.push([...pad(left[i] ?? [], bodyWidth), space(2), ...(right[i] ?? [])])
  } else lines.push(...left, ...right)
  const url = `http://127.0.0.1:${cfg.port}`
  const link: Line = [{ text: `Ringside ${url}`, color: 'suggestion', href: url }]
  lines.push(snapshot.lastSuccess === null ? fit(link, columns) : sides(link,
    [piece(`updated ${Math.max(0, Math.floor((now - snapshot.lastSuccess) / 1000))}s ago`, 'inactive')], columns))
  if (snapshot.skippedRuns) lines.push([piece(`${snapshot.skippedRuns} unreadable runs skipped`, 'warning')])
  return lines.map(line => fit(line, columns))
}
