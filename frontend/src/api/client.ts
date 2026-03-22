// Default to 127.0.0.1 instead of localhost to avoid IPv6 (::1) resolution issues on Windows
// when the backend is bound to IPv4 only.
// Also normalize any env-provided localhost base to 127.0.0.1 (common source of “CORS”/ERR_FAILED in dev).
const RAW_API_BASE = import.meta.env.VITE_API_BASE ?? ''

function normalizeApiBase(raw: string): string {
  if (!raw) return ''
  try {
    const u = new URL(raw)
    if (u.hostname === 'localhost') u.hostname = '127.0.0.1'
    // Strip trailing slash so callers can safely do `${API_BASE}${path}`.
    return u.toString().replace(/\/$/, '')
  } catch {
    return raw.replace(/\/$/, '').replace(/^http:\/\/localhost(?=:\d+|$)/, 'http://127.0.0.1')
  }
}

// Default: same-origin requests (works when the frontend is reverse-proxied to the backend).
// If VITE_API_BASE is provided (e.g. Render static frontend + separate backend service), use it in ALL modes.
const API_BASE = RAW_API_BASE ? normalizeApiBase(RAW_API_BASE) : ''

// Track in-flight refresh to avoid thundering herd
let _refreshPromise: Promise<boolean> | null = null
let _redirectingToLogin = false

function handleUnauthorized(): void {
  setAccessTokenInternal(null)
  if (typeof window === 'undefined') return

  // Best-effort: clear HttpOnly auth cookies on the server side too.
  // This prevents stale cookie loops after tenant/session changes.
  try {
    fetch(`${API_BASE}/api/auth/logout`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      keepalive: true,
    }).catch(() => {
      // ignore
    })
  } catch {
    // ignore
  }

  if (_redirectingToLogin) return
  if (window.location.pathname === '/login') return
  _redirectingToLogin = true
  window.location.assign('/login')
}

async function _tryRefresh(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/api/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
    })
    if (!res.ok) return false
    const data = (await res.json()) as any
    if (data?.access_token) {
      setAccessTokenInternal(data.access_token)
    }
    return true
  } catch {
    return false
  }
}

function setAccessTokenInternal(token: string | null): void {
  try {
    if (typeof window === 'undefined') return
    if (!token) {
      window.localStorage.removeItem('access_token')
      return
    }
    window.localStorage.setItem('access_token', token)
  } catch {
    // ignore
  }
}

function getAccessToken(): string | null {
  try {
    if (typeof window === 'undefined') return null
    return window.localStorage.getItem('access_token')
  } catch {
    return null
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  // If we've already determined auth is invalid and started redirecting,
  // avoid firing more protected API requests that will just 401 again.
  if (_redirectingToLogin && !path.includes('/auth/login')) {
    throw new Error('Session expired. Redirecting to login.')
  }

  const token = getAccessToken()

  let res = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  })

  // Automatic token refresh on 401
  if (res.status === 401 && !!token && !path.includes('/auth/refresh') && !path.includes('/auth/login')) {
    if (!_refreshPromise) {
      _refreshPromise = _tryRefresh().finally(() => { _refreshPromise = null })
    }
    const refreshed = await _refreshPromise
    if (refreshed) {
      // Retry the original request with the new token
      const newToken = getAccessToken()
      const retryRes = await fetch(`${API_BASE}${path}`, {
        ...init,
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...(newToken ? { Authorization: `Bearer ${newToken}` } : {}),
          ...(init?.headers ?? {}),
        },
      })
      if (retryRes.ok) {
        return (await retryRes.json()) as T
      }
      // Continue error handling with retry response (not the original 401).
      res = retryRes
      // If retry also fails with 401, force re-auth.
      if (retryRes.status === 401) {
        handleUnauthorized()
        throw new Error('Session expired. Please log in again.')
      }
      // Fall through to normal error handling with retry response.
    } else {
      handleUnauthorized()
      throw new Error('Session expired. Please log in again.')
    }
  }

  if (!res.ok) {
    if (res.status === 401 && !path.includes('/auth/login')) {
      handleUnauthorized()
      throw new Error('Session expired. Please log in again.')
    }

    const contentType = res.headers.get('content-type') ?? ''
    const raw = await res.text()

    // DB outage handling (503): show a stable, user-friendly message.
    if (res.status === 503) {
      // Try to detect the explicit code; otherwise still show the friendly message.
      if (contentType.includes('application/json')) {
        try {
          const data = JSON.parse(raw) as any
          const topCode = typeof data?.code === 'string' ? data.code : null
          const detailCode = typeof data?.detail?.code === 'string' ? data.detail.code : null
          if (topCode === 'DATABASE_UNAVAILABLE' || detailCode === 'DATABASE_UNAVAILABLE') {
            throw new Error('Scheduling system temporarily unavailable. Please retry.')
          }
        } catch {
          // fall through
        }
      }
      throw new Error('Scheduling system temporarily unavailable. Please retry.')
    }

    // FastAPI typically returns JSON errors like: { "detail": ... }
    if (contentType.includes('application/json')) {
      let data: any = null
      try {
        data = JSON.parse(raw)
      } catch {
        data = null
      }

      if (data) {
        const detail = data?.detail

        // Solver/API errors often return structured details with a run_id for debugging.
        // Example: { detail: { error: 'SOLVER_DB_INTEGRITY_ERROR', message: '...', run_id: '...' } }
        if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
          const runId = typeof (detail as any).run_id === 'string' ? (detail as any).run_id : null
          const err = typeof (detail as any).error === 'string' ? (detail as any).error : null
          const typ = typeof (detail as any).type === 'string' ? (detail as any).type : null
          const msg = typeof (detail as any).message === 'string' ? (detail as any).message : null

          if (runId || err || msg || typ) {
            const head = [err, typ].filter(Boolean).join(': ')
            const body = msg && msg.trim() ? msg.trim() : null
            const base = [head, body].filter(Boolean).join(' - ')
            const finalMsg = `${base || 'Request failed'}${runId ? ` (run_id: ${runId})` : ''}`
            throw new Error(finalMsg)
          }
        }

        // New service-level style: { code: string, message: string }
        if (typeof data?.code === 'string') {
          const msg = typeof data?.message === 'string' && data.message.trim() ? data.message : data.code
          throw new Error(msg)
        }

        if (typeof detail === 'string' && detail.trim()) {
          if (detail === 'TIME_SLOTS_IN_USE') {
            throw new Error(
              'Cannot replace time slots while existing timetables exist. Disable “Replace existing” or clear timetable runs/entries first.',
            )
          }
          if (detail.trim().toLowerCase() === 'internal server error') {
            throw new Error(`Internal Server Error (${res.status}). Check backend logs.`)
          }
          throw new Error(detail)
        }

        // Our validation style: { detail: { code: string, errors: string[] } }
        if (detail && typeof detail === 'object') {
          const code = typeof detail.code === 'string' ? detail.code : null
          const errors = Array.isArray(detail.errors) ? detail.errors.filter((x: any) => typeof x === 'string') : []
          const msg =
            code && errors.length
              ? `${code}: ${errors.join(', ')}`
              : code
                ? code
                : raw

          throw new Error(msg || `Request failed: ${res.status}`)
        }

        // Pydantic/FastAPI validation: { detail: [{ loc, msg, type }, ...] }
        if (Array.isArray(detail) && detail.length) {
          const msg = detail
            .map((d: any) => (typeof d?.msg === 'string' ? d.msg : null))
            .filter(Boolean)
            .join(', ')
          throw new Error(msg || `Request failed: ${res.status}`)
        }
      }
    }

    if (raw && raw.trim().toLowerCase() === 'internal server error') {
      throw new Error(`Internal Server Error (${res.status}). Check backend logs.`)
    }
    throw new Error(raw || `Request failed: ${res.status}`)
  }
  return (await res.json()) as T
}

export async function logout(): Promise<void> {
  try {
    await apiFetch('/api/auth/logout', { method: 'POST' })
  } catch {
    // Best-effort logout; cookie may already be cleared.
  }
}
