import { Box, Typography, useTheme } from '@mui/material'
import Icon from './Icon.jsx'
import { CATEGORY_LABEL, CATEGORY_STYLE } from '../theme.js'

/**
 * What kind of message this is.
 *
 * Types use cool hues so they never read as a verdict: green, amber and red
 * already mean clear, unsure and wrong. A badge that borrowed those would
 * make "Invoice query" look like a problem.
 */
export default function TypeBadge({ category, compact = false }) {
  const theme = useTheme()
  const dark = theme.palette.mode === 'dark'
  const style = CATEGORY_STYLE[category]
  const label = compact
    ? (style?.short ?? category)
    : (CATEGORY_LABEL[category] ?? category)

  if (!style) {
    return (
      <Typography variant="caption" sx={{ color: 'text.secondary' }}>{label}</Typography>
    )
  }

  const colour = dark ? style.dark : style.light

  return (
    <Box
      component="span"
      title={CATEGORY_LABEL[category] ?? category}
      sx={{
        display: 'inline-flex', alignItems: 'center', gap: 0.75, flexShrink: 0,
        px: 1.25, py: 0.35, borderRadius: '4px',
        fontSize: 10.5, fontWeight: 600, whiteSpace: 'nowrap',
        color: colour,
        border: '1px solid',
        borderColor: dark ? 'rgba(148,163,184,0.25)' : 'rgba(100,116,139,0.22)',
      }}
    >
      <Icon name={style.icon} size={11} />
      {label}
    </Box>
  )
}
