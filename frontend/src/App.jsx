import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Box, Button, CssBaseline, Dialog, DialogActions, DialogContent,
  DialogContentText, DialogTitle, Tooltip, Typography,
} from '@mui/material'
import { ThemeProvider } from '@mui/material/styles'
import DiffPane from './components/DiffPane.jsx'
import Icon from './components/Icon.jsx'
import QueuePane, { FILTERS } from './components/QueuePane.jsx'
import ReviewPane from './components/ReviewPane.jsx'
import { api } from './api.js'
import { WORDMARK, buildTheme } from './theme.js'

/**
 * Three-pane triage: queue, comparison, decision.
 *
 * Fixed height with each pane scrolling on its own, so the comparison never
 * shifts under the reviewer as they work down the queue. A decision
 * advances the selection automatically: hands stay on the keyboard and the
 * next case is already on screen.
 *
 * Shortcut safety has two rules:
 *  - Clearing a flagged document is press-and-hold, not a keypress. It is
 *    the only action that can release a real defect.
 *  - It also stays locked until every flagged field has been opened, so an
 *    operator cannot wave through something they have not looked at.
 */

function usePreferredMode() {
  const [mode, setMode] = useState(() => {
    try {
      const saved = localStorage.getItem('sdoc-mode')
      if (saved === 'light' || saved === 'dark') return saved
    } catch { /* private window, blocked storage */ }
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })

  useEffect(() => {
    try { localStorage.setItem('sdoc-mode', mode) } catch { /* not essential */ }
  }, [mode])

  return [mode, () => setMode((m) => (m === 'dark' ? 'light' : 'dark'))]
}

function useRun() {
  const [run, setRun] = useState(null)
  const [error, setError] = useState(null)
  const timer = useRef(null)
  const cancelled = useRef(false)

  // `poll` is a ref-backed callback rather than a local inside the mount
  // effect, so the "Run again" buttons drive the SAME loop the initial load
  // does.
  //
  // It used to live inside the effect, which meant a button could only do
  // `setRun(await api.startRun())` -- storing a run whose status is
  // "running" and then never asking again. The backend finished in about
  // ten seconds; the UI sat on "Reading the inbox" indefinitely, which
  // reads exactly like a hung run on a slow database.
  const poll = useCallback(async (id) => {
    try {
      const r = await api.getRun(id)
      if (cancelled.current) return
      setRun(r)
      if (r.status === 'running') {
        timer.current = setTimeout(() => poll(id), 1000)
      }
    } catch (e) {
      if (!cancelled.current) setError(e.message)
    }
  }, [])

  // Both ways of beginning a run share one path, so neither can forget to
  // poll -- which is the bug that made "Run again" look like it hung.
  const begin = useCallback(async (request) => {
    try {
      setError(null)
      const r = await request()
      if (cancelled.current) return
      setRun(r)
      poll(r.id)
    } catch (e) {
      if (!cancelled.current) setError(e.message)
    }
  }, [poll])

  const start = useCallback(() => begin(api.startRun), [begin])
  const reset = useCallback(() => begin(api.resetDemo), [begin])

  useEffect(() => {
    cancelled.current = false
    ;(async () => {
      try {
        const runs = await api.listRuns()
        const latest = runs.find((r) => r.status === 'succeeded') ?? runs[0]
        if (latest) return poll(latest.id)
        await start()
      } catch (e) { if (!cancelled.current) setError(e.message) }
    })()

    return () => { cancelled.current = true; clearTimeout(timer.current) }
  }, [poll, start])

  return { run, start, reset, error }
}

export default function App() {
  const [mode, toggleMode] = usePreferredMode()
  const theme = useMemo(() => buildTheme(mode), [mode])

  const { run, start, reset, error } = useRun()
  const [confirmReset, setConfirmReset] = useState(false)
  const [filter, setFilter] = useState('triage')
  const [category, setCategory] = useState('')
  const [emails, setEmails] = useState([])
  const [loading, setLoading] = useState(false)
  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [stats, setStats] = useState(null)
  const [busy, setBusy] = useState(false)

  const [openFields, setOpenFields] = useState(new Set())
  const [acknowledged, setAcknowledged] = useState(new Set())
  const [focusedField, setFocusedField] = useState(null)
  const openedAt = useRef(Date.now())

  const ready = run?.status === 'succeeded'

  /**
   * Two different sets, because they answer two different questions.
   *
   * `flaggedFields` is what could actually be a defect, and it gates
   * "Clear it" -- a formatting artifact is not a risk worth blocking on.
   *
   * `taggedFields` is everything carrying a tag, which is what the reviewer
   * can see on screen. N walks this set: navigation that skipped visible
   * rows was the reason the cursor appeared to stick between two fields.
   */
  const flaggedFields = useMemo(() => (detail?.field_verdicts ?? [])
    .filter((fv) => fv.verdict !== 'MATCH')
    .map((fv) => fv.field), [detail])

  const taggedFields = useMemo(() => (detail?.field_verdicts ?? [])
    .filter((fv) => fv.kind && fv.kind !== 'match')
    .map((fv) => fv.field), [detail])

  /**
   * Which message types can appear in the selected state.
   *
   * Undefined for views that are not state-scoped ("Everything",
   * "Reviewed"), where every type is possible.
   */
  const availableTypes = useMemo(() => {
    const stateKey = FILTERS.find((f) => f.key === filter)?.params?.state
    if (!stateKey) return undefined
    return stats?.by_state_category?.[stateKey] ?? {}
  }, [filter, stats])

  const loadList = useCallback(async () => {
    if (!ready) return
    setLoading(true)
    try {
      // State and type are independent axes; both narrow the same query.
      const params = { ...(FILTERS.find((f) => f.key === filter)?.params ?? {}) }
      if (category) params.category = category
      const [rows, s] = await Promise.all([api.emails(run.id, params), api.stats(run.id)])
      setEmails(rows)
      setStats(s)
      setSelectedId((cur) =>
        rows.some((r) => r.email_id === cur) ? cur : rows[0]?.email_id ?? null)
    } finally { setLoading(false) }
  }, [ready, run?.id, filter, category])

  useEffect(() => { loadList() }, [loadList])

  // Changing state can invalidate the chosen type; drop it rather than
  // leaving the operator staring at an empty list.
  useEffect(() => {
    if (category && availableTypes && !availableTypes[category]) setCategory('')
  }, [category, availableTypes])

  useEffect(() => {
    if (!ready || !selectedId) { setDetail(null); return }
    let cancelled = false
    openedAt.current = Date.now()
    setOpenFields(new Set())
    setAcknowledged(new Set())
    setFocusedField(null)
    api.email(run.id, selectedId).then((d) => { if (!cancelled) setDetail(d) })
    return () => { cancelled = true }
  }, [ready, run?.id, selectedId])

  const acknowledge = useCallback((field) => {
    // Opening a flagged field is the acknowledgement; it never un-acknowledges.
    setAcknowledged((cur) => new Set(cur).add(field))
    setFocusedField(field)
  }, [])

  /** Clicking a row toggles it. */
  const toggleField = useCallback((field) => {
    setOpenFields((cur) => {
      const next = new Set(cur)
      next.has(field) ? next.delete(field) : next.add(field)
      return next
    })
    acknowledge(field)
  }, [acknowledge])

  /** Keyboard navigation opens; it never closes what it lands on. */
  const openField = useCallback((field) => {
    setOpenFields((cur) => new Set(cur).add(field))
    acknowledge(field)
  }, [acknowledge])

  const index = useMemo(
    () => emails.findIndex((e) => e.email_id === selectedId), [emails, selectedId])

  const step = useCallback((delta) => {
    if (!emails.length) return
    setSelectedId(emails[Math.min(Math.max(index + delta, 0), emails.length - 1)].email_id)
  }, [emails, index])

  /**
   * Jump to the next flagged field — the N key.
   *
   * Unvisited fields come first, so N walks the whole set before it starts
   * cycling. Previously it advanced by index and *toggled*, so with two
   * flagged fields the third press closed the row it had just opened and
   * the cursor appeared to stick between them.
   */
  const nextFlagged = useCallback(() => {
    const walk = taggedFields.length ? taggedFields : flaggedFields
    if (!walk.length) return
    const at = walk.indexOf(focusedField)
    openField(walk[(at + 1) % walk.length])
  }, [taggedFields, flaggedFields, focusedField, openField])

  const decide = useCallback(async (payload) => {
    if (!detail || busy) return
    setBusy(true)
    try {
      const updated = await api.review(run.id, detail.email_id, {
        ...payload, seconds_to_decide: (Date.now() - openedAt.current) / 1000,
      })
      setEmails((cur) => cur.map((e) => (e.email_id === updated.email_id ? updated : e)))
      api.stats(run.id).then(setStats)
      api.email(run.id, detail.email_id).then(setDetail)
      step(1)
    } finally { setBusy(false) }
  }, [detail, busy, run?.id, step])

  const undo = useCallback(async () => {
    if (!detail || busy) return
    setBusy(true)
    try {
      const updated = await api.clearReview(run.id, detail.email_id)
      setEmails((cur) => cur.map((e) => (e.email_id === updated.email_id ? updated : e)))
      api.stats(run.id).then(setStats)
      api.email(run.id, detail.email_id).then(setDetail)
    } finally { setBusy(false) }
  }, [detail, busy, run?.id])

  const retry = useCallback(async () => {
    if (!detail || busy) return
    setBusy(true)
    try {
      const updated = await api.retry(run.id, detail.email_id)
      setDetail(updated)
      setEmails((cur) => cur.map((e) => (e.email_id === updated.email_id
        ? { ...e, ...updated } : e)))
      api.stats(run.id).then(setStats)
    } finally { setBusy(false) }
  }, [detail, busy, run?.id])

  useEffect(() => {
    const onKey = (e) => {
      const tag = e.target.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || e.metaKey || e.ctrlKey) return
      const k = e.key.toLowerCase()
      const unacked = flaggedFields.filter((f) => !acknowledged.has(f))

      if (k === 'j') { e.preventDefault(); step(-1) }
      else if (k === 'k') { e.preventDefault(); step(1) }
      else if (k === 'n') { e.preventDefault(); nextFlagged() }
      else if (k === 'c') {
        e.preventDefault()
        // Clearing a flagged document is hold-to-confirm in the panel; the
        // keyboard deliberately has no one-press path to it.
        if (detail?.status !== 'MISMATCH' && unacked.length === 0) {
          decide({ final_status: 'OK' })
        }
      } else if (k === 'd' && detail?.defect_fields?.length) {
        e.preventDefault()
        decide({ final_status: 'MISMATCH', final_defect_fields: detail.defect_fields })
      } else if (k === 'e') { e.preventDefault(); decide({ final_status: 'NEEDS_REVIEW' }) }
      else if (k === 'u') { e.preventDefault(); undo() }
      else if (k === 'r' && detail?.case_state === 'UNREADABLE') {
        e.preventDefault(); retry()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [step, nextFlagged, decide, undo, retry, detail, flaggedFields, acknowledged])

  const shell = (children) => (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      {children}
    </ThemeProvider>
  )

  if (error) {
    return shell(
      <Box sx={{ p: 8 }}>
        <Typography variant="h1" sx={{ mb: 2 }}>The service is unreachable</Typography>
        <Typography variant="body2" sx={{ mb: 1 }}>{error}</Typography>
        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          Check the API is running, then reload.
        </Typography>
      </Box>)
  }

  if (!ready) {
    return shell(
      <Box sx={{ p: 8 }}>
        <Typography variant="h1" sx={{ mb: 2 }}>Reading the inbox</Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          {run?.status === 'failed'
            ? `The last run failed: ${run.error}`
            : 'Classifying messages and comparing documents.'}
        </Typography>
        {run?.status === 'failed' && (
          <Button variant="contained" sx={{ mt: 4 }} onClick={start}>
            Try again
          </Button>
        )}
      </Box>)
  }

  return shell(
    <Box sx={{ height: '100vh', display: 'flex', flexDirection: 'column', bgcolor: 'background.default' }}>
      <Box component="header" sx={{
        display: 'flex', alignItems: 'center', gap: 6, px: 5, py: 3.5,
        borderBottom: '1px solid', borderColor: 'divider', bgcolor: 'background.paper',
      }}>
        {/* Stacked over two lines, so the serif reads as a masthead and the
            header stays short enough for the stat row beside it. */}
        <Typography component="h1" sx={WORDMARK}>
          <Box component="span" sx={{ display: 'block' }}>Emails,</Box>
          <Box component="span" sx={{ display: 'block' }}>Please</Box>
        </Typography>

        <Box sx={{ display: 'flex', gap: 6 }}>
          {/* Every figure reads from stats.by_state, the same source the
              sidebar tabs use, so the two cannot drift apart. */}
          <Stat value={run.total_emails} label="messages" />
          <Stat value={stats?.by_state?.MISMATCH ?? 0} label="discrepancies" tone="error.main" />
          <Stat value={stats?.by_state?.NEEDS_REVIEW ?? 0} label="need a human" tone="warning.main" />
          <Stat value={stats?.by_state?.MISSING_ATTACHMENT ?? 0} label="awaiting doc" tone="#7C3AED" />
          <Stat value={stats?.by_state?.WRONG_DOCUMENT ?? 0} label="wrong doc" tone="#C2410C" />
          <Stat value={stats?.by_state?.UNREADABLE ?? 0} label="unreadable" tone="text.secondary" />
          <Stat value={stats?.reviewed ?? 0} label="reviewed" tone="success.main" />
        </Box>

        <Box sx={{ flex: 1 }} />

        <Tooltip title={mode === 'dark' ? 'Switch to light' : 'Switch to dark'} arrow>
          <Box component="button" onClick={toggleMode} aria-label="Toggle colour scheme" sx={{
            display: 'grid', placeItems: 'center', width: 34, height: 34,
            borderRadius: '7px', cursor: 'pointer', color: 'text.secondary',
            border: '1px solid', borderColor: 'divider', bgcolor: 'transparent',
            '&:hover': { bgcolor: 'action.hover' },
          }}>
            <Icon name={mode === 'dark' ? 'sun' : 'moon'} size={16} />
          </Box>
        </Tooltip>
        <Button size="small" onClick={start}>Run again</Button>
        {/* The demo has no login, so one workspace is shared by everyone
            looking at it. This is how the next person gets a clean start
            without having to undo someone else's decisions by hand. */}
        <Tooltip title="Clear all review decisions and re-run" arrow>
          <Button size="small" color="warning" onClick={() => setConfirmReset(true)}>
            Reset demo
          </Button>
        </Tooltip>
        <Button size="small" variant="outlined" target="_blank" rel="noopener"
                href={`/api/runs/${run.id}/submission`}>
          Export report
        </Button>
      </Box>

      <Box sx={{
        flex: 1, minHeight: 0, display: 'grid',
        gridTemplateColumns: '308px minmax(0, 1fr) 348px',
        gridTemplateRows: '100%', overflow: 'hidden',
        // Grid items default to min-height:auto; without this the panes grow
        // to their content and the whole page scrolls instead of each pane.
        '& > *': { minHeight: 0, height: '100%', overflow: 'hidden' },
        '@media (max-width: 1180px)': { gridTemplateColumns: '260px minmax(0, 1fr)' },
      }}>
        <QueuePane
          emails={emails} selectedId={selectedId} onSelect={setSelectedId}
          filter={filter} onFilter={setFilter} loading={loading}
          counts={{ ...(stats?.by_state ?? {}), reviewed: stats?.reviewed }}
          category={category} onCategory={setCategory}
          categoryCounts={stats?.by_category}
          availableTypes={availableTypes}
        />
        <DiffPane
          email={detail} openFields={openFields} runId={run.id}
          onToggleField={toggleField} focusedField={focusedField}
        />
        <Box sx={{ '@media (max-width: 1180px)': { display: 'none' } }}>
          <ReviewPane
            email={detail} onDecide={decide} onUndo={undo} onRetry={retry} runId={run.id}
            busy={busy} acknowledged={acknowledged} flaggedFields={flaggedFields}
          />
        </Box>
      </Box>

      <Box component="footer" sx={{
        display: 'flex', gap: 5, px: 5, py: 2, flexWrap: 'wrap',
        borderTop: '1px solid', borderColor: 'divider', bgcolor: 'background.paper',
      }}>
        {[['J / K', 'up / down'], ['N', 'next flagged field'], ['C', 'clear'],
          ['D', 'discrepancy'], ['E', 'second look'], ['R', 'retry extraction'],
          ['U', 'undo']].map(([k, d]) => (
          <Box key={k} sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Typography sx={{
              fontFamily: 'ui-monospace, monospace', fontSize: 10.5, px: 0.75,
              borderRadius: '4px', border: '1px solid', borderColor: 'divider',
              color: 'text.secondary',
            }}>{k}</Typography>
            <Typography sx={{ fontSize: 11, color: 'text.secondary' }}>{d}</Typography>
          </Box>
        ))}
      </Box>

      {/* Confirmed, not press-and-hold. Press-and-hold guards clearing one
          flagged shipment, where the risk is a reflex on the wrong row. This
          is rarer, deliberate, and affects everyone's work at once, so what
          it needs is a sentence explaining the blast radius. */}
      <Dialog open={confirmReset} onClose={() => setConfirmReset(false)}>
        <DialogTitle>Reset the demo?</DialogTitle>
        <DialogContent>
          <DialogContentText>
            This deletes <strong>every review decision</strong> and starts a
            fresh run, so the next person sees the machine's verdicts with
            nothing already decided.
          </DialogContentText>
          <DialogContentText sx={{ mt: 2 }}>
            There is no sign-in, so this workspace is shared — the reset
            applies to anyone else looking at the demo right now, and it
            cannot be undone. The run takes about ten seconds.
          </DialogContentText>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setConfirmReset(false)}>Cancel</Button>
          <Button color="warning" variant="contained" onClick={() => {
            setConfirmReset(false)
            reset()
          }}>
            Clear decisions and re-run
          </Button>
        </DialogActions>
      </Dialog>
    </Box>)
}

function Stat({ value, label, tone }) {
  return (
    <Box>
      <Typography sx={{
        fontFamily: 'ui-monospace, monospace', fontSize: 18, fontWeight: 650,
        lineHeight: 1.1, fontVariantNumeric: 'tabular-nums', color: tone ?? 'text.primary',
      }}>
        {value}
      </Typography>
      <Typography sx={{ fontSize: 11.5, color: 'text.secondary' }}>{label}</Typography>
    </Box>
  )
}
