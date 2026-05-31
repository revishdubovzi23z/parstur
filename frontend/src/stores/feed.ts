// ROADMAP Stage 10.4 — feed list state.
//
// Pinia store that drives the SPA feed grid. Mirrors the behaviour
// of the legacy `fetchFeed()` method on the root Vue instance in
// `index.html` (see ~line 1564): build a query string from the
// current filters, GET `/api/feed`, store `items` + `totalPages`,
// expose loading / error flags. The store does not render UI; the
// `FeedView` component subscribes to it.
//
// The store deliberately stops short of the legacy
// `fetchItemCollections` / `fetchCollections` chain — that's the job
// of Stage 10.5. Pagination and basic filters land here so the new
// `/beta` page is browsable end-to-end with auth disabled.

import { defineStore } from 'pinia'
import { apiFetch, UnauthorizedError } from '../api/client'
import { useCollectionsStore } from './collections'
import { useSessionStore } from './session'
import { useVisitStore } from './visits'
import {
  DEFAULT_FEED_FILTERS,
  type FeedFilters,
  type FeedItem,
  type FeedRelease,
  type FeedResponse,
} from '../types/feed'

interface FeedStoreState {
  items: FeedItem[]
  page: number
  limit: number
  totalPages: number
  loading: boolean
  /** Connection-level failure (fetch threw or non-2xx that isn't 401). */
  connectionError: boolean
  filters: FeedFilters
  /** Timestamp of the last successful fetch — used by tests + debug UI. */
  lastFetchedAt: number | null
}

/**
 * Build the `/api/feed?...` URL from the current pagination + filters.
 * Exported for unit tests so the contract with the backend is locked
 * down independently of the store wiring.
 */
export function buildFeedUrl(
  page: number,
  limit: number,
  filters: FeedFilters,
): string {
  const params = new URLSearchParams()
  params.set('page', String(page))
  params.set('limit', String(limit))
  params.set('category_id', String(filters.categoryId))
  if (filters.collectionId != null) {
    params.set('collection_id', String(filters.collectionId))
  }
  if (filters.search.trim()) {
    params.set('search', filters.search.trim())
  }
  if (filters.minYear != null) params.set('min_year', String(filters.minYear))
  if (filters.maxYear != null) params.set('max_year', String(filters.maxYear))
  if (filters.minDate) params.set('min_date', filters.minDate)
  if (filters.maxDate) params.set('max_date', filters.maxDate)
  if (filters.minKp > 0) params.set('min_kp', String(filters.minKp))
  if (filters.maxKp < 10) params.set('max_kp', String(filters.maxKp))
  if (filters.minImdb > 0) params.set('min_imdb', String(filters.minImdb))
  if (filters.maxImdb < 10) params.set('max_imdb', String(filters.maxImdb))
  if (filters.hideRated) params.set('hide_rated', 'true')
  if (filters.hideCollected) params.set('hide_collected', 'true')
  if (filters.sortBy && filters.sortBy !== 'date_desc') {
    params.set('sort_by', filters.sortBy)
  }
  return `/api/feed?${params.toString()}`
}

export const useFeedStore = defineStore('feed', {
  state: (): FeedStoreState => ({
    items: [],
    page: 1,
    limit: 20,
    totalPages: 1,
    loading: false,
    connectionError: false,
    filters: { ...DEFAULT_FEED_FILTERS },
    lastFetchedAt: null,
  }),

  getters: {
    hasItems: (state): boolean => state.items.length > 0,
    canPrevPage: (state): boolean => state.page > 1,
    canNextPage: (state): boolean => state.page < state.totalPages,
  },

  actions: {
    setFilters(patch: Partial<FeedFilters>): void {
      this.filters = { ...this.filters, ...patch }
      this.page = 1
    },

    resetFilters(): void {
      this.filters = { ...DEFAULT_FEED_FILTERS }
      this.page = 1
    },

    /**
     * Restore the SPA to its initial state — used by the clickable
     * logo in the header. Clears every feed filter and resets to the
     * first page; the caller is responsible for tearing down sibling
     * state (e.g. visits.showNewOnly, collections.selectedId) and
     * re-firing fetches. We keep the per-store concerns explicit so
     * tests can pin them individually.
     */
    homeReset(): void {
      this.filters = { ...DEFAULT_FEED_FILTERS }
      this.page = 1
    },

    setPage(page: number): void {
      const next = Math.max(1, Math.min(page, this.totalPages || 1))
      if (next !== this.page) {
        this.page = next
      }
    },

    onItemRemoved(): void {
      if (this.items.length === 0) {
        if (this.page > 1) {
          this.page -= 1
        }
        void this.fetchFeed()
      }
    },

    async fetchFeed(): Promise<void> {
      // Don't bother hitting the backend if we know the session is
      // not authenticated — the legacy code does the same so 401
      // bursts don't pile up while the login modal is up.
      const session = useSessionStore()
      if (!session.canCallApi) {
        this.items = []
        this.totalPages = 1
        this.loading = false
        return
      }

      this.loading = true
      try {
        // Stage 10.7b — when the visit store has "new only" toggled
        // on we narrow the request to releases newer than the last
        // visit. This overrides the user's manual min_date filter,
        // matching the legacy precedence in `index.html:1581`.
        const visits = useVisitStore()
        const effectiveFilters =
          visits.showNewOnly && visits.lastVisitDate
            ? { ...this.filters, minDate: visits.lastVisitDate }
            : this.filters
        const url = buildFeedUrl(this.page, this.limit, effectiveFilters)
        const res = await apiFetch(url)
        if (!res.ok) {
          this.connectionError = true
          return
        }
        const data = (await res.json()) as FeedResponse
        this.items = data.items ?? []
        this.totalPages = data.totalPages ?? 1
        this.connectionError = false
        this.lastFetchedAt = Date.now()
        if (this.page > this.totalPages) {
          this.page = this.totalPages
          // Re-fetch for the clamped page
          await this.fetchFeed()
          return
        }
        // Stage 10.5 — fan out into the collections store so cards
        // can render their bookmark badge. Don't await; the cards
        // will reactively pick up the result once it lands.
        if (this.items.length > 0) {
          const collections = useCollectionsStore()
          void collections.loadItemCollections(this.items.map((it) => it.id))
        }
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          // 401 already cleared the token inside apiFetch. Let the
          // session store flip the modal back open; the feed itself
          // just shows an empty connection-error free state.
          session.handleUnauthorized(err)
          this.items = []
          this.totalPages = 1
          this.connectionError = false
        } else {
          this.connectionError = true
        }
      } finally {
        this.loading = false
      }
    },

    async nextPage(): Promise<void> {
      if (!this.canNextPage) return
      this.page += 1
      await this.fetchFeed()
    },

    async prevPage(): Promise<void> {
      if (!this.canPrevPage) return
      this.page -= 1
      await this.fetchFeed()
    },

    /**
     * Pull fresh data for a single item (e.g. after a background
     * update finished) and replace it in the items list if present.
     */
    async updateItemById(id: number): Promise<void> {
      const session = useSessionStore()
      if (!session.canCallApi) return

      try {
        const res = await apiFetch(`/api/item/${id}`)
        if (!res.ok) return

        const data = (await res.json()) as {
          item: FeedItem
          releases: FeedRelease[]
          collections: number[]
        }
        const index = this.items.findIndex((it) => it.id === id)
        if (index !== -1) {
          // Merge releases into the item object so the card reflects them
          const updated = { ...data.item, releases: data.releases }
          this.items[index] = updated
        }
      } catch {
        /* silent fail */
      }
    },
  },
})
