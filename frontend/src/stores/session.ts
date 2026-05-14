// ROADMAP Stage 10.2 — session/auth state store.
//
// Pinia replacement for the legacy data fields
// `authEnabled` / `authToken` / `showLogin` that lived on the root
// Vue instance in `index.html`. The store does **not** render any
// UI; it only owns the state and the few async operations that
// mutate it (init / login / logout). The login modal (PR 10.3) and
// any consumer view binds against the getters.

import { defineStore } from 'pinia'
import { apiFetch, getStoredToken, setStoredToken, UnauthorizedError } from '../api/client'

export type AuthState =
  | 'unknown' // haven't pinged /api/auth_status yet
  | 'disabled' // backend says auth is off; everyone can use the app
  | 'authenticated' // we have a valid bearer token in sessionStorage
  | 'unauthenticated' // auth is required but no/expired token

interface SessionStoreState {
  status: AuthState
  token: string
  loginError: string
}

interface AuthStatusResponse {
  auth_enabled: boolean
}

interface LoginResponse {
  token: string
  auth_enabled?: boolean
}

export const useSessionStore = defineStore('session', {
  state: (): SessionStoreState => ({
    status: 'unknown',
    token: getStoredToken(),
    loginError: '',
  }),

  getters: {
    /**
     * `true` when the user can call protected endpoints — either auth
     * is globally disabled on the backend, or we currently hold a
     * valid token. Components should bind against this rather than
     * checking `status` directly so the "auth disabled" deployment
     * mode keeps working transparently.
     */
    canCallApi: (state): boolean =>
      state.status === 'disabled' || state.status === 'authenticated',
    /**
     * `true` only when auth is on AND we don't have a token. The
     * login modal (PR 10.3) opens itself on this getter.
     */
    needsLogin: (state): boolean => state.status === 'unauthenticated',
  },

  actions: {
    /**
     * Ping `/api/auth_status` to decide whether the backend has auth
     * turned on, then resolve into one of `disabled` / `authenticated`
     * / `unauthenticated`. Safe to call multiple times.
     */
    async init(): Promise<void> {
      try {
        const res = await apiFetch('/api/auth_status', { skipAuth: true })
        if (!res.ok) {
          this.status = 'unauthenticated'
          return
        }
        const data: AuthStatusResponse = await res.json()
        if (!data.auth_enabled) {
          this.status = 'disabled'
          return
        }
        // Auth on; do we already have a token? We don't verify it
        // server-side here — the first protected request will get a
        // 401 and the apiFetch helper plus `handleUnauthorized` will
        // flip us back to `unauthenticated`. Avoiding an extra probe
        // keeps the boot path fast.
        this.status = this.token ? 'authenticated' : 'unauthenticated'
      } catch {
        // /api/auth_status itself is allow-listed in
        // `auth_middleware`, so a network error here means the
        // backend is unreachable, not auth-related. Surface as
        // "unauthenticated" so the user sees the login modal (and
        // the inevitable error message when they try to submit).
        this.status = 'unauthenticated'
      }
    },

    /**
     * Submit credentials to `/api/login`. On success the token is
     * stored in sessionStorage and the store flips to
     * `authenticated`. Login errors are surfaced via `loginError`
     * for the modal to render.
     */
    async login(username: string, password: string): Promise<boolean> {
      this.loginError = ''
      try {
        const res = await apiFetch('/api/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, password }),
          skipAuth: true,
        })
        if (!res.ok) {
          this.loginError =
            res.status === 401
              ? 'Неверный логин или пароль'
              : `Сервер ответил ${res.status}`
          return false
        }
        const data: LoginResponse = await res.json()
        this.token = data.token
        setStoredToken(data.token)
        this.status = 'authenticated'
        return true
      } catch (err) {
        this.loginError =
          err instanceof Error ? `Ошибка соединения: ${err.message}` : 'Ошибка соединения'
        return false
      }
    },

    /**
     * Tell the backend to invalidate the token (best-effort), then
     * clear local state regardless of the response. Always safe to
     * call.
     */
    async logout(): Promise<void> {
      const previousToken = this.token
      // Drop local state up-front so any concurrent request gets the
      // empty token and avoids a 401 race during the round-trip.
      this.token = ''
      setStoredToken('')
      if (previousToken) {
        try {
          await apiFetch('/api/logout', {
            method: 'POST',
            headers: { Authorization: `Bearer ${previousToken}` },
            skipAuth: true,
          })
        } catch {
          /* swallow — backend already won't honour the token. */
        }
      }
      this.status = 'unauthenticated'
    },

    /**
     * Called by `useApi` when any request comes back 401. Mirrors
     * the legacy behaviour of dropping the token and putting the
     * app back in "show login" state.
     */
    handleUnauthorized(_err: UnauthorizedError): void {
      this.token = ''
      this.status = 'unauthenticated'
      // Token already cleared from storage by apiFetch.
    },
  },
})
