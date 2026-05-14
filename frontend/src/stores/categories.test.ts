// ROADMAP Stage 10.5 — categories store unit tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { buildCategoriesUrl, useCategoriesStore } from './categories'
import { useSessionStore } from './session'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

describe('buildCategoriesUrl', () => {
  it('returns the bare endpoint when both flags are off', () => {
    expect(buildCategoriesUrl(false, false)).toBe('/api/categories')
  })

  it('appends hide_rated / hide_collected only when true', () => {
    expect(buildCategoriesUrl(true, false)).toBe(
      '/api/categories?hide_rated=true',
    )
    expect(buildCategoriesUrl(false, true)).toBe(
      '/api/categories?hide_collected=true',
    )
    expect(buildCategoriesUrl(true, true)).toBe(
      '/api/categories?hide_rated=true&hide_collected=true',
    )
  })
})

describe('useCategoriesStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('skips the request when the session cannot call the API', async () => {
    const session = useSessionStore()
    session.$patch({ status: 'unauthenticated' })
    const store = useCategoriesStore()

    await store.refresh()
    expect(globalThis.fetch).not.toHaveBeenCalled()
    expect(store.items).toEqual([])
  })

  it('stores the response on success', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson([
        { id: -1, name: 'All', count: 100 },
        { id: 1, name: 'Movies', count: 50 },
      ]),
    )
    const session = useSessionStore()
    session.$patch({ status: 'disabled' })
    const store = useCategoriesStore()

    await store.refresh()
    expect(store.items.map((c) => c.id)).toEqual([-1, 1])
    expect(store.loading).toBe(false)
    expect(store.error).toBe('')
  })

  it('records an error on non-2xx', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('boom', { status: 503 }),
    )
    const session = useSessionStore()
    session.$patch({ status: 'disabled' })
    const store = useCategoriesStore()

    await store.refresh()
    expect(store.error).toBe('HTTP 503')
    expect(store.items).toEqual([])
  })

  it('hands 401 to the session store and clears items', async () => {
    window.sessionStorage.setItem('authToken', 'stale')
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('Unauthorized', { status: 401 }),
    )
    const session = useSessionStore()
    session.$patch({ status: 'authenticated', token: 'stale' })
    const store = useCategoriesStore()

    await store.refresh()

    expect(session.status).toBe('unauthenticated')
    expect(store.items).toEqual([])
  })

  it('forwards hide flags into the URL', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson([]))
    const session = useSessionStore()
    session.$patch({ status: 'disabled' })
    const store = useCategoriesStore()

    await store.refresh(true, true)
    const [url] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(String(url)).toBe(
      '/api/categories?hide_rated=true&hide_collected=true',
    )
  })
})
