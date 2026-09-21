import { Box, Tooltip, Typography, useTheme } from '@mui/material'

/**
 * How sure the machine is of its own reading.
 *
 * Deliberately not green/red. Confidence and correctness are different
 * axes: a genuine, correctly-detected discrepancy sits at 100% confidence,
 * and colouring that green would say "good news" about a shipment that is
 * wrong. Green and red belong to the outcome tags alone.
 *
 * So the bar is neutral by default and only takes on colour when the
 * reading itself is doubtful. Colour here always means "look at this",
 * never "this is fine".
 */

// Below this the machine cannot vouch for its own reading; it matches
// ESCALATION_THRESHOLD on the server, where the same number decides whether
// a case is reported or escalated.
export const ATTENTION_BELOW = 0.70
export const CAUTION_BELOW = 0.85

export default function Confidence({ value, width = 54, showValue = true }) {
  const theme = useTheme()
  const dark = theme.palette.mode === 'dark'

  if (value == null) {
    return <Typography sx={{ fontSize: 12, color: 'text.secondary' }}>&mdash;</Typography>
  }

  const pct = Math.round(value * 100)

  let tone, note
  if (value < ATTENTION_BELOW) {
    tone = dark ? '#FB7185' : '#E11D48'
    note = 'Low confidence — the reading itself is in doubt, so this case is escalated rather than reported'
  } else if (value < CAUTION_BELOW) {
    tone = dark ? '#FBBF24' : '#B45309'
    note = 'Moderate confidence — some interpretation was needed to compare these values'
  } else {
    // Neutral: a high-confidence reading is unremarkable, whatever the
    // outcome. Nothing here should read as reassurance.
    tone = dark ? '#94A3B8' : '#64748B'
    note = 'High confidence in the reading. This says nothing about whether the values agree.'
  }

  const track = dark ? 'rgba(148,163,184,0.20)' : '#E2E8F0'

  return (
    <Tooltip title={note} arrow>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
        <Box
          role="meter"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="reading confidence"
          sx={{
            width, height: 5, borderRadius: 3, overflow: 'hidden', flexShrink: 0,
            bgcolor: track,
          }}
        >
          <Box sx={{ width: `${pct}%`, height: '100%', bgcolor: tone, borderRadius: 3 }} />
        </Box>
        {showValue && (
          <Typography sx={{
            fontFamily: 'ui-monospace, monospace', fontSize: 11.5, color: tone,
            fontVariantNumeric: 'tabular-nums',
            fontWeight: value < CAUTION_BELOW ? 600 : 500,
          }}>
            {pct}%
          </Typography>
        )}
      </Box>
    </Tooltip>
  )
}
