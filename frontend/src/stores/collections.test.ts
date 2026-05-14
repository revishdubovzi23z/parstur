// ROADMAP Stage 10.5 — collections store unit tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useCollectionsStore } from './collections'
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

describe('useCollectionsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('refresh stores the list and clears loading on success', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson([
        { id: 1, name: 'Watch later', count: 5, sort_order: 0 },
        { id: 2, name: 'Favs', count: 3, sort_order: 1 },
      ]),
    )
    authorise()
    const store = useCollectionsStore()

    await store.refresh()
    expect(store.items.map((c) => c.id)).toEqual([1, 2])
    expect(store.loading).toBe(false)
  })

  it('loadItemCollections batches into the map', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ '10': [1, 2], '20': [] }),
    )
    authorise()
    const store = useCollectionsStore()

    await store.loadItemCollections([10, 20])
    expect(store.collectionsForItem(10)).toEqual([1, 2])
    expect(store.collectionsForItem(20)).toEqual([])
    expect(store.collectionsForItem(999)).toEqual([])

    const [, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    const initOpts = init as RequestInit
    expect(initOpts.method).toBe('POST')
    expect(JSON.parse(String(initOpts.body))).toEqual({ ids: [10, 20] })
  })

  it('toggleItem(added) appends and updates the map', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'success', action: 'added' }),
    )
    authorise()
    const store = useCollectionsStore()
    store.itemCollections = { 7: [3] }

    const result = await store.toggleItem(7, 5)
    expect(result).toBe('added')
    expect(store.collectionsForItem(7)).toEqual([3, 5])
  })

  it('toggleItem(removed) drops the membership', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'success', action: 'removed' }),
    )
    authorise()
    const store = useCollectionsStore()
    store.itemCollections = { 7: [3, 5] }

    const result = await store.toggleItem(7, 3)
    expect(result).toBe('removed')
    expect(store.collectionsForItem(7)).toEqual([5])
  })

  it('createCollection calls POST and re-fetches on success', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson({ status: 'success' }))
      .mockResolvedValueOnce(
        mockJson([{ id: 99, name: 'New', count: 0, sort_order: 0 }]),
      )

    authorise()
    const store = useCollectionsStore()
    const ok = await store.createCollection('  New  ')

    expect(ok).toBe(true)
    expect(store.items.map((c) => c.id)).toEqual([99])
    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(String(url)).toBe('/api/collections')
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      name: 'New',
    })
  })

  it('createCollection rejects empty / whitespace names without network', async () => {
    authorise()
    const store = useCollectionsStore()
    expect(await store.createCollection('   ')).toBe(false)
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('renameCollection PUTs and re-fetches', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson({ status: 'success' }))
      .mockResolvedValueOnce(mockJson([]))

    authorise()
    const store = useCollectionsStore()
    const ok = await store.renameCollection(42, '  Renamed  ')

    expect(ok).toBe(true)
    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(String(url)).toBe('/api/collections/42')
    expect((init as RequestInit).method).toBe('PUT')
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      name: 'Renamed',
    })
  })

  it('deleteCollection clears selection when deleting the selected one', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson({ status: 'success' }))
      .mockResolvedValueOnce(mockJson([]))

    authorise()
    const store = useCollectionsStore()
    store.selectedId = 5

    const ok = await store.deleteCollection(5)
    expect(ok).toBe(true)
    expect(store.selectedId).toBeNull()
  })

  it('select sets the selectedId without fetching', async () => {
    authorise()
    const store = useCollectionsStore()
    store.select(7)
    expect(store.selectedId).toBe(7)
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('skips network when session is not callable', async () => {
    const session = useSessionStore()
    session.$patch({ status: 'unauthenticated' })
    const store = useCollectionsStore()

    expect(await store.refresh()).toBeUndefined()
    expect(await store.createCollection('foo')).toBe(false)
    expect(await store.renameCollection(1, 'foo')).toBe(false)
    expect(await store.deleteCollection(1)).toBe(false)
    expect(await store.toggleItem(1, 1)).toBeNull()
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  describe('saveOrder (drag-and-drop reorder)', () => {
    it('POSTs the new order and updates the local items array', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ status: 'success' }),
      )
      authorise()
      const store = useCollectionsStore()
      store.items = [
        { id: 1, name: 'A', count: 1, sort_order: 0 },
        { id: 2, name: 'B', count: 2, sort_order: 1 },
        { id: 3, name: 'C', count: 3, sort_order: 2 },
      ]
      const ok = await store.saveOrder([3, 1, 2])
      expect(ok).toBe(true)
      const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
      expect(String(url)).toBe('/api/collections/save_order')
      expect((init as RequestInit).method).toBe('POST')
      expect(JSON.parse(String((init as RequestInit).body))).toEqual({
        order: [3, 1, 2],
      })
      expect(store.items.map((c) => c.id)).toEqual([3, 1, 2])
    })

    it('rolls back via refresh() on a failing POST', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson({ detail: 'busy' }, { status: 500 }))
        .mockResolvedValueOnce(
          mockJson([
            { id: 1, name: 'A', count: 1, sort_order: 0 },
            { id: 2, name: 'B', count: 2, sort_order: 1 },
          ]),
        )
      authorise()
      const store = useCollectionsStore()
      store.items = [
        { id: 1, name: 'A', count: 1, sort_order: 0 },
        { id: 2, name: 'B', count: 2, sort_order: 1 },
      ]
      const ok = await store.saveOrder([2, 1])
      expect(ok).toBe(false)
      // Refresh re-fetched the original order from the backend.
      expect(store.items.map((c) => c.id)).toEqual([1, 2])
    })

    it('refuses partial / stale ID lists without making a request', async () => {
      authorise()
      const store = useCollectionsStore()
      store.items = [
        { id: 1, name: 'A', count: 1, sort_order: 0 },
        { id: 2, name: 'B', count: 2, sort_order: 1 },
      ]
      const ok = await store.saveOrder([1]) // missing id=2
      expect(ok).toBe(false)
      expect(globalThis.fetch).not.toHaveBeenCalled()
    })
  })
})
