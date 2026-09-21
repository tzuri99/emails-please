import { Box, Typography, useTheme } from '@mui/material'
import Icon from './Icon.jsx'
import { FIELD_LABEL, VERDICT } from '../theme.js'

/**
 * The paper trail for one document.
 *
 * Reads as a sequence because that is what it is: what the machine found,
 * then every human decision after it, oldest first. Entries are appended
 * server-side and never edited, so what is drawn here is the record itself
 * rather than a summary of it.
 */

function when(iso) {
  const d = new Date(iso)
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function Entry({ icon, tone, actor, headline, detail, time, last }) {
  return (
    <Box sx={{ display: 'flex', gap: 3, position: 'relative', pb: last ? 0 : 4 }}>
      {!last && (
        <Box sx={{
          position: 'absolute', left: 13, top: 28, bottom: 0, width: '1px',
          bgcolor: 'divider',
        }} />
      )}
      <Box sx={{
        width: 27, height: 27, borderRadius: '50%', flexShrink: 0,
        display: 'grid', placeItems: 'center',
        border: '1px solid', borderColor: tone.border,
        bgcolor: tone.bg, color: tone.fg, zIndex: 1,
      }}>
        <Icon name={icon} size={14} />
      </Box>

      <Box sx={{ minWidth: 0, pt: 0.25 }}>
        <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 2, flexWrap: 'wrap' }}>
          <Typography sx={{ fontSize: 13, fontWeight: 600 }}>{headline}</Typography>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            {actor}{time ? ` · ${when(time)}` : ''}
          </Typography>
        </Box>
        {detail && (
          <Typography variant="caption"
                      sx={{ display: 'block', mt: 0.5, color: 'text.secondary' }}>
            {detail}
          </Typography>
        )}
      </Box>
    </Box>
  )
}

export default function AuditTrail({ email }) {
  const theme = useTheme()
  const dark = theme.palette.mode === 'dark'
  const tone = (status) => (VERDICT[status] ?? VERDICT.NEEDS_REVIEW)[dark ? 'dark' : 'light']

  const reviews = email?.reviews ?? []
  const fieldNames = (list) =>
    (list ?? []).map((f) => FIELD_LABEL[f] ?? f).join(', ')

  const machineDetail = email.defect_fields?.length
    ? `Flagged ${fieldNames(email.defect_fields)}`
    : email.review_reason
      ? `Escalated: ${email.review_reason.replace(/_/g, ' ')}`
      : 'All seven fields agreed'

  return (
    <Box sx={{ px: 4, py: 4 }}>
      <Typography variant="h3" sx={{ mb: 3 }}>History</Typography>

      <Entry
        icon="cpu"
        tone={tone(email.status)}
        headline={`Machine: ${VERDICT[email.status]?.label ?? email.status}`}
        actor="automated check"
        detail={machineDetail}
        last={reviews.length === 0}
      />

      {reviews.map((r, i) => (
        <Entry
          key={i}
          icon={r.decision === 'agreed' ? 'check' : 'user'}
          tone={tone(r.final_status)}
          headline={r.decision === 'agreed'
            ? `Agreed: ${VERDICT[r.final_status]?.label ?? r.final_status}`
            : `Overrode to ${VERDICT[r.final_status]?.label ?? r.final_status}`}
          actor={r.reviewer || 'operator'}
          time={r.created_at}
          detail={[
            r.final_defect_fields?.length ? fieldNames(r.final_defect_fields) : null,
            r.note || null,
            r.seconds_to_decide ? `decided in ${Math.round(r.seconds_to_decide)}s` : null,
          ].filter(Boolean).join(' · ')}
          last={i === reviews.length - 1}
        />
      ))}

      {reviews.length === 0 && (
        <Typography variant="caption"
                    sx={{ display: 'block', mt: 2, color: 'text.secondary' }}>
          No human has reviewed this yet.
        </Typography>
      )}
    </Box>
  )
}
