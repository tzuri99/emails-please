import { Box, Typography, useTheme } from '@mui/material'
import Confidence from './Confidence.jsx'
import Icon from './Icon.jsx'
import TypeBadge from './TypeBadge.jsx'
import { CATEGORIES, CATEGORY_LABEL, VERDICT } from '../theme.js'

/**
 * The work queue, served least-certain first.
 *
 * Each row names its verdict in words next to the icon; the tint is the
 * third cue, never the only one. Rows are deliberately taller than the
 * data strictly needs — an operator scanning a few hundred of these all
 * day reads a list with air in it faster than a packed one.
 */

export const FILTERS = [
  { key: 'triage', label: 'Needs a human', params: { state: 'NEEDS_REVIEW' }, stateKey: 'NEEDS_REVIEW' },
  { key: 'defects', label: 'Discrepancies', params: { state: 'MISMATCH' }, stateKey: 'MISMATCH' },
  { key: 'awaiting', label: 'Awaiting doc', params: { state: 'MISSING_ATTACHMENT' },
    stateKey: 'MISSING_ATTACHMENT' },
  { key: 'wrongdoc', label: 'Wrong doc', params: { state: 'WRONG_DOCUMENT' },
    stateKey: 'WRONG_DOCUMENT' },
  { key: 'unreadable', label: 'Unreadable', params: { state: 'UNREADABLE' }, stateKey: 'UNREADABLE' },
  { key: 'clear', label: 'Clear', params: { state: 'OK' }, stateKey: 'OK' },
  { key: 'all', label: 'Everything', params: {} },
  // A browsable audit log: every case a human has already decided.
  { key: 'reviewed', label: 'Reviewed', params: { reviewed: true }, stateKey: 'reviewed' },
]

/** What a recorded decision is called in the queue. */
const DECISION_LABEL = {
  OK: 'Cleared',
  MISMATCH: 'Confirmed discrepancy',
  NEEDS_REVIEW: 'Sent for a second look',
}

export default function QueuePane({
  emails, selectedId, onSelect, filter, onFilter, loading, counts,
  category, onCategory, categoryCounts, availableTypes,
}) {
  const theme = useTheme()
  const dark = theme.palette.mode === 'dark'

  return (
    <Box sx={{
      height: '100%', display: 'flex', flexDirection: 'column',
      borderRight: '1px solid', borderColor: 'divider', bgcolor: 'background.default',
    }}>
      <Box role="tablist" aria-label="Queue filter" sx={{
        display: 'flex', flexWrap: 'wrap', gap: 1, p: 3,
        borderBottom: '1px solid', borderColor: 'divider',
      }}>
        {FILTERS.map((f) => {
          const on = filter === f.key
          return (
            <Box
              key={f.key}
              role="tab"
              tabIndex={0}
              aria-selected={on}
              onClick={() => onFilter(f.key)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onFilter(f.key) }
              }}
              sx={{
                px: 2.5, py: 1.25, fontSize: 12, fontWeight: on ? 600 : 500,
                cursor: 'pointer', borderRadius: '6px', border: '1px solid',
                transition: 'background-color 150ms, border-color 150ms',
                borderColor: on ? 'primary.main' : 'divider',
                bgcolor: on ? 'primary.main' : 'transparent',
                color: on ? 'primary.contrastText' : 'text.secondary',
                '&:hover': on ? {} : { bgcolor: 'action.hover' },
              }}
            >
              {f.label}
              {counts?.[f.stateKey] != null && (
                <Box component="span" sx={{ ml: 1, opacity: on ? 0.85 : 0.6, fontWeight: 600 }}>
                  {counts[f.stateKey]}
                </Box>
              )}
            </Box>
          )
        })}
      </Box>

      {/* Type is a second, independent axis: any state can be narrowed to
          any message type, so it is a selector rather than more tabs. */}
      <Box sx={{
        display: 'flex', alignItems: 'center', gap: 1.5, px: 3, py: 2,
        borderBottom: '1px solid', borderColor: 'divider',
      }}>
        <Typography sx={{ fontSize: 11, color: 'text.secondary', flexShrink: 0 }}>
          Type
        </Typography>
        <Box
          component="select"
          value={category}
          aria-label="Filter by message type"
          onChange={(e) => onCategory(e.target.value)}
          sx={{
            flex: 1, minWidth: 0, font: 'inherit', fontSize: 12,
            px: 1.5, py: 0.75, borderRadius: '6px', cursor: 'pointer',
            color: 'text.primary', bgcolor: 'background.paper',
            border: '1px solid', borderColor: category ? 'primary.main' : 'divider',
          }}
        >
          <option value="">
            All types ({Object.values(availableTypes ?? categoryCounts ?? {})
              .reduce((a, b) => a + b, 0)})
          </option>
          {CATEGORIES.map((c) => {
            // A type that cannot occur in the selected state is disabled
            // rather than silently yielding an empty list, which reads as
            // a bug. Spam is never "needs a human".
            const n = availableTypes ? (availableTypes[c] ?? 0) : categoryCounts?.[c]
            const impossible = availableTypes ? !availableTypes[c] : false
            return (
              <option key={c} value={c} disabled={impossible}>
                {CATEGORY_LABEL[c]}
                {n != null ? ` (${n})` : ''}
                {impossible ? ' — none in this view' : ''}
              </option>
            )
          })}
        </Box>
      </Box>

      <Box sx={{ flex: 1, overflowY: 'auto' }} role="listbox" aria-label="Messages">
        {loading && (
          <Typography variant="caption" sx={{ p: 4, display: 'block', color: 'text.secondary' }}>
            Loading&hellip;
          </Typography>
        )}

        {!loading && emails.length === 0 && (
          <Box sx={{ p: 5 }}>
            <Typography variant="body2" sx={{ mb: 1, fontWeight: 600 }}>
              Nothing waiting here.
            </Typography>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              Every message matching this filter has been dealt with.
            </Typography>
          </Box>
        )}

        {emails.map((e) => {
          const selected = e.email_id === selectedId
          const v = VERDICT[e.case_state] ?? VERDICT[e.status] ?? VERDICT.NEEDS_REVIEW
          const c = v[dark ? 'dark' : 'light']
          return (
            <Box
              key={e.email_id}
              role="option"
              aria-selected={selected}
              onClick={() => onSelect(e.email_id)}
              sx={{
                px: 3.5, py: 3, cursor: 'pointer',
                borderBottom: '1px solid', borderColor: 'divider',
                borderLeft: '3px solid',
                borderLeftColor: selected ? 'primary.main' : 'transparent',
                bgcolor: selected
                  ? (dark ? 'rgba(14,165,233,0.12)' : '#F0F9FF')
                  : 'transparent',
                transition: 'background-color 150ms',
                '&:hover': { bgcolor: selected ? undefined : 'action.hover' },
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1 }}>
                <Box sx={{ color: c.fg, display: 'grid', placeItems: 'center' }}>
                  <Icon name={v.icon} size={13} />
                </Box>
                <Typography sx={{ fontSize: 11.5, color: c.fg, fontWeight: 600 }}>
                  {v.label}
                </Typography>
                <Box sx={{ flex: 1 }} />
                {e.review && (
                  /* A decision carried across a re-run whose verdict then
                     changed is marked, not hidden: the reviewer answered a
                     different question and should look again. */
                  <Box title={e.review_is_stale
                    ? 'Decided before the machine changed its verdict — worth re-checking'
                    : undefined} sx={{
                    display: 'flex', alignItems: 'center', gap: 0.75,
                    px: 1, py: 0.25, borderRadius: '4px',
                    border: '1px solid',
                    borderColor: e.review_is_stale ? 'warning.main' : 'divider',
                    color: e.review_is_stale ? 'warning.main' : 'text.secondary',
                  }}>
                    <Icon name={e.review_is_stale ? 'alert' : 'user'} size={10} />
                    <Typography sx={{ fontSize: 10 }}>
                      {DECISION_LABEL[e.review.final_status] ?? e.review.final_status}
                      {e.review_is_stale ? ' · recheck' : ''}
                    </Typography>
                  </Box>
                )}
              </Box>

              <Typography variant="body2" sx={{
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                fontWeight: selected ? 600 : 450, mb: 1,
              }}>
                {e.subject || '(no subject)'}
              </Typography>

              <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                <Typography sx={{
                  fontSize: 11, fontFamily: 'ui-monospace, monospace', color: 'text.secondary',
                }}>
                  {e.email_id.replace('email_', '#')}
                </Typography>
                <TypeBadge category={e.category} compact />
                <Box sx={{ flex: 1 }} />
                {/* Labelled: an unlabelled coloured bar reads as progress. */}
                <Typography sx={{ fontSize: 10.5, color: 'text.secondary' }}>
                  confidence
                </Typography>
                <Confidence value={e.confidence} width={34} showValue={false} />
              </Box>
            </Box>
          )
        })}
      </Box>
    </Box>
  )
}
