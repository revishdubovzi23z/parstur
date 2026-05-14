// ROADMAP Stage 10.2 — apiFetch contract tests.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  AUTH_TOKEN_STORAGE_KEY,
  apiFetch,
  getStoredToken,
  setStoredToken,
  UnauthorizedError,
} from './client'

describe('apiFetch', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('injects Bearer token from sessionStorage', async () => {
    setStoredToken('s3cr3t')
    const fetchMock = vi
      .mocked(globalThis.fetch)
      .mockResolvedValueOnce(new Response('{}', { status: 200 }))

    await apiFetch('/api/anything')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/anything')
    const headers = new Headers(init?.headers as HeadersInit)
    expect(headers.get('Authorization')).toBe('Bearer s3cr3t')
  })

  it('omits Authorization when skipAuth is set', async () => {
    setStoredToken('s3cr3t')
    const fetchMock = vi
      .mocked(globalThis.fetch)
      .mockResolvedValueOnce(new Response('{}', { status: 200 }))

    await apiFetch('/api/login', { skipAuth: true, method: 'POST' })

    const [, init] = fetchMock.mock.calls[0]
    const headers = new Headers(init?.headers as HeadersInit)
    expect(headers.has('Authorization')).toBe(false)
  })

  it('throws UnauthorizedError and clears storage on 401', async () => {
    setStoredToken('expired')
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('Unauthorized', { status: 401 }),
    )

    await expect(apiFetch('/api/feed')).rejects.toBeInstanceOf(UnauthorizedError)
    expect(window.sessionStorage.getItem(AUTH_TOKEN_STORAGE_KEY)).toBeNull()
    expect(getStoredToken()).toBe('')
  })

  it('does NOT throw on 401 when skipAuth is true (login endpoint)', async () => {
    // Hitting /api/login with bad credentials returns 401 too; the
    // caller (session store) wants to inspect that response itself,
    // not have apiFetch eat it.
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('Unauthorized', { status: 401 }),
    )

    const res = await apiFetch('/api/login', { skipAuth: true, method: 'POST' })
    expect(res.status).toBe(401)
  })

  it('returns the raw Response on non-2xx, non-401', async () => {
    setStoredToken('tok')
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('boom', { status: 500 }),
    )

    const res = await apiFetch('/api/feed')
    expect(res.status).toBe(500)
    // Token still stored — 500 is not an auth signal.
    expect(getStoredToken()).toBe('tok')
  })
})
