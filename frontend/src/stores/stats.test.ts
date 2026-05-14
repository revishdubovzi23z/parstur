// ROADMAP Stage 10.7c — stats/job-history store unit tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useStatsStore } from './stats'
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

const STATS_PAYLOAD = {
  total_video: 1234,
  no_poster: 12,
  no_ratings: 7,
  no_rezka: 3,
  no_ids: 1,
  last_runs: { sync_video: '2024-05-10 12:00:00' },
}

const HISTORY_PAYLOAD = [
  {
    id: 1,
    job_type: 'sync_video',
    start_time: '2024-05-10 12:00:00',
    end_time: '2024-05-10 12:01:30',
    duration: 90,
    items_processed: 50,
    total_items: 50,
    status: 'completed',
  },
  {
    id: 2,
    job_type: 'rezka',
    start_time: '2024-05-09 09:00:00',
    end_time: '2024-05-09 09:00:30',
    duration: 30,
    items_processed: 5,
    total_items: 10,
    status: 'error',
  },
]

describe('useStatsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('refresh', () => {
    it('skips both requests when the session cannot call the API', async () => {
      const session = useSessionStore()
      session.$patch({ status: 'unauthenticated' })
      const stats = useStatsStore()
      await stats.refresh()
      expect(globalThis.fetch).not.toHaveBeenCalled()
    })

    it('issues parallel GETs against /api/stats and /api/job_history', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson(STATS_PAYLOAD))
        .mockResolvedValueOnce(mockJson(HISTORY_PAYLOAD))
      authorise()
      const stats = useStatsStore()

      await stats.refresh(10)

      const calls = vi.mocked(globalThis.fetch).mock.calls.map((c) => String(c[0]))
      expect(calls).toContain('/api/stats')
      expect(calls).toContain('/api/job_history?limit=10')
      expect(stats.stats.total_video).toBe(1234)
      expect(stats.stats.no_poster).toBe(12)
      expect(stats.stats.last_runs.sync_video).toBe('2024-05-10 12:00:00')
      expect(stats.jobHistory).toHaveLength(2)
      expect(stats.jobHistory[0].job_type).toBe('sync_video')
      expect(stats.loading).toBe(false)
      expect(stats.error).toBeNull()
    })

    it('respects a custom limit', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson(STATS_PAYLOAD))
        .mockResolvedValueOnce(mockJson([]))
      authorise()
      const stats = useStatsStore()

      await stats.refresh(25)

      const calls = vi.mocked(globalThis.fetch).mock.calls.map((c) => String(c[0]))
      expect(calls).toContain('/api/job_history?limit=25')
    })

    it('records the error and stops loading when /api/stats fails', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(new Response('boom', { status: 500 }))
        .mockResolvedValueOnce(mockJson(HISTORY_PAYLOAD))
      authorise()
      const stats = useStatsStore()

      await stats.refresh()

      expect(stats.error).toMatch(/500/)
      expect(stats.loading).toBe(false)
      // Stays at the initial zeros — we don't apply a partial update.
      expect(stats.stats.total_video).toBe(0)
    })

    it('records the error when /api/job_history fails', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson(STATS_PAYLOAD))
        .mockResolvedValueOnce(new Response('boom', { status: 502 }))
      authorise()
      const stats = useStatsStore()

      await stats.refresh()

      expect(stats.error).toMatch(/502/)
      expect(stats.jobHistory).toEqual([])
    })

    it('flips the session to unauthenticated on a 401', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(new Response('nope', { status: 401 }))
        .mockResolvedValueOnce(mockJson(HISTORY_PAYLOAD))
      authorise()
      const stats = useStatsStore()

      await stats.refresh()

      expect(useSessionStore().status).toBe('unauthenticated')
      expect(stats.error).toMatch(/Сбой запроса|Unauthorized/)
    })

    it('falls back to empty `last_runs` and array when API omits them', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(
          mockJson({
            total_video: 5,
            no_poster: 0,
            no_ratings: 0,
            no_rezka: 0,
            no_ids: 0,
          }),
        )
        .mockResolvedValueOnce(mockJson(null))
      authorise()
      const stats = useStatsStore()

      await stats.refresh()

      expect(stats.stats.last_runs).toEqual({})
      expect(stats.jobHistory).toEqual([])
    })
  })

  describe('hasEnrichmentBacklog', () => {
    it('is false when all enrichment counters are zero', () => {
      const stats = useStatsStore()
      expect(stats.hasEnrichmentBacklog).toBe(false)
    })

    it('is true when any of no_poster / no_ratings / no_rezka is positive', () => {
      const stats = useStatsStore()
      stats.stats = { ...stats.stats, no_poster: 1 }
      expect(stats.hasEnrichmentBacklog).toBe(true)
      stats.stats = { ...stats.stats, no_poster: 0, no_ratings: 1 }
      expect(stats.hasEnrichmentBacklog).toBe(true)
      stats.stats = { ...stats.stats, no_ratings: 0, no_rezka: 1 }
      expect(stats.hasEnrichmentBacklog).toBe(true)
    })
  })

  describe('reset', () => {
    it('clears stats / history / error / loading', () => {
      const stats = useStatsStore()
      stats.stats = { ...stats.stats, total_video: 99 }
      stats.jobHistory = HISTORY_PAYLOAD as never
      stats.error = 'boom'
      stats.loading = true

      stats.reset()

      expect(stats.stats.total_video).toBe(0)
      expect(stats.jobHistory).toEqual([])
      expect(stats.error).toBeNull()
      expect(stats.loading).toBe(false)
    })
  })
})
