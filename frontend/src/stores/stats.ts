// ROADMAP Stage 10.7c — Stats + Job History store.
//
// Pinia replacement for the legacy `stats` / `jobHistory` data fields
// and the `fetchStats()` method on the root Vue instance in
// `index.html` (~lines 1186-1187, 2075-2091).
//
// Owns two pieces of state:
//
//  - `stats`: aggregate counters from `GET /api/stats`
//    (`total_video`, `no_poster`, `no_ratings`, `no_rezka`, `no_ids`,
//    `last_runs`).
//  - `jobHistory`: last N entries from `GET /api/job_history?limit=N`.
//
// `refresh()` hits both endpoints in parallel (legacy fired them
// sequentially, but the requests are independent and the parallel
// version is strictly faster). On 401 the session store is notified
// via `handleUnauthorized` so the login modal can re-open.

import { defineStore } from 'pinia'

import { apiFetch, UnauthorizedError } from '../api/client'
import { useSessionStore } from './session'

/** Aggregate stats counters returned by `/api/stats`. */
export interface StatsSnapshot {
  total_video: number
  no_poster: number
  no_ratings: number
  no_rezka: number
  no_ids: number
  /** Map of `job_type` → ISO timestamp of the last successful run. */
  last_runs: Record<string, string>
}

/** One row of `/api/job_history` (legacy `job_history` table). */
export interface JobHistoryEntry {
  id: number
  job_type: string
  start_time: string
  end_time: string | null
  duration: number | null
  items_processed: number
  total_items: number
  status: 'completed' | 'error' | 'stopped' | string
}

interface StatsStoreState {
  stats: StatsSnapshot
  jobHistory: JobHistoryEntry[]
  loading: boolean
  error: string | null
}

const EMPTY_STATS: StatsSnapshot = {
  total_video: 0,
  no_poster: 0,
  no_ratings: 0,
  no_rezka: 0,
  no_ids: 0,
  last_runs: {},
}

function describe(err: unknown): string {
  if (err instanceof Error) return err.message
  return String(err)
}

export const useStatsStore = defineStore('stats', {
  state: (): StatsStoreState => ({
    stats: { ...EMPTY_STATS, last_runs: {} },
    jobHistory: [],
    loading: false,
    error: null,
  }),

  getters: {
    /**
     * `true` when at least one enrichment counter is non-zero. The
     * dashboard renders an "обогатить" banner in that case (the
     * actual sync trigger lives on the upcoming 10.7d sync controls).
     */
    hasEnrichmentBacklog: (state): boolean =>
      state.stats.no_poster > 0 ||
      state.stats.no_ratings > 0 ||
      state.stats.no_rezka > 0,
  },

  actions: {
    /**
     * Fetch `/api/stats` and `/api/job_history?limit=N` in parallel
     * and update state. Errors set `error` and leave previous data
     * intact so the panel can fall back to "stale" rendering instead
     * of flashing zeros.
     */
    async refresh(limit = 10): Promise<void> {
      const session = useSessionStore()
      if (!session.canCallApi) return
      this.loading = true
      this.error = null
      try {
        const [statsRes, historyRes] = await Promise.all([
          apiFetch('/api/stats'),
          apiFetch(`/api/job_history?limit=${limit}`),
        ])
        if (!statsRes.ok) {
          this.error = `HTTP ${statsRes.status} при загрузке /api/stats`
          return
        }
        if (!historyRes.ok) {
          this.error = `HTTP ${historyRes.status} при загрузке /api/job_history`
          return
        }
        const statsData = (await statsRes.json()) as Partial<StatsSnapshot>
        const historyData = (await historyRes.json()) as JobHistoryEntry[]
        this.stats = {
          ...EMPTY_STATS,
          ...statsData,
          last_runs: statsData.last_runs ?? {},
        }
        this.jobHistory = Array.isArray(historyData) ? historyData : []
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
        this.error = `Сбой запроса: ${describe(err)}`
      } finally {
        this.loading = false
      }
    },

    /** Reset to a pristine state. Used when closing the panel. */
    reset(): void {
      this.stats = { ...EMPTY_STATS, last_runs: {} }
      this.jobHistory = []
      this.error = null
      this.loading = false
    },
  },
})
