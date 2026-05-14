// ROADMAP Stage 10.2 — low-level HTTP client for the new SPA.
//
// Mirrors the contract of the legacy `apiFetch()` method on the root
// Vue instance in `index.html` (see lines ~1463-1481): inject the
// `Authorization: Bearer <token>` header from session storage when
// present, and on a 401 from an authenticated endpoint clear the
// stored token and notify the caller via an event the session store
// can listen for. Keeping the same backend contract is what allows
// the new SPA at /beta to talk to the same FastAPI app that the
// legacy / talks to without any backend changes.

/**
 * Custom error thrown when a request fails authentication. The session
 * store catches this to flip into "needs login" state — keeping the
 * error class explicit avoids the legacy pattern of throwing a plain
 * `new Error('Unauthorized')` whose only signal was the string body.
 */
export class UnauthorizedError extends Error {
  readonly response: Response

  constructor(response: Response) {
    super(`Unauthorized: ${response.status} ${response.statusText}`)
    this.name = 'UnauthorizedError'
    this.response = response
  }
}

/**
 * Browser-side storage key for the bearer token. Identical to the
 * legacy key so a user logged in on `/` and then visiting `/beta`
 * (or vice-versa) doesn't have to log in again. Once Stage 10.7
 * removes the legacy frontend this can stay — sessionStorage is
 * per-origin, so the namespace is unchanged.
 */
export const AUTH_TOKEN_STORAGE_KEY = 'authToken'

export interface ApiFetchOptions extends RequestInit {
  /**
   * Override the URL prefix. Defaults to the empty string so paths
   * like `/api/feed` work as-is when the SPA is served from the same
   * origin as the API (the only mode the app currently supports).
   */
  baseUrl?: string
  /**
   * Skip injecting the bearer token even if one is stored. Used for
   * the login endpoint itself and the auth_status probe so we don't
   * accidentally send a stale token while trying to determine auth
   * state.
   */
  skipAuth?: boolean
}

/**
 * Read the bearer token from sessionStorage. Wrapped in try/catch
 * because some browser sandboxes (private mode in old Safari, file://
 * pages, CSP-stricter contexts) throw on sessionStorage access.
 */
export function getStoredToken(): string {
  try {
    return window.sessionStorage.getItem(AUTH_TOKEN_STORAGE_KEY) ?? ''
  } catch {
    return ''
  }
}

export function setStoredToken(token: string): void {
  try {
    if (token) {
      window.sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token)
    } else {
      window.sessionStorage.removeItem(AUTH_TOKEN_STORAGE_KEY)
    }
  } catch {
    /* ignore — see getStoredToken */
  }
}

/**
 * Perform an HTTP request against the backend, injecting the stored
 * bearer token unless `skipAuth` is set.
 *
 * The function does NOT JSON-parse the response — callers decide
 * whether they want `.json()`, `.text()`, or the raw Response (e.g.
 * for streaming endpoints). This matches the legacy semantics and
 * keeps the contract minimal.
 *
 * On HTTP 401 it throws `UnauthorizedError` so the session store can
 * react (clear token, surface login modal). Other non-2xx statuses
 * are returned as-is to the caller — the legacy code does the same
 * thing and then inspects `res.ok` / `res.status` at the call site.
 */
export async function apiFetch(
  path: string,
  options: ApiFetchOptions = {},
): Promise<Response> {
  const { baseUrl = '', skipAuth = false, headers, ...rest } = options
  const url = `${baseUrl}${path}`

  const mergedHeaders = new Headers(headers)
  if (!skipAuth) {
    const token = getStoredToken()
    if (token && !mergedHeaders.has('Authorization')) {
      mergedHeaders.set('Authorization', `Bearer ${token}`)
    }
  }

  const response = await fetch(url, { ...rest, headers: mergedHeaders })
  if (response.status === 401 && !skipAuth) {
    setStoredToken('')
    throw new UnauthorizedError(response)
  }
  return response
}
