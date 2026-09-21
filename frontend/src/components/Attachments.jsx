import { useEffect, useState } from 'react'
import { Box, Button, Typography, useTheme } from '@mui/material'
import Icon from './Icon.jsx'

/**
 * The email's attachments, openable in place.
 *
 * A reviewer disagreeing with an extracted value needs the document, not a
 * description of it. Text is shown inline because that is the fastest thing
 * to scan; PDFs render in a frame; spreadsheets and Word files have no
 * in-browser viewer worth building, so they download.
 *
 * Files are addressed by position in the email's own attachment list, so
 * the client never names a path and no request can reach outside the
 * dataset.
 */

const INLINE_TEXT = new Set(['txt', 'csv'])
const INLINE_FRAME = new Set(['pdf', 'png', 'jpg', 'jpeg'])

function extensionOf(path) {
  return path.split('.').pop().toLowerCase()
}

function TextPreview({ url }) {
  const [state, setState] = useState({ loading: true })

  useEffect(() => {
    let cancelled = false
    setState({ loading: true })
    fetch(url)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`${r.status}`))))
      .then((text) => !cancelled && setState({ text }))
      .catch((e) => !cancelled && setState({ error: e.message }))
    return () => { cancelled = true }
  }, [url])

  if (state.loading) {
    return <Typography variant="caption" sx={{ color: 'text.secondary' }}>Loading&hellip;</Typography>
  }
  if (state.error) {
    return (
      <Typography variant="caption" sx={{ color: 'error.main' }}>
        Could not load this attachment ({state.error}).
      </Typography>
    )
  }
  return (
    <Box component="pre" sx={{
      m: 0, p: 3, borderRadius: '6px', overflowX: 'auto', maxHeight: 420,
      fontFamily: 'ui-monospace, monospace', fontSize: 11.5, lineHeight: 1.6,
      bgcolor: 'action.hover', color: 'text.primary',
      border: '1px solid', borderColor: 'divider',
    }}>
      {state.text}
    </Box>
  )
}

export default function Attachments({ runId, emailId, attachments }) {
  const theme = useTheme()
  const dark = theme.palette.mode === 'dark'
  const [open, setOpen] = useState(null)

  useEffect(() => { setOpen(null) }, [emailId])

  if (!attachments?.length) {
    return (
      <Typography variant="caption" sx={{ color: 'text.secondary' }}>
        No attachments on this message.
      </Typography>
    )
  }

  return (
    <Box>
      <Typography variant="h3" sx={{ mb: 2 }}>
        Attachments ({attachments.length})
      </Typography>

      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
        {attachments.map((path, i) => {
          const name = path.split('/').pop()
          const ext = extensionOf(name)
          const url = `/api/runs/${runId}/emails/${emailId}/attachments/${i}`
          const viewable = INLINE_TEXT.has(ext) || INLINE_FRAME.has(ext)
          const isOpen = open === i

          return (
            <Box key={path} sx={{
              border: '1px solid', borderColor: isOpen ? 'primary.main' : 'divider',
              borderRadius: '7px', overflow: 'hidden',
            }}>
              <Box sx={{
                display: 'flex', alignItems: 'center', gap: 2, px: 3, py: 2,
                bgcolor: isOpen ? 'action.hover' : 'transparent',
              }}>
                <Box sx={{ color: 'text.secondary' }}>
                  <Icon name="fileWarning" size={15} />
                </Box>
                <Typography sx={{
                  flex: 1, minWidth: 0, fontSize: 12.5,
                  fontFamily: 'ui-monospace, monospace',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {name}
                </Typography>
                <Typography sx={{
                  fontSize: 10, px: 1, py: 0.25, borderRadius: '4px',
                  color: 'text.secondary', border: '1px solid', borderColor: 'divider',
                  textTransform: 'uppercase',
                }}>
                  {ext}
                </Typography>

                {viewable && (
                  <Button size="small" onClick={() => setOpen(isOpen ? null : i)}>
                    {isOpen ? 'Hide' : 'View'}
                  </Button>
                )}
                <Button size="small" href={url} target="_blank" rel="noopener">
                  {viewable ? 'New tab' : 'Download'}
                </Button>
              </Box>

              {isOpen && (
                <Box sx={{ p: INLINE_TEXT.has(ext) ? 2 : 0, borderTop: '1px solid',
                           borderColor: 'divider' }}>
                  {INLINE_TEXT.has(ext)
                    ? <TextPreview url={url} />
                    : (
                      <Box
                        component="iframe"
                        src={url}
                        title={name}
                        sx={{
                          width: '100%', height: 520, border: 0, display: 'block',
                          // PDF viewers render on white; keep that from
                          // flashing against a dark page while it loads.
                          bgcolor: dark ? '#1E293B' : '#FFFFFF',
                        }}
                      />
                    )}
                </Box>
              )}
            </Box>
          )
        })}
      </Box>
    </Box>
  )
}
