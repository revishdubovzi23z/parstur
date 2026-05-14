// ROADMAP Stage 10.5 — categories sidebar state.
//
// Pinia store backing the legacy `fetchCategories` data field on the
// root Vue instance in `index.html` (~line 1558). The store owns the
// list of category buckets and exposes a `refresh` action that picks
// up the `hideRated` / `hideCollected` flags from the feed filters so
// the counts reflect the same view the grid is rendering.

import { defineStore } from 'pinia'
import { apiFetch, UnauthorizedError } from '../api/client'
import { useSessionStore } from './session'
import type { CategoryEntry } from '../types/feed'

interface CategoriesStoreState {
  items: CategoryEntry[]
  loading: boolean
  error: string
}

export function buildCategoriesUrl(
  hideRated: boolean,
  hideCollected: boolean,
): string {
  const params = new URLSearchParams()
  if (hideRated) params.set('hide_rated', 'true')
  if (hideCollected) params.set('hide_collected', 'true')
  const qs = params.toString()
  return qs ? `/api/categories?${qs}` : '/api/categories'
}

export const useCategoriesStore = defineStore('categories', {
  state: (): CategoriesStoreState => ({
    items: [],
    loading: false,
    error: '',
  }),

  actions: {
    async refresh(hideRated = false, hideCollected = false): Promise<void> {
      const session = useSessionStore()
      if (!session.canCallApi) {
        this.items = []
        return
      }
      this.loading = true
      this.error = ''
      try {
        const res = await apiFetch(buildCategoriesUrl(hideRated, hideCollected))
        if (!res.ok) {
          this.error = `HTTP ${res.status}`
          return
        }
        this.items = ((await res.json()) as CategoryEntry[]) ?? []
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
          this.items = []
        } else {
          this.error = err instanceof Error ? err.message : 'unknown'
        }
      } finally {
        this.loading = false
      }
    },
  },
})
