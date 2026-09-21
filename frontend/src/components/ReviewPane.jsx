import { useEffect, useRef, useState } from 'react'
import { Box, Button, TextField, Tooltip, Typography, useTheme } from '@mui/material'
import DraftReply from './DraftReply.jsx'
import Icon from './Icon.jsx'
import { CATEGORY_LABEL, FIELD_LABEL, REASON_LABEL, VERDICT } from '../theme.js'

/**
 * The decision panel.
 *
 * It asks one question -- what is true for this shipment? -- and offers the
 * three possible answers. The machine's proposal is badged on whichever
 * answer it picked, so agreeing is simply choosing that one.
 *
 * The previous version asked a different question ("do you agree?") and then
 * also offered the outcomes, which meant "Agree" and "Mark clear" were the
 * same button whenever the machine had already said clear. Two controls for
 * one action is not a labelling problem; it is the wrong question.
 *
 * Two safeguards, both about releasing a flagged document:
 *
 *  - Clearing a shipment the machine flagged is press-and-hold. It is the
 *    one action here that can let a real defect through.
 *  - It is also disabled until every flagged field has been opened. The
 *    operator has to have looked at what they are waving through.
 */

const HOLD_MS = 800

// Message types with no shipment to make a judgement about.
const NO_SHIPMENT = new Set(['SPAM', 'GENERAL'])

// Verb + noun, so the button says what it does without a sentence.
const ACK = {
  SPAM: {
    heading: 'Junk message',
    detail: 'Nothing to check. Filing it keeps the queue honest.',
    action: 'Discard message',
    icon: 'alert',
  },
  GENERAL: {
    heading: 'No action needed',
    detail: 'An operational update with no document to compare.',
    action: 'Mark handled',
    icon: 'check',
  },
}

function useHold(onComplete, enabled) {
  const [progress, setProgress] = useState(0)
  const frame = useRef(null)
  const start = useRef(0)

  const stop = () => {
    cancelAnimationFrame(frame.current)
    frame.current = null
    setProgress(0)
  }

  const tick = () => {
    const elapsed = Date.now() - start.current
    const pct = Math.min(elapsed / HOLD_MS, 1)
    setProgress(pct)
    if (pct >= 1) {
      stop()
      onComplete()
    } else {
      frame.current = requestAnimationFrame(tick)
    }
  }

  const begin = () => {
    if (!enabled || frame.current) return
    start.current = Date.now()
    frame.current = requestAnimationFrame(tick)
  }

  useEffect(() => () => cancelAnimationFrame(frame.current), [])
  return { progress, begin, stop, holding: progress > 0 }
}

function Outcome({
  tone, icon, title, detail, shortcut, proposed, disabled, disabledReason,
  onAct, hold, holdProgress,
}) {
  const theme = useTheme()
  const dark = theme.palette.mode === 'dark'
  const c = tone[dark ? 'dark' : 'light']

  const body = (
    <Box
      component="button"
      type="button"
      disabled={disabled}
      onClick={hold ? undefined : onAct}
      onMouseDown={hold ? onAct : undefined}
      onMouseUp={hold ? onAct?.stop : undefined}
      sx={{
        width: '100%', textAlign: 'left', font: 'inherit', position: 'relative',
        display: 'flex', gap: 3, alignItems: 'flex-start', overflow: 'hidden',
        px: 3.5, py: 3, borderRadius: 2, cursor: disabled ? 'not-allowed' : 'pointer',
        border: '1px solid', borderColor: disabled ? 'divider' : c.border,
        bgcolor: disabled ? 'transparent' : c.bg,
        color: disabled ? 'text.secondary' : c.fg,
        opacity: disabled ? 0.55 : 1,
        transition: 'background-color 160ms, border-color 160ms',
        '&:hover': disabled ? {} : { filter: dark ? 'brightness(1.25)' : 'brightness(0.97)' },
      }}
    >
      {holdProgress > 0 && (
        <Box sx={{
          position: 'absolute', inset: 0, width: `${holdProgress * 100}%`,
          bgcolor: c.fg, opacity: 0.18, pointerEvents: 'none',
        }} />
      )}
      <Box sx={{ mt: 0.25, color: 'inherit' }}><Icon name={icon} size={17} /></Box>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <Typography sx={{ fontSize: 13.5, fontWeight: 600, color: 'inherit' }}>
            {title}
          </Typography>
          {proposed && (
            // Explicit background: `bgcolor: currentColor` on an element that
            // also sets `color` resolves both to the same value, which paints
            // the label invisibly onto its own chip.
            <Typography sx={{
              fontSize: 10.5, fontWeight: 650, px: 1.25, py: 0.35, borderRadius: '4px',
              letterSpacing: '0.02em', whiteSpace: 'nowrap',
              bgcolor: c.fg, color: dark ? '#0F172A' : '#FFFFFF',
            }}>
              AI picked this
            </Typography>
          )}
          <Box sx={{ flex: 1 }} />
          <Typography sx={{
            fontSize: 11, fontFamily: 'ui-monospace, monospace', opacity: 0.75,
            border: '1px solid currentColor', borderRadius: '4px', px: 0.75,
          }}>
            {shortcut}
          </Typography>
        </Box>
        <Typography sx={{ fontSize: 12, mt: 0.5, color: 'inherit', opacity: 0.85 }}>
          {disabled ? disabledReason : detail}
        </Typography>
      </Box>
    </Box>
  )

  return disabled && disabledReason
    ? <Tooltip title={disabledReason} arrow placement="left">
        <span style={{ display: 'block' }}>{body}</span>
      </Tooltip>
    : body
}

export default function ReviewPane({
  email, onDecide, onUndo, onRetry, busy, acknowledged, flaggedFields, runId,
}) {
  const [note, setNote] = useState('')
  const [fields, setFields] = useState([])

  useEffect(() => {
    setNote('')
    setFields(email?.defect_fields ?? [])
  }, [email?.email_id])

  const unacknowledged = (flaggedFields ?? []).filter((f) => !acknowledged.has(f))
  const machineFlagged = email?.status === 'MISMATCH'
  const clearIsRisky = machineFlagged
  const clearBlocked = unacknowledged.length > 0

  const doClear = () => onDecide({ final_status: 'OK', note })
  const holdClear = useHold(doClear, clearIsRisky && !clearBlocked && !busy)

  if (!email) return <Box sx={{ borderLeft: '1px solid', borderColor: 'divider' }} />

  const standing = email.review
  const proposal = email.status

  // Nothing to compare until the document arrives, so the panel offers
  // the one move that helps: ask the sender for it.
  if (email.case_state === 'MISSING_ATTACHMENT'
      || email.case_state === 'WRONG_DOCUMENT') {
    const wrongDoc = email.case_state === 'WRONG_DOCUMENT'
    return (
      <Box sx={{
        height: '100%', overflowY: 'auto', display: 'flex', flexDirection: 'column',
        borderLeft: '1px solid', borderColor: 'divider', bgcolor: 'background.paper',
      }}>
        <Box sx={{ px: 4, pt: 4, pb: 3 }}>
          <Typography variant="h2">
            {wrongDoc ? 'Wrong document attached' : 'Waiting on a document'}
          </Typography>
          <Typography variant="caption" sx={{ display: 'block', mt: 1, color: 'text.secondary' }}>
            {wrongDoc
              ? 'The file that arrived is not the one this check needs.'
              : 'Nothing to compare until it arrives.'}
          </Typography>
        </Box>

        {standing && (
          <Box sx={{
            mx: 4, mb: 3, px: 3, py: 2.5, borderRadius: 2,
            border: '1px solid', borderColor: 'divider', bgcolor: 'action.hover',
          }}>
            <Typography sx={{ fontSize: 12.5, fontWeight: 600 }}>
              Request sent
            </Typography>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              by {standing.reviewer || 'operator'}
            </Typography>
          </Box>
        )}

        <Box sx={{ px: 4, pb: 4 }}>
          <DraftReply
            runId={runId} emailId={email.email_id} busy={busy}
            sent={Boolean(standing)}
            onMarkSent={() => onDecide({
              final_status: 'NEEDS_REVIEW',
              note: wrongDoc
                ? 'Asked the sender to send the correct document.'
                : 'Requested the missing document from the sender.',
            })}
          />
        </Box>

        {standing && (
          <Box sx={{ px: 4 }}>
            <Button size="small" disabled={busy} onClick={onUndo}>Undo (U)</Button>
          </Box>
        )}
      </Box>
    )
  }

  // Spam and general traffic carry no shipment, so asking what is true
  // about one reads as a bug. They get the single action that actually
  // applies: acknowledge and move on.
  if (NO_SHIPMENT.has(email.category)) {
    return (
      <Box sx={{
        height: '100%', overflowY: 'auto', display: 'flex', flexDirection: 'column',
        borderLeft: '1px solid', borderColor: 'divider', bgcolor: 'background.paper',
      }}>
        <Box sx={{ px: 4, pt: 4, pb: 3 }}>
          <Typography variant="h2">{ACK[email.category].heading}</Typography>
          <Typography variant="caption" sx={{ display: 'block', mt: 1, color: 'text.secondary' }}>
            {ACK[email.category].detail}
          </Typography>
        </Box>

        {standing && (
          <Box sx={{
            mx: 4, mb: 3, px: 3, py: 2.5, borderRadius: 2,
            border: '1px solid', borderColor: 'divider', bgcolor: 'action.hover',
          }}>
            <Typography sx={{ fontSize: 12.5, fontWeight: 600 }}>Handled</Typography>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              by {standing.reviewer || 'operator'}
            </Typography>
          </Box>
        )}

        <Box sx={{ px: 4 }}>
          <Button
            fullWidth variant="contained" disabled={busy || Boolean(standing)}
            startIcon={<Icon name={ACK[email.category].icon} size={15} />}
            onClick={() => onDecide({ final_status: 'OK', note })}
          >
            {ACK[email.category].action}
          </Button>
          {standing && (
            <Button size="small" disabled={busy} onClick={onUndo} sx={{ mt: 2 }}>
              Undo (U)
            </Button>
          )}
        </Box>

        <Box sx={{ px: 4, pt: 4 }}>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            Classified as {CATEGORY_LABEL[email.category] ?? email.category}
            {email.route_reason ? ` — ${email.route_reason}` : ''}.
          </Typography>
        </Box>
      </Box>
    )
  }

  return (
    <Box sx={{
      height: '100%', overflowY: 'auto', display: 'flex', flexDirection: 'column',
      borderLeft: '1px solid', borderColor: 'divider', bgcolor: 'background.paper',
    }}>
      {email.case_state === 'UNREADABLE' && (
        // A parse failure is not a judgement call: the useful first move is
        // to try again, so it sits above the outcomes rather than among them.
        <Box sx={{
          mx: 4, mt: 4, px: 3.5, py: 3, borderRadius: 2,
          border: '1px solid', borderColor: 'divider', bgcolor: 'action.hover',
        }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 1 }}>
            <Icon name="fileWarning" size={16} />
            <Typography sx={{ fontSize: 13, fontWeight: 650 }}>
              Could not read the attachment
            </Typography>
          </Box>
          <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', mb: 2.5 }}>
            {REASON_LABEL[email.review_reason] ?? 'The document would not parse'}.
            Re-running extraction is worth a try before deciding.
          </Typography>
          <Button variant="contained" size="small" disabled={busy} onClick={onRetry}
                  startIcon={<Icon name="retry" size={14} />}>
            Retry extraction (R)
          </Button>
        </Box>
      )}

      <Box sx={{ px: 4, pt: 4, pb: 3 }}>
        <Typography variant="h2">What is true for this shipment?</Typography>
      </Box>

      {standing && (
        <Box sx={{
          mx: 4, mb: 3, px: 3, py: 2.5, borderRadius: 2,
          border: '1px solid', borderColor: 'divider', bgcolor: 'action.hover',
        }}>
          <Typography sx={{ fontSize: 12.5, fontWeight: 600 }}>
            Recorded as {VERDICT[standing.final_status]?.label ?? standing.final_status}
          </Typography>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            by {standing.reviewer || 'operator'}
            {standing.decision === 'overridden' && ' · overrode the machine'}
          </Typography>
        </Box>
      )}

      <Box sx={{ px: 4, display: 'flex', flexDirection: 'column', gap: 2 }}>
        <Outcome
          tone={VERDICT.OK}
          icon="check"
          title="Clear it"
          detail={clearIsRisky
            ? 'Release the draft despite the flag. Hold to confirm.'
            : 'No discrepancy. The draft can be finalised.'}
          shortcut="C"
          proposed={proposal === 'OK'}
          disabled={busy || clearBlocked}
          disabledReason={clearBlocked
            ? `Open ${unacknowledged.length} flagged field${unacknowledged.length > 1 ? 's' : ''} first`
            : null}
          hold={clearIsRisky}
          holdProgress={holdClear.progress}
          onAct={clearIsRisky
            ? Object.assign(holdClear.begin, { stop: holdClear.stop })
            : doClear}
        />

        <Outcome
          tone={VERDICT.MISMATCH}
          icon="alert"
          title="Confirm discrepancy"
          detail={fields.length
            ? `${fields.length} field${fields.length > 1 ? 's' : ''} wrong: ${fields.map((f) => FIELD_LABEL[f] ?? f).join(', ')}`
            : 'Select at least one field below.'}
          shortcut="D"
          proposed={proposal === 'MISMATCH'}
          disabled={busy || fields.length === 0}
          disabledReason={fields.length === 0 ? 'Choose which fields are wrong' : null}
          onAct={() => onDecide({
            final_status: 'MISMATCH', final_defect_fields: fields, note,
          })}
        />

        <Outcome
          tone={VERDICT.NEEDS_REVIEW}
          icon="help"
          title="Send for a second look"
          detail="You cannot decide from these documents alone."
          shortcut="E"
          proposed={proposal === 'NEEDS_REVIEW'}
          disabled={busy}
          onAct={() => onDecide({ final_status: 'NEEDS_REVIEW', note })}
        />
      </Box>

      {email.field_verdicts?.length > 0 && (
        <Box sx={{ px: 4, pt: 4 }}>
          <Typography variant="h3" sx={{ mb: 1.5 }}>Which fields are wrong?</Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1.5 }}>
            {email.field_verdicts.map((fv) => {
              const on = fields.includes(fv.field)
              return (
                <Box
                  key={fv.field}
                  role="checkbox"
                  aria-checked={on}
                  tabIndex={0}
                  onClick={() => setFields((cur) =>
                    cur.includes(fv.field) ? cur.filter((x) => x !== fv.field) : [...cur, fv.field])}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      setFields((cur) => cur.includes(fv.field)
                        ? cur.filter((x) => x !== fv.field) : [...cur, fv.field])
                    }
                  }}
                  sx={{
                    display: 'flex', alignItems: 'center', gap: 1,
                    px: 2, py: 1.25, fontSize: 12, cursor: 'pointer', borderRadius: '6px',
                    border: '1px solid',
                    borderColor: on ? 'error.main' : 'divider',
                    color: on ? 'error.main' : 'text.secondary',
                    bgcolor: on ? 'transparent' : 'transparent',
                    '&:hover': { borderColor: on ? 'error.main' : 'text.secondary' },
                  }}
                >
                  {on && <Icon name="check" size={13} />}
                  {FIELD_LABEL[fv.field] ?? fv.field}
                </Box>
              )
            })}
          </Box>
        </Box>
      )}

      <Box sx={{ px: 4, pt: 3, pb: 4 }}>
        <TextField
          label="Why (optional)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          multiline minRows={2} size="small" fullWidth
          helperText="Stored permanently with your decision."
        />
        {standing && (
          <Button size="small" disabled={busy} onClick={onUndo} sx={{ mt: 2 }}>
            Withdraw my decision (U)
          </Button>
        )}
      </Box>
    </Box>
  )
}
