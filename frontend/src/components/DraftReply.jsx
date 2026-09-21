import { useEffect, useState } from 'react'
import { Box, Button, Typography } from '@mui/material'
import Icon from './Icon.jsx'
import { api } from '../api.js'

/**
 * A reply asking for the documents this case is missing.
 *
 * Drafted, never sent. Composing text is cheap and reversible; sending
 * mail on someone's behalf is neither, and the person who owns the mailbox
 * should be the one who presses send. The operator copies it out and marks
 * it sent, which records the action in the audit trail.
 */
export default function DraftReply({ runId, emailId, onMarkSent, sent, busy }) {
  const [draft, setDraft] = useState(null)
  const [error, setError] = useState(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    let cancelled = false
    setDraft(null)
    setError(null)
    setCopied(false)
    api.draftReply(runId, emailId)
      .then((d) => !cancelled && setDraft(d))
      .catch((e) => !cancelled && setError(e.message))
    return () => { cancelled = true }
  }, [runId, emailId])

  const copy = async () => {
    if (!draft) return
    const text = `Subject: ${draft.subject}\n\n${draft.body}`
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard is blocked outside a secure context; the textarea below
      // is still selectable, so this is a convenience, not the only path.
      setError('Clipboard unavailable — select the text and copy manually.')
    }
  }

  if (error && !draft) {
    return <Typography variant="caption" sx={{ color: 'error.main' }}>{error}</Typography>
  }
  if (!draft) {
    return <Typography variant="caption" sx={{ color: 'text.secondary' }}>Drafting…</Typography>
  }

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 1.5 }}>
        <Typography variant="h3">Reply to request it</Typography>
        {draft.reference && (
          <Typography sx={{
            fontSize: 10.5, fontFamily: 'ui-monospace, monospace',
            px: 1, py: 0.25, borderRadius: '4px', color: 'text.secondary',
            border: '1px solid', borderColor: 'divider',
          }}>
            {draft.reference}
          </Typography>
        )}
      </Box>

      <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary', mb: 1.5 }}>
        To {draft.to}
      </Typography>

      <Box
        component="textarea"
        readOnly
        value={`Subject: ${draft.subject}\n\n${draft.body}`}
        rows={11}
        sx={{
          width: '100%', resize: 'vertical', p: 2.5, borderRadius: '6px',
          font: 'inherit', fontSize: 11.5, lineHeight: 1.6,
          fontFamily: 'ui-monospace, monospace',
          color: 'text.primary', bgcolor: 'action.hover',
          border: '1px solid', borderColor: 'divider',
        }}
      />

      <Box sx={{ display: 'flex', gap: 1.5, mt: 2 }}>
        <Button
          size="small" variant="outlined" onClick={copy}
          startIcon={<Icon name={copied ? 'check' : 'mail'} size={14} />}
        >
          {copied ? 'Copied' : 'Copy'}
        </Button>
        <Button
          size="small" variant={sent ? 'outlined' : 'contained'}
          disabled={busy || sent} onClick={onMarkSent}
          startIcon={<Icon name="check" size={14} />}
        >
          {sent ? 'Marked as sent' : 'Mark as sent'}
        </Button>
      </Box>

      {error && (
        <Typography variant="caption" sx={{ display: 'block', mt: 1.5, color: 'warning.main' }}>
          {error}
        </Typography>
      )}
    </Box>
  )
}
