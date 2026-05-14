// ROADMAP Stage 10.2 — session store integration tests.
//
// These tests cover the state machine of `useSessionStore`. The fetch
// API is stubbed so the tests run in happy-dom without a backend.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useSessionStore } from './session'

function mockResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

describe('useSessionStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('reports `disabled` when backend auth is off', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockResponse({ auth_enabled: false }),
    )
    const store = useSessionStore()
    await store.init()

    expect(store.status).toBe('disabled')
    expect(store.canCallApi).toBe(true)
    expect(store.needsLogin).toBe(false)
  })

  it('reports `unauthenticated` when auth is on and no token is stored', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockResponse({ auth_enabled: true }),
    )
    const store = useSessionStore()
    await store.init()

    expect(store.status).toBe('unauthenticated')
    expect(store.needsLogin).toBe(true)
    expect(store.canCallApi).toBe(false)
  })

  it('reports `authenticated` when auth is on and token already in storage', async () => {
    window.sessionStorage.setItem('authToken', 'cached')
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockResponse({ auth_enabled: true }),
    )
    const store = useSessionStore()
    await store.init()

    expect(store.status).toBe('authenticated')
    expect(store.canCallApi).toBe(true)
  })

  it('login() stores the token and flips status to authenticated', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockResponse({ auth_enabled: true }))
      .mockResolvedValueOnce(
        mockResponse({ token: 'fresh-token', auth_enabled: true }),
      )

    const store = useSessionStore()
    await store.init()
    const ok = await store.login('user', 'pass')

    expect(ok).toBe(true)
    expect(store.status).toBe('authenticated')
    expect(store.token).toBe('fresh-token')
    expect(window.sessionStorage.getItem('authToken')).toBe('fresh-token')
    expect(store.loginError).toBe('')
  })

  it('login() surfaces a 401 as a localized error', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockResponse({ auth_enabled: true }))
      .mockResolvedValueOnce(
        new Response('Unauthorized', { status: 401 }),
      )

    const store = useSessionStore()
    await store.init()
    const ok = await store.login('user', 'wrong')

    expect(ok).toBe(false)
    expect(store.status).toBe('unauthenticated')
    expect(store.loginError).toBe('Неверный логин или пароль')
  })

  it('logout() clears local state and calls the backend', async () => {
    window.sessionStorage.setItem('authToken', 'cached')
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockResponse({ auth_enabled: true }))
      .mockResolvedValueOnce(mockResponse({ ok: true }))

    const store = useSessionStore()
    await store.init()
    expect(store.status).toBe('authenticated')

    await store.logout()
    expect(store.status).toBe('unauthenticated')
    expect(store.token).toBe('')
    expect(window.sessionStorage.getItem('authToken')).toBeNull()
    // /api/logout was called with the stale token attached
    expect(vi.mocked(globalThis.fetch)).toHaveBeenCalledTimes(2)
  })
})
