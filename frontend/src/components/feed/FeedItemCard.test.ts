// ROADMAP Stage 10.5 — FeedItemCard bookmark menu tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import FeedItemCard from './FeedItemCard.vue'
import { useCollectionsStore } from '../../stores/collections'
import { useItemsStore } from '../../stores/items'
import { useSessionStore } from '../../stores/session'
import { useVisitStore } from '../../stores/visits'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function setup(): {
  collections: ReturnType<typeof useCollectionsStore>
} {
  setActivePinia(createPinia())
  const session = useSessionStore()
  session.$patch({ status: 'disabled' })
  const collections = useCollectionsStore()
  collections.items = [
    { id: 1, name: 'Watch later', count: 1, sort_order: 0 },
    { id: 2, name: 'Favs', count: 0, sort_order: 1 },
  ]
  collections.itemCollections = { 100: [1] }
  return { collections }
}

const baseItem = {
  id: 100,
  title: 'Test movie',
  year: 2024,
  category_id: 5,
  poster_url: 'https://example.com/p.jpg',
  description: null,
  kp_rating: 7.5,
  imdb_rating: 8.2,
  kp_id: null,
  imdb_id: null,
  rezka_url: null,
  original_title: null,
}

describe('FeedItemCard.vue', () => {
  beforeEach(() => {
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders title, year, and rating badges', () => {
    setup()
    const wrapper = mount(FeedItemCard, { props: { item: baseItem } })
    expect(wrapper.find('[data-testid="feed-item-title"]').text()).toBe(
      'Test movie',
    )
    expect(wrapper.find('[data-testid="feed-item-year"]').text()).toBe('2024')
    expect(wrapper.find('[data-testid="feed-item-rating-kp"]').text()).toContain(
      'KP 7.5',
    )
    expect(
      wrapper.find('[data-testid="feed-item-rating-imdb"]').text(),
    ).toContain('IMDB 8.2')
  })

  it('shows the bookmark count and highlights when the item is in any collection', () => {
    setup()
    const wrapper = mount(FeedItemCard, { props: { item: baseItem } })
    expect(wrapper.find('[data-testid="feed-item-bookmark-count"]').text()).toBe(
      '1',
    )
    expect(
      wrapper.find('[data-testid="feed-item-bookmark-toggle"]').classes(),
    ).toContain('bg-indigo-600')
  })

  it('toggles the menu and forwards a toggleItem call', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'success', action: 'added' }),
    )
    const { collections } = setup()
    const wrapper = mount(FeedItemCard, { props: { item: baseItem } })

    expect(wrapper.find('[data-testid="feed-item-bookmark-menu"]').exists()).toBe(
      false,
    )
    await wrapper.find('[data-testid="feed-item-bookmark-toggle"]').trigger(
      'click',
    )
    expect(wrapper.find('[data-testid="feed-item-bookmark-menu"]').exists()).toBe(
      true,
    )

    await wrapper
      .find('[data-testid="feed-item-bookmark-option-2"]')
      .trigger('click')
    await flushPromises()

    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/collections/2/toggle',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(collections.collectionsForItem(100)).toEqual([1, 2])
  })

  it('renders the NEW badge when the latest release is after the last visit', () => {
    setup()
    const visits = useVisitStore()
    visits.lastVisit = '2024-01-01 00:00:00'
    const wrapper = mount(FeedItemCard, {
      props: { item: { ...baseItem, latest_release: '2024-06-01 00:00:00' } },
    })
    expect(wrapper.find('[data-testid="feed-item-new-badge"]').exists()).toBe(
      true,
    )
  })

  it('hides the NEW badge when there is no last visit or release is older', () => {
    setup()
    const visits = useVisitStore()
    visits.lastVisit = '2024-08-01 00:00:00'
    const wrapperOld = mount(FeedItemCard, {
      props: { item: { ...baseItem, latest_release: '2024-06-01 00:00:00' } },
    })
    expect(
      wrapperOld.find('[data-testid="feed-item-new-badge"]').exists(),
    ).toBe(false)
    const wrapperMissing = mount(FeedItemCard, {
      props: { item: { ...baseItem, latest_release: null } },
    })
    expect(
      wrapperMissing.find('[data-testid="feed-item-new-badge"]').exists(),
    ).toBe(false)
  })

  it('falls back to a placeholder when there is no poster', () => {
    setup()
    const wrapper = mount(FeedItemCard, {
      props: { item: { ...baseItem, poster_url: null } },
    })
    expect(
      wrapper.find('[data-testid="feed-item-poster-placeholder"]').exists(),
    ).toBe(true)
    expect(wrapper.find('[data-testid="feed-item-poster"]').exists()).toBe(false)
  })

  it('clicking the title opens the item card modal via the items store', async () => {
    setup()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ item: baseItem, releases: [], collections: [] }),
    )
    const items = useItemsStore()
    const openSpy = vi.spyOn(items, 'open')
    const wrapper = mount(FeedItemCard, { props: { item: baseItem } })
    await wrapper.find('[data-testid="feed-item-title"]').trigger('click')
    expect(openSpy).toHaveBeenCalledWith(100, baseItem)
  })

  it('hover-shortcut «🔄» fires items.reprocessById without opening the modal', async () => {
    setup()
    const items = useItemsStore()
    const spy = vi
      .spyOn(items, 'reprocessById')
      .mockResolvedValueOnce(true)
    const openSpy = vi.spyOn(items, 'open')
    const wrapper = mount(FeedItemCard, { props: { item: baseItem } })
    await wrapper
      .find('[data-testid="feed-item-hover-reprocess"]')
      .trigger('click')
    expect(spy).toHaveBeenCalledWith(100)
    expect(openSpy).not.toHaveBeenCalled()
  })

  it('hover-shortcut «🗑️» opens the modal with the reset dialog pre-popped', async () => {
    setup()
    const items = useItemsStore()
    const spy = vi
      .spyOn(items, 'openWithResetDialog')
      .mockResolvedValueOnce(undefined)
    const wrapper = mount(FeedItemCard, { props: { item: baseItem } })
    await wrapper
      .find('[data-testid="feed-item-hover-reset"]')
      .trigger('click')
    expect(spy).toHaveBeenCalledWith(100, baseItem)
  })
})
