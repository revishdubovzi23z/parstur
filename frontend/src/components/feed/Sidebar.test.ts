// ROADMAP Stage 10.5 — Sidebar component tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

import Sidebar from './Sidebar.vue'
import { useCategoriesStore } from '../../stores/categories'
import { useCollectionsStore } from '../../stores/collections'
import { useFeedStore } from '../../stores/feed'
import { useSessionStore } from '../../stores/session'
import { useSyncStore } from '../../stores/sync'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function setup(): {
  categories: ReturnType<typeof useCategoriesStore>
  collections: ReturnType<typeof useCollectionsStore>
  feed: ReturnType<typeof useFeedStore>
} {
  setActivePinia(createPinia())
  const session = useSessionStore()
  session.$patch({ status: 'disabled' })
  const categories = useCategoriesStore()
  const collections = useCollectionsStore()
  const feed = useFeedStore()
  categories.items = [
    { id: -1, name: 'All', count: 100 },
    { id: 5, name: 'Movies', count: 20 },
  ]
  collections.items = [
    { id: 1, name: 'Watch later', count: 4, sort_order: 0 },
    { id: 2, name: 'Favs', count: 2, sort_order: 1 },
  ]
  return { categories, collections, feed }
}

describe('Sidebar.vue', () => {
  beforeEach(() => {
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders a compact category select and collections from the stores', () => {
    setup()
    const wrapper = mount(Sidebar)
    expect(wrapper.find('[data-testid="sidebar-all-videos-btn"]').text()).toContain(
      'Все видео',
    )
    expect(wrapper.find('[data-testid="sidebar-category-select"]').text()).toContain(
      'Movies',
    )
    expect(
      wrapper.find('[data-testid="sidebar-category-option-5"]').text(),
    ).toContain('Movies')
    expect(wrapper.find('[data-testid="sidebar-collection-1"]').text()).toContain(
      'Watch later',
    )
  })

  it('selecting a category patches feed filters and refetches', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ items: [], totalPages: 1 }),
    )
    const { feed, collections } = setup()
    collections.selectedId = 1

    const wrapper = mount(Sidebar)
    await wrapper.find('[data-testid="sidebar-category-select"]').setValue('5')
    await flushPromises()

    expect(feed.filters.categoryId).toBe(5)
    expect(collections.selectedId).toBeNull()
    expect(feed.filters.collectionId).toBeNull()
    const [url] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(String(url)).toContain('category_id=5')
  })

  it('selecting a collection sets selectedId and patches feed filters', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ items: [], totalPages: 1 }),
    )
    const { feed, collections } = setup()

    const wrapper = mount(Sidebar)
    await wrapper.find('[data-testid="sidebar-collection-2"]').trigger('click')
    await flushPromises()

    expect(collections.selectedId).toBe(2)
    expect(feed.filters.collectionId).toBe(2)
  })

  it('creating a collection posts the name and clears the input', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson({ status: 'success' }))
      .mockResolvedValueOnce(
        mockJson([{ id: 1, name: 'Watch later', count: 4, sort_order: 0 }]),
      )
    setup()

    const wrapper = mount(Sidebar)
    await wrapper
      .find('[data-testid="sidebar-collections-toggle-add"]')
      .trigger('click')
    await wrapper
      .find('[data-testid="sidebar-collections-input"]')
      .setValue('  New  ')
    await wrapper
      .find('[data-testid="sidebar-collections-add-form"]')
      .trigger('submit.prevent')
    await flushPromises()

    const calls = vi.mocked(globalThis.fetch).mock.calls.map(([u]) => String(u))
    expect(calls[0]).toBe('/api/collections')
    expect(
      JSON.parse(
        String(
          (vi.mocked(globalThis.fetch).mock.calls[0][1] as RequestInit).body,
        ),
      ),
    ).toEqual({ name: 'New' })
    // Re-fetch happens automatically.
    expect(calls[1]).toBe('/api/collections')
  })

  it('deleting a collection asks for confirmation and clears the selection', async () => {
    const confirmSpy = vi
      .spyOn(window, 'confirm')
      .mockImplementation(() => true)
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson({ status: 'success' })) // DELETE
      .mockResolvedValueOnce(mockJson([])) // refresh
      .mockResolvedValueOnce(mockJson({ items: [], totalPages: 1 })) // feed

    const { feed, collections } = setup()
    collections.selectedId = 1
    feed.setFilters({ collectionId: 1 })

    const wrapper = mount(Sidebar)
    await wrapper
      .find('[data-testid="sidebar-collection-delete-1"]')
      .trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(collections.selectedId).toBeNull()
    expect(feed.filters.collectionId).toBeNull()
  })

  it('renders a drag handle on each collection row and is draggable', async () => {
    setup()
    const wrapper = mount(Sidebar)
    const row = wrapper.find('[data-testid="sidebar-collection-row-1"]')
    expect(row.exists()).toBe(true)
    expect(row.attributes('draggable')).toBe('true')
    expect(
      wrapper.find('[data-testid="sidebar-collection-handle-1"]').exists(),
    ).toBe(true)
  })

  it('collapses and expands the collections list', async () => {
    setup()
    const wrapper = mount(Sidebar)
    expect(wrapper.find('[data-testid="sidebar-collections-list"]').exists()).toBe(
      true,
    )

    await wrapper.find('[data-testid="sidebar-collections-toggle"]').trigger('click')
    expect(wrapper.find('[data-testid="sidebar-collections-list"]').exists()).toBe(
      false,
    )
    expect(wrapper.find('[data-testid="sidebar-collection-1"]').exists()).toBe(false)

    await wrapper.find('[data-testid="sidebar-collections-toggle"]').trigger('click')
    expect(wrapper.find('[data-testid="sidebar-collections-list"]').exists()).toBe(
      true,
    )
  })

  it('persists a new order via collections.saveOrder() on drop', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'success' }),
    )
    const { collections } = setup()
    const wrapper = mount(Sidebar)

    // Drag id=1 onto id=2 — final order should be [2, 1].
    const data = new Map<string, string>()
    const dataTransfer = {
      setData: (k: string, v: string) => data.set(k, v),
      getData: (k: string) => data.get(k) ?? '',
      effectAllowed: 'move',
      dropEffect: 'move',
    } as unknown as DataTransfer
    await wrapper
      .find('[data-testid="sidebar-collection-row-1"]')
      .trigger('dragstart', { dataTransfer })
    await wrapper
      .find('[data-testid="sidebar-collection-row-2"]')
      .trigger('dragover', { dataTransfer })
    await wrapper
      .find('[data-testid="sidebar-collection-row-2"]')
      .trigger('drop', { dataTransfer })
    await flushPromises()

    const call = vi
      .mocked(globalThis.fetch)
      .mock.calls.find(
        ([u]) => String(u) === '/api/collections/save_order',
      )
    expect(call).toBeDefined()
    expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({
      order: [2, 1],
    })
    expect(collections.items.map((c) => c.id)).toEqual([2, 1])
  })

  // ── Lazy collections: empty-state CTA ───────────────────────────
  describe('empty-state HDRezka sync CTA', () => {
    it('renders the empty-state card and Sync button when no collections exist', () => {
      const pinia = setActivePinia(createPinia())
      const session = useSessionStore()
      session.$patch({ status: 'disabled' })
      const collections = useCollectionsStore()
      collections.items = [] // fresh DB
      void pinia

      const wrapper = mount(Sidebar)
      expect(
        wrapper.find('[data-testid="sidebar-collections-empty"]').exists(),
      ).toBe(true)
      const btn = wrapper.find(
        '[data-testid="sidebar-collections-empty-sync"]',
      )
      expect(btn.exists()).toBe(true)
      expect(btn.text()).toContain('Sync с HDRezka')
      expect(btn.attributes('disabled')).toBeUndefined()
    })

    it('clicking the Sync button calls startRezkaCollections + collections.refresh', async () => {
      // First call: POST /api/start_rezka_collections. Second:
      // GET /api/collections (the refresh after success).
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson({ status: 'started' }))
        .mockResolvedValueOnce(
          mockJson([
            { id: 99, name: 'New from HDRezka', count: 0, sort_order: 0 },
          ]),
        )
      const pinia = setActivePinia(createPinia())
      const session = useSessionStore()
      session.$patch({ status: 'disabled' })
      const collections = useCollectionsStore()
      collections.items = []
      void pinia

      const wrapper = mount(Sidebar)
      await wrapper
        .find('[data-testid="sidebar-collections-empty-sync"]')
        .trigger('click')
      await flushPromises()

      const calls = vi
        .mocked(globalThis.fetch)
        .mock.calls.map(([u]) => String(u))
      expect(calls).toContain('/api/start_rezka_collections')
      expect(calls).toContain('/api/collections')
    })

    it('disables the button while a sync is already in flight', () => {
      const pinia = setActivePinia(createPinia())
      const session = useSessionStore()
      session.$patch({ status: 'disabled' })
      const collections = useCollectionsStore()
      collections.items = []
      const sync = useSyncStore()
      // The store keeps a per-process_key status map. Anything
      // other than the idle states (`idle` / `success` / `error`)
      // counts as "busy"; `running` is the one the sidebar binds
      // to, so use it explicitly.
      sync.statuses.rezka_collections = 'running'
      void pinia

      const wrapper = mount(Sidebar)
      const btn = wrapper.find(
        '[data-testid="sidebar-collections-empty-sync"]',
      )
      expect(btn.attributes('disabled')).toBeDefined()
      expect(btn.text()).toContain('Синхронизация')
    })

    it('hides the empty-state card when at least one collection exists', () => {
      setup()
      const wrapper = mount(Sidebar)
      expect(
        wrapper.find('[data-testid="sidebar-collections-empty"]').exists(),
      ).toBe(false)
    })
  })

  it('renaming a collection forwards the new name', async () => {
    const promptSpy = vi
      .spyOn(window, 'prompt')
      .mockImplementation(() => 'Renamed')
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson({ status: 'success' })) // PUT
      .mockResolvedValueOnce(mockJson([])) // refresh

    setup()
    const wrapper = mount(Sidebar)
    await wrapper
      .find('[data-testid="sidebar-collection-rename-1"]')
      .trigger('click')
    await flushPromises()

    expect(promptSpy).toHaveBeenCalled()
    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(String(url)).toBe('/api/collections/1')
    expect((init as RequestInit).method).toBe('PUT')
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      name: 'Renamed',
    })
  })
})
