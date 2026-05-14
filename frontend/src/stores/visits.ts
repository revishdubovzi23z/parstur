// ROADMAP Stage 10.7b — last visit / "new only" state.
//
// Pinia replacement for the legacy `lastVisit` / `showNewOnly` data
// fields and the `fetchLastVisit`, `markVisited`, `isNewItem`,
// `toggleNewOnly` methods on the root Vue instance in `index.html`
// (~1188, 2245-2267). Owns:
//
//  - `lastVisit`: timestamp from `GET /api/last_visit`. Used to
//    decide if a feed item's `latest_release` counts as "new".
//  - `showNewOnly`: toggle persisted in **localStorage** (legacy used
//    localStorage on purpose so the preference survives across tabs).
//
// When `showNewOnly` is on, the feed store rewrites its `min_date`
// filter to the date portion of `lastVisit` before building the
// request URL (see `useFeedStore.fetchFeed`).

import { defineStore } from 'pinia'

import { apiFetch, UnauthorizedError } from '../api/client'
import { useSessionStore } from './session'

interface LastVisitResponse {
  last_visit: string | null
}

interface MarkVisitedResponse {
  status: string
  last_visit: string | null
}

interface VisitStoreState {
  lastVisit: string | null
  showNewOnly: boolean
}

const STORAGE_KEY = 'showNewOnly'

function readPersistedToggle(): boolean {
  if (typeof window === 'undefined') return false
  try {
    return window.localStorage.getItem(STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

function persistToggle(value: boolean): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(STORAGE_KEY, String(value))
  } catch {
    /* private mode etc. — silently ignore */
  }
}

export const useVisitStore = defineStore('visits', {
  state: (): VisitStoreState => ({
    lastVisit: null,
    showNewOnly: readPersistedToggle(),
  }),

  getters: {
    /**
     * The `YYYY-MM-DD` portion of `lastVisit`, suitable as the
     * `min_date` query string param. Legacy did `lastVisit.split(' ')[0]`
     * — same idea, but tolerant of `T`-delimited ISO strings too.
     */
    lastVisitDate: (state): string | null => {
      if (!state.lastVisit) return null
      const m = state.lastVisit.match(/^(\d{4}-\d{2}-\d{2})/)
      return m ? m[1] : null
    },
  },

  actions: {
    /** Fetch the persisted last_visit timestamp. */
    async refresh(): Promise<void> {
      const session = useSessionStore()
      if (!session.canCallApi) return
      try {
        const res = await apiFetch('/api/last_visit')
        if (!res.ok) return
        const data: LastVisitResponse = await res.json()
        this.lastVisit = data.last_visit
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          useSessionStore().handleUnauthorized(err)
        }
      }
    },

    /**
     * Record a visit (legacy fires this on `beforeunload`). Failure
     * is swallowed — the user is leaving the tab anyway.
     */
    async markVisited(): Promise<void> {
      const session = useSessionStore()
      if (!session.canCallApi) return
      try {
        const res = await apiFetch('/api/mark_visited', { method: 'POST' })
        if (!res.ok) return
        const data: MarkVisitedResponse = await res.json()
        if (data.last_visit) this.lastVisit = data.last_visit
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          useSessionStore().handleUnauthorized(err)
        }
      }
    },

    /**
     * Toggle the "new only" filter. Persists to localStorage and
     * refreshes `lastVisit` if turning on (so the date filter is
     * fresh). Caller is responsible for refetching the feed.
     */
    async toggleNewOnly(): Promise<void> {
      this.showNewOnly = !this.showNewOnly
      persistToggle(this.showNewOnly)
      if (this.showNewOnly) {
        await this.refresh()
      }
    },

    /**
     * `true` if the given release date is strictly after the last
     * visit. Mirrors the legacy `isNewItem(item)`.
     */
    isNewRelease(releaseDate: string | null | undefined): boolean {
      if (!releaseDate || !this.lastVisit) return false
      const t = Date.parse(releaseDate)
      const l = Date.parse(this.lastVisit)
      if (Number.isNaN(t) || Number.isNaN(l)) return false
      return t > l
    },
  },
})
