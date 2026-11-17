import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, Timer } from 'claude-code'
import { config, EMPTY, fit, isStale, layout, parseRuns, runState, selectRun, summary } from './core'
import type { Config } from './core'

const snapshot = atom({ plugin: 'referee', key: 'snapshot' } as const, EMPTY, { shape: 'referee-3' })

// Status line
async function refreshStatus($: EngineInterface, cfg: Config) {
  const saved = await read($, snapshot)
  const now = await $.clock.now()
  const run = selectRun(saved, now).run
  const live = run && runState(run, saved.active) === 'LIVE'
  $.ui.status(cfg.statusLine && live ? fit([{ text: summary(saved, now, cfg) }], 120).map(part => part.text).join('') : undefined)
}

// Open pane
async function openPane($: EngineInterface, cfg: Config) {
  return $.ui.open({ id: 'referee', title: 'Referee', columns: cfg.paneColumns, rows: 8 })
}

// Fetch one Ringside snapshot; prevent overlapping requests and late session writes.
type Lifecycle = { inFlight: boolean; generation: number }
async function poll($: EngineInterface, cfg: Config, life: Lifecycle) {
  if (life.inFlight) {
    await refreshStatus($, cfg)
    $.ui.invalidate('ui.render')
    return
  }
  life.inFlight = true
  const current = life.generation
  try {
    if (!cfg.statusLine && !(await $.ui.panes()).some(pane => pane.id === 'referee')) return
    let response
    try {
      response = await $.http.fetch(`http://127.0.0.1:${cfg.port}/api/runs`, { method: 'GET' })
      if (!response.ok) throw new Error('Ringside unavailable')
    } catch {
      if (current === life.generation) await update($, snapshot, old => ({ ...old, error: 'unreachable' as const }))
      return
    }
    let data
    try {
      data = parseRuns(response.text)
    } catch {
      if (current === life.generation) await update($, snapshot, old => ({ ...old, error: 'contract' as const }))
      return
    }
    const now = await $.clock.now()
    if (current === life.generation) await update($, snapshot, () => ({ ...data, lastSuccess: now, error: null }))
  } finally {
    life.inFlight = false
    if (current === life.generation) {
      await refreshStatus($, cfg)
      $.ui.invalidate('ui.render')
    }
  }
}

// Hooks and polling lifecycle
export const register: Register = (on, options) => {
  const cfg = config(options)
  let timer: Timer | undefined
  const life: Lifecycle = { inFlight: false, generation: 0 }

  on('session.start', async ($, e, next) => {
    timer?.cancel()
    life.generation++
    await $.command.register({ name: 'referee', description: 'Watch the Ringer run: open, close, or status', argumentHint: '[open|close|status]' })
    if (cfg.openOnStart) await openPane($, cfg)
    timer = $.clock.every(cfg.pollMs, () => { void poll($, cfg, life) })
    await poll($, cfg, life)
    return next(e)
  })

  on('session.end', async ($, e, next) => {
    if (e.reason === 'clear' || e.reason === 'resume') return next(e)
    life.generation++
    timer?.cancel()
    timer = undefined
    $.ui.status(undefined)
    return next(e)
  })

  on('command.run', { command: 'referee' }, async ($, e) => {
    const verb = e.args.trim() || 'open'
    if (verb === 'close') {
      await $.ui.close({ id: 'referee' })
      return { text: 'Referee closed.' }
    }
    if (verb === 'status') return { text: summary(await read($, snapshot), await $.clock.now(), cfg) }
    if (verb !== 'open') return { text: 'Usage: /referee open|close|status' }
    const opened = await openPane($, cfg)
    if (!timer) timer = $.clock.every(cfg.pollMs, () => { void poll($, cfg, life) })
    await poll($, cfg, life)
    return { text: opened.isPlaced ? 'Referee opened.' : `Referee is not shown: ${opened.reason}` }
  })

  on('ui.render', { component: 'Pane', requestId: 'referee' }, async ($, e) => {
    const { Box, Text, Link } = $.ui.resolve(e)
    const saved = await read($, snapshot)
    const now = await $.clock.now()
    const stale = isStale(saved, now, cfg.pollMs)
    const lines = layout(saved, now, cfg, e.props.placement, e.props.bodyColumns, e.props.scroll.bodyRows)
    return (
      <Box flexDirection="column" width={e.props.bodyColumns}>
        {lines.map(line => (
          <Text dimColor={stale} wrap="truncate" maxLines={1}>
            {line.map(part => part.href && e.surface === 'terminal' ? <Link href={part.href}>{part.text}</Link> : <Text color={part.color}>{part.text}</Text>)}
          </Text>
        ))}
      </Box>
    )
  })
}
