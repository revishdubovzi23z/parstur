// ROADMAP Stage 10.7b — visit store unit tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useVisitStore } from './visits'
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

describe('useVisitStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    window.localStorage.clear()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('refresh', () => {
    it('stores the last_visit from the API', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ last_visit: '2024-05-10 12:34:56' }),
      )
      authorise()
      const visits = useVisitStore()
      await visits.refresh()
      expect(visits.lastVisit).toBe('2024-05-10 12:34:56')
      expect(visits.lastVisitDate).toBe('2024-05-10')
    })

    it('skips the request when the session cannot call the API', async () => {
      const session = useSessionStore()
      session.$patch({ status: 'unauthenticated' })
      const visits = useVisitStore()
      await visits.refresh()
      expect(globalThis.fetch).not.toHaveBeenCalled()
    })

    it('silently leaves state untouched on non-2xx responses', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        new Response('boom', { status: 500 }),
      )
      authorise()
      const visits = useVisitStore()
      visits.lastVisit = 'sentinel'
      await visits.refresh()
      expect(visits.lastVisit).toBe('sentinel')
    })
  })

  describe('markVisited', () => {
    it('POSTs to /api/mark_visited and stores last_visit', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ status: 'success', last_visit: '2024-06-01 00:00:00' }),
      )
      authorise()
      const visits = useVisitStore()
      await visits.markVisited()
      const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
      expect(String(url)).toBe('/api/mark_visited')
      expect((init as RequestInit).method).toBe('POST')
      expect(visits.lastVisit).toBe('2024-06-01 00:00:00')
    })

    it('does nothing when the session cannot call the API', async () => {
      const session = useSessionStore()
      session.$patch({ status: 'unauthenticated' })
      const visits = useVisitStore()
      await visits.markVisited()
      expect(globalThis.fetch).not.toHaveBeenCalled()
    })
  })

  describe('toggleNewOnly', () => {
    it('flips, persists to localStorage, and refreshes on enable', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ last_visit: '2024-04-01 00:00:00' }),
      )
      authorise()
      const visits = useVisitStore()
      expect(visits.showNewOnly).toBe(false)
      await visits.toggleNewOnly()
      expect(visits.showNewOnly).toBe(true)
      expect(window.localStorage.getItem('showNewOnly')).toBe('true')
      expect(vi.mocked(globalThis.fetch).mock.calls[0][0]).toBe('/api/last_visit')
    })

    it('does not refresh when disabling', async () => {
      window.localStorage.setItem('showNewOnly', 'true')
      authorise()
      const visits = useVisitStore()
      // Re-init the store to pick up the persisted value.
      visits.$patch({ showNewOnly: true })
      await visits.toggleNewOnly()
      expect(visits.showNewOnly).toBe(false)
      expect(window.localStorage.getItem('showNewOnly')).toBe('false')
      expect(globalThis.fetch).not.toHaveBeenCalled()
    })
  })

  describe('isNewRelease', () => {
    it('returns true when the release is strictly after the last visit', () => {
      const visits = useVisitStore()
      visits.lastVisit = '2024-01-15 12:00:00'
      expect(visits.isNewRelease('2024-01-16 00:00:00')).toBe(true)
    })

    it('returns false when the release is on or before the last visit', () => {
      const visits = useVisitStore()
      visits.lastVisit = '2024-01-15 12:00:00'
      expect(visits.isNewRelease('2024-01-15 12:00:00')).toBe(false)
      expect(visits.isNewRelease('2024-01-15 00:00:00')).toBe(false)
    })

    it('returns false when either side is missing or malformed', () => {
      const visits = useVisitStore()
      expect(visits.isNewRelease(null)).toBe(false)
      expect(visits.isNewRelease(undefined)).toBe(false)
      visits.lastVisit = null
      expect(visits.isNewRelease('2024-01-01')).toBe(false)
      visits.lastVisit = 'not a date'
      expect(visits.isNewRelease('2024-01-01')).toBe(false)
    })
  })

  describe('lastVisitDate getter', () => {
    it('extracts the YYYY-MM-DD portion regardless of separator', () => {
      const visits = useVisitStore()
      visits.lastVisit = '2024-05-10 12:34:56'
      expect(visits.lastVisitDate).toBe('2024-05-10')
      visits.lastVisit = '2024-05-10T12:34:56Z'
      expect(visits.lastVisitDate).toBe('2024-05-10')
      visits.lastVisit = null
      expect(visits.lastVisitDate).toBeNull()
      visits.lastVisit = 'garbage'
      expect(visits.lastVisitDate).toBeNull()
    })
  })
})
