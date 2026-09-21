import { Box, Typography, useTheme } from '@mui/material'
import Icon from './Icon.jsx'

/**
 * How one value travelled from page text to the value we compared.
 *
 * Only steps that changed something are drawn. A chain of six no-op
 * transformations teaches nothing and buries the one that mattered, which
 * is usually a unit conversion or a dropped locode.
 */

function Chip({ children, muted }) {
  return (
    <Box component="span" sx={{
      fontFamily: 'ui-monospace, monospace', fontSize: 11.5,
      px: 1.75, py: 0.75, borderRadius: '5px', whiteSpace: 'nowrap',
      border: '1px solid', borderColor: 'divider',
      bgcolor: muted ? 'action.hover' : 'background.paper',
      color: muted ? 'text.secondary' : 'text.primary',
    }}>{children}</Box>
  )
}

export default function NormalizationTrace({ trace, side }) {
  const theme = useTheme()
  const dark = theme.palette.mode === 'dark'
  if (!trace) return null
  const steps = (trace.steps || []).filter((s) => s.changed)

  return (
    <Box sx={{ py: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2 }}>
        <Typography variant="caption" sx={{ fontWeight: 600 }}>{side}</Typography>
        {trace.source === 'ocr' && (
          <Box sx={{
            display: 'flex', alignItems: 'center', gap: 0.75,
            fontSize: 10.5, px: 1.25, py: 0.4, borderRadius: '4px',
            bgcolor: dark ? 'rgba(245,158,11,0.14)' : '#FFFBEB',
            color: dark ? '#FCD34D' : '#B45309',
            border: '1px solid', borderColor: dark ? 'rgba(245,158,11,0.3)' : '#FDE68A',
          }}>
            <Icon name="scan" size={11} />
            read by OCR from a scan
          </Box>
        )}
      </Box>

      {/* What the page actually said, before anything was done to it. A
          reviewer who disagrees with a value should not have to reopen the
          attachment to check it. */}
      {trace.snippet && (
        <Box sx={{
          mb: 2, px: 2, py: 1.5, borderRadius: '6px',
          bgcolor: 'action.hover', borderLeft: '2px solid',
          borderColor: 'primary.main',
        }}>
          <Typography sx={{ fontSize: 10, color: 'text.secondary', mb: 0.5 }}>
            source &middot; {trace.locator}
          </Typography>
          <Typography sx={{
            fontFamily: 'ui-monospace, monospace', fontSize: 11.5,
            color: 'text.primary', wordBreak: 'break-word',
          }}>
            {trace.snippet}
          </Typography>
        </Box>
      )}

      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
        <Chip muted>{trace.raw || '(empty)'}</Chip>
        {steps.map((s, i) => (
          <Box key={i} sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Box sx={{ color: 'text.secondary', opacity: 0.5 }}>
              <Icon name="chevron" size={12} />
            </Box>
            <Box>
              <Chip>{s.after}</Chip>
              <Typography sx={{ fontSize: 10, color: 'text.secondary', mt: 0.5 }}>
                {s.name.replace(/_/g, ' ')}
              </Typography>
            </Box>
          </Box>
        ))}
        {steps.length === 0 && (
          <Typography variant="caption" sx={{ fontStyle: 'italic', color: 'text.secondary' }}>
            used exactly as written
          </Typography>
        )}
      </Box>
    </Box>
  )
}
