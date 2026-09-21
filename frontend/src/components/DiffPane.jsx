import { useEffect, useRef, useState } from 'react'
import { Box, Collapse, Divider, Tab, Tabs, Tooltip, Typography, useTheme } from '@mui/material'
import Attachments from './Attachments.jsx'
import AuditTrail from './AuditTrail.jsx'
import Confidence from './Confidence.jsx'
import Icon from './Icon.jsx'
import NormalizationTrace from './NormalizationTrace.jsx'
import TypeBadge from './TypeBadge.jsx'
import { FIELD_LABEL, REASON_LABEL, SEVERITY, VERDICT, kindTag, severityOf, visuallyHidden } from '../theme.js'

/**
 * The comparison, as a heatmap.
 *
 * Rows are tinted by how serious the difference is, not merely by whether
 * there is one: a formatting artifact and a genuine contradiction are
 * different problems for the reviewer and should not look alike. Each row
 * still carries an icon and a word, so the tint is reinforcement rather
 * than the message.
 *
 * Kept as a three-column grid rather than cards: the job is scanning down
 * the SI column against the BL column, and a card per field breaks exactly
 * that alignment.
 */

const MARK = {
  MATCH: { icon: 'equals', title: 'Values agree' },
  MISMATCH: { icon: 'notEquals', title: 'Values differ' },
  UNCOMPARABLE: { icon: 'dash', title: 'Cannot compare' },
}

function Value({ text, strong, muted }) {
  if (!text) {
    return (
      <Typography sx={{ fontSize: 12.5, fontStyle: 'italic', color: 'text.secondary' }}>
        not stated
      </Typography>
    )
  }
  return (
    <Typography sx={{
      fontFamily: 'ui-monospace, "SF Mono", Menlo, monospace',
      fontSize: 12.5, lineHeight: 1.5, wordBreak: 'break-word',
      fontVariantNumeric: 'tabular-nums',
      fontWeight: strong ? 600 : 400,
      color: muted ? 'text.secondary' : 'text.primary',
    }}>
      {text}
    </Typography>
  )
}

function FieldRow({ fv, open, onToggle, isFocused }) {
  const theme = useTheme()
  const ref = useRef(null)

  // Bring the keyboard cursor into view. Without this, N moves focus to a
  // row that may be well below the fold and nothing appears to happen.
  useEffect(() => {
    if (!isFocused || !ref.current) return
    ref.current.scrollIntoView({
      block: 'nearest',
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
        ? 'auto' : 'smooth',
    })
  }, [isFocused])

  const dark = theme.palette.mode === 'dark'
  const sev = severityOf(fv)
  const mark = MARK[fv.verdict] ?? MARK.UNCOMPARABLE
  const tag = kindTag(fv)
  const differs = fv.verdict !== 'MATCH'
  const tint = dark ? sev.tintDark : sev.tintLight
  const bar = dark ? sev.barDark : sev.barLight

  return (
    <Box ref={ref} sx={{
      borderBottom: '1px solid', borderColor: 'divider',
      bgcolor: isFocused
        ? (dark ? 'rgba(14,165,233,0.10)' : '#F0F9FF')
        : tint,
      transition: 'background-color 140ms',
      scrollMarginTop: 8,
      // One left-edge indicator, not two: primary when the keyboard cursor
      // is here, severity otherwise.
      //
      // Drawn as a pseudo-element rather than an inset box-shadow, which
      // paints behind child backgrounds -- the row's own hover fill was
      // covering it, so the rail flickered away under the pointer.
      //
      // Its presence is driven by `kind`, the same field the tag reads.
      // Keying it off `verdict`/`artifact` instead meant a MATCH tagged
      // "Formatting" showed a tag with no rail.
      position: 'relative',
      '&::before': {
        content: '""',
        position: 'absolute',
        left: 0, top: 0, bottom: 0, width: '3px',
        zIndex: 1, pointerEvents: 'none',
        bgcolor: isFocused
          ? theme.palette.primary.main
          : (tag ? bar : 'transparent'),
      },
    }}>
      <Box
        role="button"
        tabIndex={0}
        aria-expanded={open}
        aria-label={`${FIELD_LABEL[fv.field] ?? fv.field}: ${tag ?? 'match'}`}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle() }
        }}
        sx={{
          display: 'grid',
          gridTemplateColumns: '150px minmax(0,1fr) 28px minmax(0,1fr) 104px 20px',
          gap: 3, alignItems: 'center', pl: 4, pr: 4, py: 3, cursor: 'pointer',
          transition: 'background-color 140ms',
          '&:hover': { bgcolor: 'action.hover' },
        }}
      >
        <Box>
          <Typography sx={{ fontSize: 13, fontWeight: differs ? 600 : 500 }}>
            {FIELD_LABEL[fv.field] ?? fv.field}
          </Typography>
          {tag && (
            // Why it differs, not just that it does.
            <Box sx={{
              display: 'inline-block', mt: 0.5, px: 1, py: 0.25, borderRadius: '4px',
              fontSize: 10, fontWeight: 650, letterSpacing: '0.01em',
              color: bar, border: '1px solid', borderColor: bar,
            }}>
              {tag}
            </Box>
          )}
        </Box>

        <Value text={fv.si?.raw} strong={differs} muted={!differs} />

        <Box sx={{ color: differs ? bar : 'text.secondary', display: 'grid', placeItems: 'center' }}>
          <Icon name={mark.icon} size={15} />
          <Box component="span" sx={visuallyHidden}>{mark.title}</Box>
        </Box>

        <Value text={fv.bl?.raw} strong={differs} muted={!differs} />

        <Confidence value={fv.verdict === 'UNCOMPARABLE' ? null : fv.confidence} />

        <Box sx={{
          color: 'text.secondary', display: 'grid', placeItems: 'center',
          transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 160ms',
        }}>
          <Icon name="chevron" size={14} />
        </Box>
      </Box>

      {differs && !open && fv.rationale?.[0] && (
        <Typography variant="caption" sx={{
          display: 'block', pl: '170px', pr: 4, pb: 2.5, mt: -1.5, color: 'text.secondary',
        }}>
          {fv.rationale[0]}
        </Typography>
      )}

      <Collapse in={open} unmountOnExit>
        <Box sx={{
          pl: '170px', pr: 4, pb: 3,
          borderTop: '1px dashed', borderColor: 'divider',
        }}>
          {fv.rationale?.length > 0 && (
            <Box component="ul" sx={{ m: 0, mt: 2.5, pl: 4 }}>
              {fv.rationale.map((r, i) => (
                <Typography component="li" key={i} variant="caption"
                            sx={{ display: 'list-item', color: 'text.secondary' }}>
                  {r}
                </Typography>
              ))}
            </Box>
          )}
          <NormalizationTrace trace={fv.si} side="Shipping Instruction" />
          <Divider />
          <NormalizationTrace trace={fv.bl} side="Draft Bill of Lading" />
        </Box>
      </Collapse>
    </Box>
  )
}

function Banner({ email }) {
  const theme = useTheme()
  const dark = theme.palette.mode === 'dark'
  const v = VERDICT[email.case_state] ?? VERDICT[email.status] ?? VERDICT.NEEDS_REVIEW
  const c = v[dark ? 'dark' : 'light']

  return (
    <Box sx={{
      display: 'flex', alignItems: 'center', gap: 3, mx: 4, my: 3,
      px: 3.5, py: 2.5, borderRadius: 2,
      bgcolor: c.bg, border: '1px solid', borderColor: c.border, color: c.fg,
    }}>
      <Icon name={v.icon} size={18} />
      <Box sx={{ minWidth: 0 }}>
        <Typography sx={{ fontSize: 13.5, fontWeight: 650, color: 'inherit' }}>
          {v.label}
        </Typography>
        <Typography sx={{ fontSize: 12, color: 'inherit', opacity: 0.9 }}>
          {email.review_reason
            ? REASON_LABEL[email.review_reason] ?? email.review_reason.replace(/_/g, ' ')
            : email.defect_fields?.length
              ? `${email.defect_fields.map((f) => FIELD_LABEL[f] ?? f).join(', ')} ${email.defect_fields.length > 1 ? 'differ' : 'differs'} between the documents`
              : v.verb}
        </Typography>
      </Box>
      <Box sx={{ flex: 1 }} />
      <Tooltip arrow title="How sure the machine is of its own reading, not of the shipment">
        <Box sx={{ textAlign: 'right' }}>
          <Confidence value={email.confidence} width={78} />
        </Box>
      </Tooltip>
    </Box>
  )
}

export default function DiffPane({
  email, openFields, onToggleField, focusedField, runId,
}) {
  const [tab, setTab] = useState(0)

  useEffect(() => { setTab(0) }, [email?.email_id])

  if (!email) {
    return (
      <Box sx={{ p: 8 }}>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          Select a message to begin.
        </Typography>
      </Box>
    )
  }

  const verdicts = email.field_verdicts ?? []

  return (
    <Box sx={{ height: '100%', overflowY: 'auto', bgcolor: 'background.paper' }}>
      <Box sx={{ px: 4, pt: 4, pb: 2 }}>
        <Typography variant="h1" sx={{ mb: 1 }}>
          {email.subject || '(no subject)'}
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
          <TypeBadge category={email.category} />
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            {email.sender} &middot; {email.email_id}
          </Typography>
        </Box>
      </Box>

      <Banner email={email} />

      <Tabs
        value={tab}
        onChange={(_e, v) => setTab(v)}
        sx={{ px: 4, minHeight: 40, borderBottom: '1px solid', borderColor: 'divider' }}
        slotProps={{ indicator: { sx: { height: 2 } } }}
      >
        <Tab label="Comparison" sx={{ minHeight: 40, fontSize: 13 }} />
        <Tab label="Email" sx={{ minHeight: 40, fontSize: 13 }} />
        <Tab label={`History${email.reviews?.length ? ` (${email.reviews.length})` : ''}`}
             sx={{ minHeight: 40, fontSize: 13 }} />
      </Tabs>

      {tab === 0 && (verdicts.length > 0 ? (
        <>
          <Box sx={{
            display: 'grid',
            gridTemplateColumns: '150px minmax(0,1fr) 28px minmax(0,1fr) 104px 20px',
            gap: 3, pl: 4, pr: 4, py: 2, borderBottom: '1px solid',
            borderColor: 'divider',
          }}>
            <Box />
            <Typography variant="caption" sx={{ fontWeight: 600 }}>Shipping Instruction</Typography>
            <Box />
            <Typography variant="caption" sx={{ fontWeight: 600 }}>Draft Bill of Lading</Typography>
            <Typography variant="caption" sx={{ fontWeight: 600 }}>Confidence</Typography>
            <Box />
          </Box>
          {verdicts.map((fv) => (
            <FieldRow
              key={fv.field}
              fv={fv}
              open={openFields.has(fv.field)}
              isFocused={focusedField === fv.field}
              onToggle={() => onToggleField(fv.field)}
            />
          ))}
        </>
      ) : (
        <Box sx={{ px: 4, py: 6 }}>
          <Typography variant="body2" sx={{ mb: 2 }}>
            No field comparison for this message.
          </Typography>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            {email.route_reason}
          </Typography>
          {email.notes?.map((n, i) => (
            <Typography key={i} variant="caption"
                        sx={{ display: 'block', mt: 1, color: 'text.secondary' }}>{n}</Typography>
          ))}
        </Box>
      ))}

      {tab === 1 && (
        <Box sx={{ px: 4, py: 4 }}>
          <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', color: 'text.secondary' }}>
            {email.body}
          </Typography>
          <Box sx={{ mt: 5 }}>
            <Attachments runId={runId} emailId={email.email_id}
                         attachments={email.attachments} />
          </Box>
        </Box>
      )}

      {tab === 2 && <AuditTrail email={email} />}
    </Box>
  )
}
