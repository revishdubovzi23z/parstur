// ROADMAP Stage 10.7f — item-card detail store.
//
// Backs the SPA item-card modal. State holds:
//   - the currently-open item (`item`, `releases`, `collections`),
//   - loading / error markers for the initial fetch,
//   - the last action error (surfaced as a toast inside the modal),
//   - mutating actions wrapping the existing item-scoped endpoints
//     in `routes/items.py`: `set_ids`, `rebind`, `reset_item`,
//     `ignore`, `update_item` (single-item reprocess).
//
// The detail payload comes from `GET /api/item/{id}` which we added
// in this same PR — it bundles the item row, its releases and the
// per-item collection ids, mirroring the way `db.get_feed` already
// decorates the feed list.
//
// The store also exposes `applySingleUpdateCompletion()` — called by
// `useSyncStore` when WS reports the `single_update` job has
// completed, so the open card refreshes itself without manual user
// action.

import { defineStore } from 'pinia'

import { apiFetch, UnauthorizedError } from '../api/client'
import { useFeedStore } from './feed'
import { useSessionStore } from './session'
import { useToastStore } from './toast'

import type { FeedItem, FeedRelease } from '../types/feed'

/** Subset of mutable fields we expose in the "Reset fields" tab.
 *  Must match the column names accepted by `routes/items.py`'s
 *  `ResetFieldsRequest`. */
export const RESETTABLE_FIELDS = [
  'kp_id',
  'imdb_id',
  'rezka_url',
  'kp_rating',
  'imdb_rating',
  'poster_url',
  'description',
] as const

export type ResettableField = (typeof RESETTABLE_FIELDS)[number]

export interface ItemDetailResponse {
  item: FeedItem
  releases: FeedRelease[]
  collections: number[]
}

interface ItemsStoreState {
  /** The currently displayed item or null when the modal is closed. */
  item: FeedItem | null
  releases: FeedRelease[]
  collections: number[]
  loading: boolean
  /** Fatal load error (set by `open()` / `refresh()`). Mutating
   *  actions instead surface their failures via `useToastStore`. */
  error: string | null
  /** Inline error surfaced by the modal next to action buttons (set
   *  by mutating actions). Cleared on each new action call. */
  actionError: string | null
  /** Cross-component intent flag — set by the feed-card hover
   *  shortcut for «🗑️ Сброс данных». ItemCardModal watches
   *  it and opens the reset dialog automatically on the next paint,
   *  then resets the flag. */
  pendingResetDialog: boolean
}

function emptyState(): ItemsStoreState {
  return {
    item: null,
    releases: [],
    collections: [],
    loading: false,
    error: null,
    actionError: null,
    pendingResetDialog: false,
  }
}

export const useItemsStore = defineStore('items', {
  state: (): ItemsStoreState => emptyState(),

  getters: {
    /** True while the detail modal is mounted (i.e. an item is set). */
    isOpen(state): boolean {
      return state.item !== null
    },
    /** True while the user has marked this item ignored. */
    isIgnored(state): boolean {
      const flag = state.item?.is_ignored ?? 0
      return Number(flag) > 0
    },
  },

  actions: {
    /**
     * Fetch `/api/item/{id}` and replace state with the result. If
     * `seed` is provided we eagerly populate `item` from the cached
     * feed payload so the modal can render its header before the
     * round-trip completes (avoids a half-second blank flash).
     */
    async open(itemId: number, seed?: FeedItem | null): Promise<void> {
      const session = useSessionStore()
      this.error = null
      this.actionError = null
      this.loading = true
      if (seed && seed.id === itemId) {
        this.item = seed
        this.releases = seed.releases ?? []
      }
      if (!session.canCallApi) {
        this.loading = false
        return
      }
      try {
        const res = await apiFetch(`/api/item/${itemId}`)
        if (res.status === 404) {
          this.error = 'Item не найден'
          this.item = null
          return
        }
        if (!res.ok) {
          this.error = `HTTP ${res.status}`
          return
        }
        const data = (await res.json()) as ItemDetailResponse
        this.item = data.item
        this.releases = Array.isArray(data.releases) ? data.releases : []
        this.collections = Array.isArray(data.collections)
          ? data.collections
          : []
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
          this.error = 'Требуется вход'
          return
        }
        this.error = err instanceof Error ? err.message : String(err)
      } finally {
        this.loading = false
      }
    },

    /** Pull a fresh `/api/item/{id}` for the currently open item. */
    async refresh(): Promise<void> {
      const currentId = this.item?.id
      if (currentId === undefined) return
      await this.open(currentId, this.item)
    },

    close(): void {
      Object.assign(this, emptyState())
    },

    async _post(
      path: string,
      body: Record<string, unknown> | null,
      label: string,
    ): Promise<boolean> {
      const session = useSessionStore()
      if (!session.canCallApi) return false
      this.actionError = null
      const toast = useToastStore()
      try {
        const init: RequestInit = { method: 'POST' }
        if (body !== null) {
          init.headers = { 'Content-Type': 'application/json' }
          init.body = JSON.stringify(body)
        }
        const res = await apiFetch(path, init)
        if (res.status === 404) {
          this.actionError = `${label}: item не найден`
          toast.error(this.actionError)
          return false
        }
        if (!res.ok) {
          let detail = `HTTP ${res.status}`
          try {
            const data = (await res.json()) as { detail?: string; error?: string }
            detail = data.detail ?? data.error ?? detail
          } catch {
            /* best-effort */
          }
          this.actionError = `${label}: ${detail}`
          toast.error(this.actionError)
          return false
        }
        return true
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
          this.actionError = 'Требуется вход'
          toast.error(this.actionError)
          return false
        }
        const msg = err instanceof Error ? err.message : String(err)
        this.actionError = `${label}: ${msg}`
        toast.error(this.actionError)
        return false
      }
    },

    /** `POST /api/set_ids/{id}` — write the KP/IMDb IDs directly
     *  without rebinding side-effects. */
    async saveIds(
      payload: { kp_id?: string | null; imdb_id?: string | null },
    ): Promise<boolean> {
      const id = this.item?.id
      if (id === undefined) return false
      const ok = await this._post(
        `/api/set_ids/${id}`,
        payload,
        'Сохранение ID',
      )
      if (ok) {
        if (this.item) {
          if (payload.kp_id !== undefined) {
            this.item.kp_id = payload.kp_id ?? null
          }
          if (payload.imdb_id !== undefined) {
            this.item.imdb_id = payload.imdb_id ?? null
          }
        }
        useToastStore().success('ID обновлены')
      }
      return ok
    },

    /** `POST /api/rebind/{id}` — full rebind triggers checked_*=0
     *  flags and an audit-log entry. */
    async rebind(
      payload: {
        kp_id?: string | null
        imdb_id?: string | null
        rezka_url?: string | null
      },
    ): Promise<boolean> {
      const id = this.item?.id
      if (id === undefined) return false
      const ok = await this._post(`/api/rebind/${id}`, payload, 'Перепривязка')
      if (ok) {
        useToastStore().success('Перепривязка выполнена')
        // Pull fresh state so the modal reflects the new checked_*=0
        // flags and any side-effects.
        await this.refresh()
      }
      return ok
    },

    /** `POST /api/reset_item/{id}` — clear selected columns. */
    async resetFields(fields: ResettableField[]): Promise<boolean> {
      const id = this.item?.id
      if (id === undefined) return false
      if (fields.length === 0) return false
      const ok = await this._post(
        `/api/reset_item/${id}`,
        { fields },
        'Сброс полей',
      )
      if (ok) {
        useToastStore().success('Поля сброшены')
        await this.refresh()
      }
      return ok
    },

    /** `POST /api/ignore/{id}` — flip the ignore flag. */
    async toggleIgnore(): Promise<boolean> {
      const id = this.item?.id
      if (id === undefined) return false
      const ok = await this._post(`/api/ignore/${id}`, null, 'Игнор')
      if (ok) {
        // Backend doesn't echo the new state, so reload to be safe.
        await this.refresh()
        // Bubble the flag flip to the feed list too — otherwise the
        // dimmed/hidden visual on the card wouldn't update without
        // a full feed refetch.
        const feed = useFeedStore()
        const cached = feed.items.find((it) => it.id === id)
        if (cached && this.item) cached.is_ignored = this.item.is_ignored
      }
      return ok
    },

    /** `POST /api/update_item/{id}` — kick off a `single_update`
     *  background job. Status reaches us over WS as a `single_update`
     *  process key in `useSyncStore`. */
    async reprocess(): Promise<boolean> {
      const id = this.item?.id
      if (id === undefined) return false
      const ok = await this._post(
        `/api/update_item/${id}`,
        null,
        'Перепроверка',
      )
      if (ok) {
        useToastStore().success('Запущена перепроверка карточки')
      }
      return ok
    },

    /** Called by `useSyncStore` when `single_update` flips to a
     *  terminal status. If the user has the modal open, we refresh
     *  the card so they see the new metadata immediately. */
    onSingleUpdateCompleted(): void {
      if (this.item !== null) void this.refresh()
    },

    /** Fire-and-forget `POST /api/update_item/{id}` for a card the
     *  user hovered over in the feed (no modal open). Used by the
     *  «🔄 Обновить» hover shortcut. */
    async reprocessById(id: number): Promise<boolean> {
      const ok = await this._post(
        `/api/update_item/${id}`,
        null,
        'Перепроверка',
      )
      if (ok) {
        useToastStore().success('Запущена перепроверка карточки')
      }
      return ok
    },

    /**
     * `POST /api/ignore/{id}` for a card in the feed.
     */
    async toggleIgnoreById(id: number): Promise<void> {
      const feed = useFeedStore()
      const cached = feed.items.find((it) => it.id === id)
      const wasIgnored = cached?.is_ignored === 1

      const ok = await this._post(`/api/ignore/${id}`, null, 'Игнор')
      if (ok) {
        // Remove from current view immediately
        feed.items = feed.items.filter((it) => it.id !== id)
        useToastStore().success(
          wasIgnored ? 'Фильм восстановлен' : 'Фильм отправлен в корзину',
        )
      }
    },

    /** Open the item modal and pre-pop the reset dialog inside.
     *  Used by the «🗑️ Сброс» hover shortcut. */
    async openWithResetDialog(
      itemId: number,
      seed?: FeedItem | null,
    ): Promise<void> {
      this.pendingResetDialog = true
      await this.open(itemId, seed)
    },
  },
})
