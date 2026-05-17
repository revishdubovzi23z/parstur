// ROADMAP Stage 10.5 — collections sidebar + bookmark-menu state.
//
// Pinia store backing the legacy `fetchCollections`, `createCollection`,
// `deleteCollection`, `renameCollection`, `fetchItemCollections`, and
// `toggleItemCollection` methods on the root Vue instance in
// `index.html` (~lines 1590-1895). The store owns:
//
//  - `items`: list of `{id, name, sort_order, count}` from
//    `/api/collections`.
//  - `itemCollections`: map of `item_id -> [collection_id, ...]` for
//    the cards currently rendered in the feed grid. Filled in batch
//    via `/api/batch_item_collections` (Stage 5.3 made that endpoint
//    a single round-trip).
//  - `selectedId`: which collection is currently filtering the feed
//    (set by the sidebar; consumed by `useFeedStore`).

import { defineStore } from 'pinia'
import { apiFetch, UnauthorizedError } from '../api/client'
import { useSessionStore } from './session'
import type { Collection, ItemCollectionsMap } from '../types/feed'

interface CollectionsStoreState {
  items: Collection[]
  itemCollections: ItemCollectionsMap
  selectedId: number | null
  loading: boolean
  error: string
}

interface ToggleResponse {
  status: string
  action: 'added' | 'removed' | string
}

export const useCollectionsStore = defineStore('collections', {
  state: (): CollectionsStoreState => ({
    items: [],
    itemCollections: {},
    selectedId: null,
    loading: false,
    error: '',
  }),

  getters: {
    /**
     * Lookup helper: which collections does a given item belong to?
     * Always returns an array (never undefined) so callers don't have
     * to null-check on every render.
     */
    collectionsForItem:
      (state) =>
      (itemId: number): number[] =>
        state.itemCollections[itemId] ?? [],
  },

  actions: {
    select(id: number | null): void {
      this.selectedId = id
    },

    async refresh(): Promise<void> {
      const session = useSessionStore()
      if (!session.canCallApi) {
        this.items = []
        return
      }
      this.loading = true
      this.error = ''
      try {
        const res = await apiFetch('/api/collections')
        if (!res.ok) {
          this.error = `HTTP ${res.status}`
          return
        }
        this.items = ((await res.json()) as Collection[]) ?? []
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

    async loadItemCollections(itemIds: number[]): Promise<void> {
      const session = useSessionStore()
      if (!session.canCallApi || itemIds.length === 0) return
      try {
        const res = await apiFetch('/api/batch_item_collections', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ids: itemIds }),
        })
        if (!res.ok) return
        const data = (await res.json()) as Record<string, number[]>
        const next: ItemCollectionsMap = { ...this.itemCollections }
        for (const [k, v] of Object.entries(data)) {
          next[Number(k)] = v ?? []
        }
        this.itemCollections = next
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
      }
    },

    async toggleItem(
      itemId: number,
      collectionId: number,
    ): Promise<'added' | 'removed' | null> {
      const session = useSessionStore()
      if (!session.canCallApi) return null
      try {
        const res = await apiFetch(`/api/collections/${collectionId}/toggle`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ item_id: itemId }),
        })
        if (!res.ok) return null
        const data = (await res.json()) as ToggleResponse
        const current = this.itemCollections[itemId] ?? []
        const feedStore = (await import('./feed')).useFeedStore()

        if (data.action === 'added') {
          this.itemCollections = {
            ...this.itemCollections,
            [itemId]: current.includes(collectionId)
              ? current
              : [...current, collectionId],
          }
          // Update count optimistically (replace object for reactivity)
          const idx = this.items.findIndex((c) => c.id === collectionId)
          if (idx !== -1) {
            this.items[idx] = {
              ...this.items[idx],
              count: (this.items[idx].count ?? 0) + 1,
            }
          }
          // If the feed is set to hide collected items, remove this one now
          if (feedStore.filters.hideCollected) {
            feedStore.items = feedStore.items.filter((it) => it.id !== itemId)
            feedStore.onItemRemoved()
          }
          return 'added'
        }
        if (data.action === 'removed') {
          this.itemCollections = {
            ...this.itemCollections,
            [itemId]: current.filter((c) => c !== collectionId),
          }
          // Update count optimistically (replace object for reactivity)
          const idx = this.items.findIndex((c) => c.id === collectionId)
          if (idx !== -1) {
            this.items[idx] = {
              ...this.items[idx],
              count: Math.max(0, (this.items[idx].count ?? 0) - 1),
            }
          }
          return 'removed'
        }
        return null
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
        return null
      }
    },

    async createCollection(name: string): Promise<boolean> {
      const session = useSessionStore()
      if (!session.canCallApi) return false
      const trimmed = name.trim()
      if (!trimmed) return false
      try {
        const res = await apiFetch('/api/collections', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: trimmed }),
        })
        if (!res.ok) return false
        const data = (await res.json()) as { status?: string }
        if (data?.status === 'success') {
          await this.refresh()
          return true
        }
        return false
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
        return false
      }
    },

    async renameCollection(id: number, name: string): Promise<boolean> {
      const session = useSessionStore()
      if (!session.canCallApi) return false
      const trimmed = name.trim()
      if (!trimmed) return false
      try {
        const res = await apiFetch(`/api/collections/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: trimmed }),
        })
        if (!res.ok) return false
        await this.refresh()
        return true
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
        return false
      }
    },

    async deleteCollection(id: number): Promise<boolean> {
      const session = useSessionStore()
      if (!session.canCallApi) return false
      try {
        const res = await apiFetch(`/api/collections/${id}`, {
          method: 'DELETE',
        })
        if (!res.ok) return false
        if (this.selectedId === id) {
          this.selectedId = null
        }
        await this.refresh()
        return true
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
        return false
      }
    },

    /**
     * Persist the current drag-and-drop reorder to the backend.
     *
     * The legacy frontend used SortableJS in the sidebar to let users
     * rearrange collection rows. On drop it POSTed the new order
     * (array of collection IDs in display order) to
     * `/api/collections/save_order`. We mirror that here. The local
     * `items` array is reordered first so the UI feels instant; on
     * failure we re-fetch from the server to roll back.
     */
    async saveOrder(orderedIds: number[]): Promise<boolean> {
      const session = useSessionStore()
      if (!session.canCallApi) return false
      const next = orderedIds
        .map((id) => this.items.find((c) => c.id === id))
        .filter((c): c is Collection => c !== undefined)
      if (next.length !== this.items.length) {
        // Drop request — IDs missing or stale. Refuse to corrupt
        // state.
        return false
      }
      // Optimistic local reorder so the sidebar stays smooth.
      this.items = next.map((c, idx) => ({ ...c, sort_order: idx }))
      try {
        const res = await apiFetch('/api/collections/save_order', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ order: orderedIds }),
        })
        if (!res.ok) {
          await this.refresh()
          return false
        }
        return true
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
        await this.refresh()
        return false
      }
    },
  },
})
