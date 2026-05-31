// ROADMAP Stage 10.7f — item-card store unit tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useItemsStore } from './items'
import { useFeedStore } from './feed'
import { useSessionStore } from './session'
import { useToastStore } from './toast'

import type { FeedItem } from '../types/feed'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function authorise(): void {
  useSessionStore().$patch({ status: 'disabled' })
}

function baseItem(overrides: Partial<FeedItem> = {}): FeedItem {
  return {
    id: 42,
    title: 'Test',
    year: 2020,
    category_id: 1,
    poster_url: null,
    description: null,
    kp_rating: null,
    imdb_rating: null,
    kp_id: '111',
    imdb_id: 'tt001',
    rezka_url: 'https://rezka/x',
    original_title: null,
    is_ignored: 0,
    ...overrides,
  }
}

const DETAIL_PAYLOAD = {
  item: baseItem(),
  releases: [
    { id: 1, item_id: 42, date_added: '2024-02-01', title: 'r1' },
    { id: 2, item_id: 42, date_added: '2024-01-01', title: 'r2' },
  ],
  collections: [3, 7],
}

describe('useItemsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('open / close / refresh', () => {
    it('seeds eagerly from the cached feed item before the GET resolves', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(DETAIL_PAYLOAD))
      authorise()
      const items = useItemsStore()
      const seed = baseItem({ title: 'Seed' })
      const pending = items.open(42, seed)
      // Seed has applied synchronously.
      expect(items.item?.title).toBe('Seed')
      await pending
      expect(items.item?.title).toBe('Test')
      expect(items.releases).toHaveLength(2)
      expect(items.collections).toEqual([3, 7])
      expect(items.isOpen).toBe(true)
    })

    it('skips the GET when the session is unauthenticated', async () => {
      useSessionStore().$patch({ status: 'unauthenticated' })
      const items = useItemsStore()
      await items.open(42, baseItem())
      expect(globalThis.fetch).not.toHaveBeenCalled()
      // Seed still set so the modal can render header.
      expect(items.item?.id).toBe(42)
    })

    it('sets error on 404', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ error: 'item not found' }, { status: 404 }),
      )
      authorise()
      const items = useItemsStore()
      await items.open(99)
      expect(items.item).toBeNull()
      expect(items.error).toBe('Item не найден')
    })

    it('sets error on other non-2xx', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        new Response('boom', { status: 500 }),
      )
      authorise()
      const items = useItemsStore()
      await items.open(42)
      expect(items.error).toBe('HTTP 500')
    })

    it('refresh issues a new GET for the same id', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson(DETAIL_PAYLOAD))
        .mockResolvedValueOnce(
          mockJson({
            ...DETAIL_PAYLOAD,
            item: baseItem({ title: 'Refreshed' }),
          }),
        )
      authorise()
      const items = useItemsStore()
      await items.open(42, baseItem())
      await items.refresh()
      expect(items.item?.title).toBe('Refreshed')
      expect(globalThis.fetch).toHaveBeenCalledTimes(2)
    })

    it('close clears state', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(DETAIL_PAYLOAD))
      authorise()
      const items = useItemsStore()
      await items.open(42)
      items.close()
      expect(items.item).toBeNull()
      expect(items.releases).toEqual([])
      expect(items.isOpen).toBe(false)
    })
  })

  describe('mutating actions', () => {
    it('saveIds POSTs to /api/set_ids and patches the cached item', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson(DETAIL_PAYLOAD))
        .mockResolvedValueOnce(mockJson({ status: 'success' }))
      authorise()
      const items = useItemsStore()
      await items.open(42)
      const ok = await items.saveIds({ kp_id: '999' })
      expect(ok).toBe(true)
      const call = vi.mocked(globalThis.fetch).mock.calls[1]
      expect(String(call[0])).toBe('/api/set_ids/42')
      expect(call[1]?.method).toBe('POST')
      expect(call[1]?.body).toBe(JSON.stringify({ kp_id: '999' }))
      expect(items.item?.kp_id).toBe('999')
    })

    it('rebind POSTs to /api/rebind and triggers a refresh', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson(DETAIL_PAYLOAD)) // open()
        .mockResolvedValueOnce(
          mockJson({ status: 'success', before: {}, after: {} }),
        ) // rebind
        .mockResolvedValueOnce(
          mockJson({
            ...DETAIL_PAYLOAD,
            item: baseItem({ rezka_url: 'https://rezka/new' }),
          }),
        ) // refresh
      authorise()
      const items = useItemsStore()
      await items.open(42)
      const ok = await items.rebind({ rezka_url: 'https://rezka/new' })
      expect(ok).toBe(true)
      expect(globalThis.fetch).toHaveBeenCalledTimes(3)
      const rebindCall = vi.mocked(globalThis.fetch).mock.calls[1]
      expect(String(rebindCall[0])).toBe('/api/rebind/42')
      expect(items.item?.rezka_url).toBe('https://rezka/new')
    })

    it('resetFields posts the selected list and short-circuits when empty', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson(DETAIL_PAYLOAD)) // open
        .mockResolvedValueOnce(mockJson({ status: 'success' })) // reset
        .mockResolvedValueOnce(mockJson(DETAIL_PAYLOAD)) // refresh
      authorise()
      const items = useItemsStore()
      await items.open(42)
      expect(await items.resetFields([])).toBe(false)
      const ok = await items.resetFields(['kp_rating', 'imdb_rating'])
      expect(ok).toBe(true)
      const call = vi.mocked(globalThis.fetch).mock.calls[1]
      expect(String(call[0])).toBe('/api/reset_item/42')
      expect(call[1]?.body).toBe(
        JSON.stringify({ fields: ['kp_rating', 'imdb_rating'] }),
      )
    })

    it('toggleIgnore POSTs to /api/ignore and removes the item from the feed store', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson(DETAIL_PAYLOAD)) // open
        .mockResolvedValueOnce(mockJson({ status: 'success' })) // ignore
      authorise()
      const feed = useFeedStore()
      feed.items.push(baseItem())
      const items = useItemsStore()
      await items.open(42)
      const ok = await items.toggleIgnore()
      expect(ok).toBe(true)
      const ignoreCall = vi.mocked(globalThis.fetch).mock.calls[1]
      expect(String(ignoreCall[0])).toBe('/api/ignore/42')

      // Modal should be closed now
      expect(items.isOpen).toBe(false)
      // Feed entry should be removed
      expect(feed.items).toHaveLength(0)
    })

    it('reprocess POSTs to /api/update_item', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson(DETAIL_PAYLOAD))
        .mockResolvedValueOnce(mockJson({ status: 'started' }))
      authorise()
      const items = useItemsStore()
      await items.open(42)
      const ok = await items.reprocess()
      expect(ok).toBe(true)
      const call = vi.mocked(globalThis.fetch).mock.calls[1]
      expect(String(call[0])).toBe('/api/update_item/42')
    })

    it('surfaces 400 detail through the toast store and action error', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson(DETAIL_PAYLOAD))
        .mockResolvedValueOnce(
          mockJson({ detail: 'bad payload' }, { status: 400 }),
        )
      authorise()
      const toast = useToastStore()
      const items = useItemsStore()
      await items.open(42)
      const ok = await items.saveIds({ kp_id: 'oops' })
      expect(ok).toBe(false)
      expect(items.actionError).toMatch(/bad payload/)
      expect(toast.current?.tone).toBe('error')
    })

    it('returns false from every action when no item is open', async () => {
      authorise()
      const items = useItemsStore()
      expect(await items.saveIds({ kp_id: 'x' })).toBe(false)
      expect(await items.rebind({ kp_id: 'x' })).toBe(false)
      expect(await items.resetFields(['kp_id'])).toBe(false)
      expect(await items.toggleIgnore()).toBe(false)
      expect(await items.reprocess()).toBe(false)
    })
  })

  describe('onSingleUpdateCompleted', () => {
    it('refreshes the open card', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson(DETAIL_PAYLOAD)) // open
        .mockResolvedValueOnce(
          mockJson({
            ...DETAIL_PAYLOAD,
            item: baseItem({ title: 'After' }),
          }),
        ) // refresh
      authorise()
      const items = useItemsStore()
      await items.open(42)
      items.onSingleUpdateCompleted()
      // refresh() is fired async; wait a microtask.
      await new Promise((resolve) => setTimeout(resolve, 0))
      expect(items.item?.title).toBe('After')
    })

    it('does nothing when no item is open', () => {
      const items = useItemsStore()
      items.onSingleUpdateCompleted()
      expect(globalThis.fetch).not.toHaveBeenCalled()
    })
  })

  describe('reprocessById (feed hover shortcut)', () => {
    it('POSTs /api/update_item/{id} and toasts success', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson({ status: 'success' }))
      authorise()
      const toast = useToastStore()
      const successSpy = vi.spyOn(toast, 'success')
      const items = useItemsStore()
      const ok = await items.reprocessById(99)
      expect(ok).toBe(true)
      const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
      expect(String(url)).toBe('/api/update_item/99')
      expect((init as RequestInit)?.method).toBe('POST')
      expect(successSpy).toHaveBeenCalled()
    })

    it('does not toast on failure', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ detail: 'busy' }, { status: 409 }),
      )
      authorise()
      const toast = useToastStore()
      const successSpy = vi.spyOn(toast, 'success')
      const items = useItemsStore()
      const ok = await items.reprocessById(99)
      expect(ok).toBe(false)
      expect(successSpy).not.toHaveBeenCalled()
    })
  })

  describe('openWithResetDialog (feed hover shortcut)', () => {
    it('sets pendingResetDialog before delegating to open()', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(DETAIL_PAYLOAD))
      authorise()
      const items = useItemsStore()
      const openSpy = vi.spyOn(items, 'open')
      await items.openWithResetDialog(42, baseItem())
      expect(items.pendingResetDialog).toBe(true)
      expect(openSpy).toHaveBeenCalledWith(42, expect.any(Object))
    })
  })
})
