import { createTheme } from '@mui/material/styles'

/**
 * Palette and tokens.
 *
 * Colour carries three jobs here and nothing else: what the machine found
 * (clear / discrepancy / unreadable), how sure it is, and which control is
 * the primary action. Anything decorative would compete with those, because
 * an operator scanning this screen is looking for exactly one thing.
 *
 * Every state pairs a tint with an icon and a word. Colour is never the only
 * carrier of meaning — a reviewer with a colour vision deficiency, or a
 * greyscale print of the report, must read the same verdict.
 */

const ocean = {
  50: '#F0F9FF', 100: '#E0F2FE', 200: '#BAE6FD', 300: '#7DD3FC',
  400: '#38BDF8', 500: '#0EA5E9', 600: '#0284C7', 700: '#0369A1',
  800: '#075985', 900: '#0C4A6E',
}

const slate = {
  50: '#F8FAFC', 100: '#F1F5F9', 200: '#E2E8F0', 300: '#CBD5E1',
  400: '#94A3B8', 500: '#64748B', 600: '#475569', 700: '#334155',
  800: '#1E293B', 900: '#0F172A', 950: '#020617',
}

/** Verdict styling. Tint + icon + word, always all three. */
export const VERDICT = {
  OK: {
    label: 'Clear',
    verb: 'No discrepancy found',
    icon: 'check',
    light: { fg: '#047857', bg: '#ECFDF5', border: '#A7F3D0' },
    dark: { fg: '#6EE7B7', bg: 'rgba(16,185,129,0.12)', border: 'rgba(16,185,129,0.35)' },
  },
  MISMATCH: {
    label: 'Discrepancy',
    verb: 'The documents disagree',
    icon: 'alert',
    light: { fg: '#BE123C', bg: '#FFF1F2', border: '#FECDD3' },
    dark: { fg: '#FDA4AF', bg: 'rgba(244,63,94,0.12)', border: 'rgba(244,63,94,0.35)' },
  },
  NEEDS_REVIEW: {
    label: 'Needs a human',
    verb: 'The machine could not decide',
    icon: 'help',
    light: { fg: '#B45309', bg: '#FFFBEB', border: '#FDE68A' },
    dark: { fg: '#FCD34D', bg: 'rgba(245,158,11,0.12)', border: 'rgba(245,158,11,0.35)' },
  },
  // A document that would not parse is a different job from a judgement
  // call: it needs a retry or a fresh file, not someone to read it. Slate
  // rather than amber, so the queue does not imply it needs human thought.
  // Nothing arrived. The action is to ask for it, not to retry a parse.
  MISSING_ATTACHMENT: {
    label: 'Awaiting document',
    verb: 'The document was never sent',
    icon: 'mail',
    light: { fg: '#7C3AED', bg: '#F5F3FF', border: '#DDD6FE' },
    dark: { fg: '#C4B5FD', bg: 'rgba(124,58,237,0.14)', border: 'rgba(167,139,250,0.35)' },
  },
  // The wrong file arrived. Answered by a reply naming what we got and
  // what we still need, not by a retry.
  WRONG_DOCUMENT: {
    label: 'Wrong document',
    verb: 'A different document was attached',
    icon: 'fileWarning',
    light: { fg: '#C2410C', bg: '#FFF7ED', border: '#FED7AA' },
    dark: { fg: '#FDBA74', bg: 'rgba(249,115,22,0.12)', border: 'rgba(249,115,22,0.32)' },
  },
  UNREADABLE: {
    label: 'Unreadable',
    verb: 'The attachment could not be parsed',
    icon: 'fileWarning',
    light: { fg: '#475569', bg: '#F1F5F9', border: '#CBD5E1' },
    dark: { fg: '#CBD5E1', bg: 'rgba(148,163,184,0.12)', border: 'rgba(148,163,184,0.30)' },
  },
}

/**
 * Field-level severity — the heatmap.
 *
 * Three bands, not a continuous gradient: a reviewer needs to know which
 * bucket a row is in, and a smooth ramp makes neighbouring rows look
 * meaningfully different when they are not.
 */
export const SEVERITY = {
  match: {
    label: 'Match',
    tintLight: 'transparent',
    tintDark: 'transparent',
    barLight: '#10B981', barDark: '#34D399',
  },
  formatting: {
    label: 'Formatting only',
    tintLight: '#FFFBEB',
    tintDark: 'rgba(245,158,11,0.10)',
    barLight: '#F59E0B', barDark: '#FBBF24',
  },
  contradiction: {
    label: 'Contradiction',
    tintLight: '#FFF1F2',
    tintDark: 'rgba(244,63,94,0.12)',
    barLight: '#F43F5E', barDark: '#FB7185',
  },
  unreadable: {
    label: 'Not stated',
    tintLight: '#F8FAFC',
    tintDark: 'rgba(148,163,184,0.10)',
    barLight: '#94A3B8', barDark: '#64748B',
  },
}

/**
 * Why two values differ, as classified by the comparison step.
 *
 * The reviewer's next action depends on this, not on the fact of a
 * difference: a genuine mismatch goes back to the shipper, a misread goes
 * back to the scanner, and a mapping problem means we may not be comparing
 * the right fields at all. Server-supplied; the UI only styles it.
 */
export const KIND_STYLE = {
  match: null,
  formatting: { band: 'formatting', short: 'Formatting' },
  unit: { band: 'contradiction', short: 'Unit mismatch' },
  ocr_misread: { band: 'formatting', short: 'Likely OCR misread' },
  label_mapping: { band: 'formatting', short: 'Label mapping' },
  genuine: { band: 'contradiction', short: 'Genuine mismatch' },
  not_stated: { band: 'unreadable', short: 'Not stated' },
}

/**
 * Which band a field verdict belongs in.
 *
 * An artifact is a reading problem, not a contradiction: values that differ
 * only by OCR spacing, a separator, or a unit are formatting. Only a plain
 * disagreement between two legible values is a contradiction.
 */
export function severityOf(fv) {
  if (!fv) return SEVERITY.match
  // The server now says why values differ, so the band follows that rather
  // than being re-derived from artifact flags in the client.
  const style = KIND_STYLE[fv.kind]
  if (style) return SEVERITY[style.band]
  if (fv.verdict === 'UNCOMPARABLE') return SEVERITY.unreadable
  if (fv.artifact) return SEVERITY.formatting
  if (fv.verdict === 'MISMATCH') {
    return fv.uncertain ? SEVERITY.formatting : SEVERITY.contradiction
  }
  return SEVERITY.match
}

/** Short tag for a field row, or null when the values simply agree. */
export function kindTag(fv) {
  if (!fv) return null
  const style = KIND_STYLE[fv.kind]
  if (style) return style.short
  return fv.verdict === 'MATCH' ? null : (fv.kind_label ?? null)
}

export const CATEGORY_LABEL = {
  BL_COMPARISON: 'Document check',
  SI_REQUEST: 'New SI request',
  INVOICE_QUERY: 'Invoice query',
  GENERAL: 'General',
  SPAM: 'Spam',
}

/**
 * Message type styling.
 *
 * Hue separates the type from the verdict: verdicts own green / amber / red
 * / slate, so types take the cooler end of the wheel and never compete for
 * the same meaning. Only the document check is tinted at full strength --
 * it is the one type that leads to work.
 */
export const CATEGORY_STYLE = {
  BL_COMPARISON: { short: 'Doc check', icon: 'fileWarning',
                   light: '#0369A1', dark: '#7DD3FC' },
  SI_REQUEST:    { short: 'New SI',    icon: 'mail',
                   light: '#7C3AED', dark: '#C4B5FD' },
  INVOICE_QUERY: { short: 'Invoice',   icon: 'clock',
                   light: '#0F766E', dark: '#5EEAD4' },
  GENERAL:       { short: 'General',   icon: 'mail',
                   light: '#64748B', dark: '#94A3B8' },
  SPAM:          { short: 'Spam',      icon: 'alert',
                   light: '#9F1239', dark: '#FDA4AF' },
}

/** Category filter options, in the order the queue offers them. */
export const CATEGORIES = [
  'BL_COMPARISON', 'SI_REQUEST', 'INVOICE_QUERY', 'GENERAL', 'SPAM',
]

export const FIELD_LABEL = {
  shipper: 'Shipper',
  consignee: 'Consignee',
  notify_party: 'Notify party',
  port_of_loading: 'Port of loading',
  port_of_discharge: 'Port of discharge',
  container_count: 'Containers',
  gross_weight_kg: 'Gross weight',
}

export const REASON_LABEL = {
  wrong_doc_type: 'Wrong document attached',
  missing_attachment: 'Attachment missing',
  unreadable: 'Could not be read reliably',
  missing_value: 'A required value is blank',
}

/**
 * The wordmark's own face.
 *
 * Deliberately scoped to the title alone: a serif here reads as a masthead
 * against the sans interface, which is the point. Applying it anywhere else
 * would undo the type hierarchy the rest of the screen depends on.
 */
export const WORDMARK = {
  fontFamily: '"Times New Roman", Times, serif',
  fontWeight: 700,
  fontSize: 19,
  lineHeight: 1.05,
  letterSpacing: '-0.005em',
}

/** Screen-reader-only text. Explicit px: MUI reads a bare 1 as 100%. */
export const visuallyHidden = {
  position: 'absolute', width: '1px', height: '1px', padding: 0,
  margin: '-1px', overflow: 'hidden', clip: 'rect(0 0 0 0)',
  whiteSpace: 'nowrap', border: 0,
}

export function buildTheme(mode) {
  const dark = mode === 'dark'
  return createTheme({
    palette: {
      mode,
      primary: { main: dark ? ocean[400] : ocean[600], contrastText: dark ? slate[950] : '#FFFFFF' },
      error: { main: dark ? '#FB7185' : '#E11D48' },
      warning: { main: dark ? '#FBBF24' : '#B45309' },
      success: { main: dark ? '#34D399' : '#059669' },
      background: {
        default: dark ? slate[900] : slate[50],
        paper: dark ? slate[800] : '#FFFFFF',
      },
      text: {
        primary: dark ? slate[100] : slate[900],
        secondary: dark ? slate[400] : slate[500],
      },
      divider: dark ? 'rgba(148,163,184,0.18)' : slate[200],
    },
    shape: { borderRadius: 8 },
    spacing: 4,
    typography: {
      fontFamily: '"Inter", system-ui, -apple-system, sans-serif',
      fontSize: 14,
      h1: { fontSize: 19, fontWeight: 650, letterSpacing: '-0.015em' },
      h2: { fontSize: 15, fontWeight: 600, letterSpacing: '-0.01em' },
      h3: { fontSize: 13, fontWeight: 600 },
      body2: { fontSize: 13.5, lineHeight: 1.55 },
      caption: { fontSize: 12, lineHeight: 1.5 },
      button: { textTransform: 'none', fontWeight: 550 },
    },
    components: {
      MuiPaper: {
        defaultProps: { elevation: 0 },
        styleOverrides: { root: { backgroundImage: 'none' } },
      },
      MuiButton: {
        defaultProps: { disableElevation: true },
        styleOverrides: {
          root: { borderRadius: 7, paddingTop: 7, paddingBottom: 7, minHeight: 38 },
        },
      },
      MuiTooltip: {
        styleOverrides: {
          tooltip: {
            fontSize: 12, lineHeight: 1.5, maxWidth: 320, padding: '8px 10px',
            backgroundColor: dark ? slate[700] : slate[800],
          },
        },
      },
      MuiCssBaseline: {
        styleOverrides: {
          // Tells the browser to paint native scrollbars, form controls and
          // the canvas for this scheme. Without it the scrollbars stay light
          // against a dark page.
          ':root': { colorScheme: mode },
          '*::-webkit-scrollbar': { width: 11, height: 11 },
          '*::-webkit-scrollbar-track': { background: 'transparent' },
          '*::-webkit-scrollbar-thumb': {
            backgroundColor: dark ? 'rgba(148,163,184,0.28)' : 'rgba(100,116,139,0.30)',
            borderRadius: 6,
            border: `3px solid ${dark ? slate[900] : slate[50]}`,
          },
          '*::-webkit-scrollbar-thumb:hover': {
            backgroundColor: dark ? 'rgba(148,163,184,0.45)' : 'rgba(100,116,139,0.5)',
          },
          // Keyboard is the primary input here, so focus must be loud.
          ':focus-visible': {
            outline: `2px solid ${dark ? ocean[400] : ocean[600]}`,
            outlineOffset: 2,
          },
          '@media (prefers-reduced-motion: reduce)': {
            '*': {
              animationDuration: '0.01ms !important',
              transitionDuration: '0.01ms !important',
            },
          },
        },
      },
    },
  })
}

export { ocean, slate }
