import { getToken } from './auth.js'

const BASE = import.meta.env.VITE_API_BASE ?? ''

async function req(path, options) {
  const token = getToken()
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      // Attached only once signed in; the server ignores it when auth is off.
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options?.headers ?? {}),
    },
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} on ${path}`)
  return res.status === 204 ? null : res.json()
}

export const api = {
  config: () => req('/api/config'),
  listRuns: () => req('/api/runs'),
  startRun: () => req('/api/runs', { method: 'POST' }),
  // Clears every review decision, then starts a run. Shared demo only.
  resetDemo: () => req('/api/demo/reset', { method: 'POST' }),
  getRun: (id) => req(`/api/runs/${id}`),
  stats: (id) => req(`/api/runs/${id}/stats`),
  emails: (id, params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== '' && v != null),
    )
    return req(`/api/runs/${id}/emails?${q}`)
  },
  email: (id, eid) => req(`/api/runs/${id}/emails/${eid}`),
  draftReply: (id, eid) => req(`/api/runs/${id}/emails/${eid}/draft-reply`),
  retry: (id, eid) => req(`/api/runs/${id}/emails/${eid}/retry`, { method: 'POST' }),
  review: (id, eid, body) =>
    req(`/api/runs/${id}/emails/${eid}/review`, { method: 'POST', body: JSON.stringify(body) }),
  clearReview: (id, eid) =>
    req(`/api/runs/${id}/emails/${eid}/review`, { method: 'DELETE' }),
}
