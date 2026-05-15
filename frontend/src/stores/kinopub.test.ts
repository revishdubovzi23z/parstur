// Unit tests for the kinopub store (PR 2 of the kino.pub integration).
//
// Drives the store through fetchStatus / startDeviceFlow / startPolling /
// logout while mocking `window.fetch`. Mirrors `admin.test.ts`.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useKinopubStore } from './kinopub'
import { useSessionStore } from './session'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function authorise(): void {
  const session = useSessionStore()
  session.$patch({ status: 'disabled' })
}

describe('useKinopubStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch')
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  describe('fetchStatus', () => {
    it('parses and stores the response on 200', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({
          enabled: true,
          authenticated: true,
          expires_at: 1_700_003_600,
          expires_in: 3600,
          client_id: 'xbmc',
        }),
      )
      authorise()
      const store = useKinopubStore()
      await store.fetchStatus()
      expect(store.status).toEqual({
        enabled: true,
        authenticated: true,
        expiresAt: 1_700_003_600,
        expiresIn: 3600,
        clientId: 'xbmc',
      })
      expect(store.isAuthenticated).toBe(true)
      expect(store.statusError).toBe('')
    })

    it('captures error message on non-2xx', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        new Response('boom', { status: 500 }),
      )
      authorise()
      const store = useKinopubStore()
      await store.fetchStatus()
      expect(store.statusError).toContain('500')
      expect(store.status).toBe(null)
    })
  })

  describe('startDeviceFlow', () => {
    it('populates flow state on success', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({
          device_code: 'DC',
          user_code: 'WXYZ-1234',
          verification_uri: 'https://kino.pub/device',
          interval: 5,
          expires_in: 600,
        }),
      )
      authorise()
      const store = useKinopubStore()
      await store.startDeviceFlow()
      expect(store.flow?.deviceCode).toBe('DC')
      expect(store.flow?.userCode).toBe('WXYZ-1234')
      expect(store.flow?.interval).toBe(5)
      expect(store.pollState).toBe('pending')
      expect(store.flowBusy).toBe(false)
    })

    it('surfaces backend detail on 503', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ detail: 'kino.pub disabled' }, { status: 503 }),
      )
      authorise()
      const store = useKinopubStore()
      await store.startDeviceFlow()
      expect(store.flow).toBe(null)
      expect(store.pollError).toContain('kino.pub disabled')
      expect(store.statusError).toContain('kino.pub disabled')
      expect(store.pollState).toBe('expired')
      expect(store.flowBusy).toBe(false)
    })
  })

  describe('polling', () => {
    it('transitions to confirmed after a successful poll and refreshes status', async () => {
      // 1) startDeviceFlow
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({
          device_code: 'DC',
          user_code: 'WXYZ',
          verification_uri: 'https://kino.pub/device',
          interval: 2,
          expires_in: 600,
        }),
      )
      // 2) immediate first poll → confirmed
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ state: 'confirmed' }),
      )
      // 3) fetchStatus follow-up
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({
          enabled: true,
          authenticated: true,
          expires_at: 1_700_003_600,
          expires_in: 3600,
          client_id: 'xbmc',
        }),
      )

      authorise()
      const store = useKinopubStore()
      await store.startDeviceFlow()
      store.startPolling()
      // Let the immediate tick finish.
      await vi.runOnlyPendingTimersAsync()
      // Drain any chained microtasks (fetchStatus).
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()

      expect(store.pollState).toBe('confirmed')
      expect(store.pollTimer).toBe(null)
      expect(store.isAuthenticated).toBe(true)
    })

    it('stops polling once the device_code expires client-side', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({
          device_code: 'DC',
          user_code: 'WXYZ',
          verification_uri: 'https://kino.pub/device',
          interval: 2,
          expires_in: 1, // expires almost immediately
        }),
      )

      authorise()
      const store = useKinopubStore()
      await store.startDeviceFlow()
      // Jump the wall-clock 5 seconds past the device_code TTL.
      vi.setSystemTime(new Date(Date.now() + 5000))
      store.startPolling()
      await vi.runOnlyPendingTimersAsync()

      expect(store.pollState).toBe('expired')
      expect(store.pollTimer).toBe(null)
    })

    it('cancelDeviceFlow clears state and stops polling', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({
          device_code: 'DC',
          user_code: 'WXYZ',
          verification_uri: 'https://kino.pub/device',
          interval: 5,
          expires_in: 600,
        }),
      )
      authorise()
      const store = useKinopubStore()
      await store.startDeviceFlow()
      store.startPolling()
      store.cancelDeviceFlow()
      expect(store.flow).toBe(null)
      expect(store.pollState).toBe('idle')
      expect(store.pollTimer).toBe(null)
    })
  })

  describe('logout', () => {
    it('hits /api/kinopub/logout and re-fetches status', async () => {
      // 1) logout
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ status: 'ok' }),
      )
      // 2) fetchStatus follow-up
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({
          enabled: true,
          authenticated: false,
          expires_at: null,
          expires_in: null,
          client_id: 'xbmc',
        }),
      )
      authorise()
      const store = useKinopubStore()
      await store.logout()
      expect(store.isAuthenticated).toBe(false)
      expect(store.logoutBusy).toBe(false)
    })
  })

  // ── PR 3 actions ─────────────────────────────────────────────────────

  describe('search', () => {
    it('skips the request when the query is whitespace', async () => {
      authorise()
      const store = useKinopubStore()
      store.searchResults = [
        { id: 1, title: 't', year: 2020, type: 'movie', url: 'u', poster: null },
      ]
      await store.search('   ')
      expect(globalThis.fetch).not.toHaveBeenCalled()
      expect(store.searchResults).toEqual([])
    })

    it('passes title and optional filters in the query string', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({
          results: [
            {
              id: 5,
              title: 'Inception',
              year: 2010,
              type: 'movie',
              url: 'https://kino.pub/item/5',
              poster: null,
            },
          ],
        }),
      )
      authorise()
      const store = useKinopubStore()
      await store.search('Inception', { year: 2010, type: 'movie', limit: 5 })
      const url = String(vi.mocked(globalThis.fetch).mock.calls[0][0])
      expect(url).toContain('title=Inception')
      expect(url).toContain('year=2010')
      expect(url).toContain('type=movie')
      expect(url).toContain('limit=5')
      expect(store.searchResults).toHaveLength(1)
      expect(store.searchResults[0].id).toBe(5)
    })

    it('surfaces server-side error detail on failure', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson(
          { detail: 'kino.pub upstream is down' },
          { status: 502 },
        ),
      )
      authorise()
      const store = useKinopubStore()
      await store.search('x')
      expect(store.searchResults).toEqual([])
      expect(store.searchError).toBe('kino.pub upstream is down')
    })

    it('clearSearch wipes the cached results', async () => {
      authorise()
      const store = useKinopubStore()
      store.searchResults = [
        { id: 1, title: 't', year: 2020, type: 'movie', url: 'u', poster: null },
      ]
      store.searchError = 'old'
      store.clearSearch()
      expect(store.searchResults).toEqual([])
      expect(store.searchError).toBe('')
    })
  })

  describe('bind / unbind', () => {
    it('POSTs the payload to /api/kinopub/bind/{id}', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({
          status: 'success',
          before: { kinopub_id: null },
          after: { kinopub_id: 12345 },
        }),
      )
      authorise()
      const store = useKinopubStore()
      const ok = await store.bind(42, {
        kinopub_id: 12345,
        kinopub_type: 'movie',
      })
      expect(ok).toBe(true)
      const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
      expect(String(url)).toBe('/api/kinopub/bind/42')
      expect(init?.method).toBe('POST')
      expect(JSON.parse(String(init?.body))).toEqual({
        kinopub_id: 12345,
        kinopub_type: 'movie',
      })
    })

    it('returns false and records error detail on failure', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ detail: 'item not found' }, { status: 404 }),
      )
      authorise()
      const store = useKinopubStore()
      const ok = await store.bind(99, { kinopub_id: 1 })
      expect(ok).toBe(false)
      expect(store.bindError).toBe('item not found')
    })

    it('POSTs to /api/kinopub/unbind/{id}', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({
          status: 'success',
          before: { kinopub_id: 1 },
          after: { kinopub_id: null },
        }),
      )
      authorise()
      const store = useKinopubStore()
      const ok = await store.unbind(42)
      expect(ok).toBe(true)
      const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
      expect(String(url)).toBe('/api/kinopub/unbind/42')
      expect(init?.method).toBe('POST')
    })
  })

  describe('fetchStreamInfo', () => {
    it('GETs /api/kinopub/stream_info/{id} and stores the payload', async () => {
      const payload = {
        id: 555,
        title: 'Inception',
        year: 2010,
        type: 'movie',
        url: 'https://kino.pub/item/555',
        videos: [],
        seasons: [],
      }
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(payload))
      authorise()
      const store = useKinopubStore()
      await store.fetchStreamInfo(42)
      expect(store.streamInfo).toEqual(payload)
      expect(store.streamError).toBe('')
    })

    it('records the server detail on failure', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ detail: 'item is not bound to a kino.pub id' }, { status: 409 }),
      )
      authorise()
      const store = useKinopubStore()
      await store.fetchStreamInfo(42)
      expect(store.streamInfo).toBe(null)
      expect(store.streamError).toBe('item is not bound to a kino.pub id')
    })
  })
})
