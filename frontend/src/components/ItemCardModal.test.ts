// ROADMAP Stage 10.7f — ItemCardModal component tests.
// Updated after the 10.7z follow-up tab → flat-layout rewrite.
// The modal no longer uses navigation tabs; reset & edit-IDs are
// inline sub-dialogs. The ✕ in the top-right both flips
// `is_ignored` and closes the modal (legacy "swipe-to-dismiss").

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import ItemCardModal from './ItemCardModal.vue'
import { useItemsStore } from '../stores/items'
import { useSessionStore } from '../stores/session'
import { useSyncStore } from '../stores/sync'

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
    description: 'A description.',
    kp_rating: 7.5,
    imdb_rating: 8.1,
    kp_id: '111',
    imdb_id: 'tt001',
    rezka_url: 'https://rezka/x',
    original_title: 'Test Original',
    is_ignored: 0,
    ...overrides,
  }
}

const DETAIL = {
  item: baseItem(),
  releases: [
    {
      id: 1,
      item_id: 42,
      date_added: '2024-02-01 10:00:00',
      title: 'release-1',
      quality: '1080p',
      size: '4 GB',
    },
  ],
  collections: [],
}

describe('ItemCardModal.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('does not render when the items store is empty', () => {
    const wrapper = mount(ItemCardModal)
    expect(wrapper.find('[data-testid="item-modal"]').exists()).toBe(false)
  })

  it('renders header, ratings, description and releases when an item is opened', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(DETAIL))
    authorise()
    const items = useItemsStore()
    const wrapper = mount(ItemCardModal)
    await items.open(42, baseItem())
    await flushPromises()
    expect(wrapper.find('[data-testid="item-modal"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="item-modal-title"]').text()).toContain(
      'Test',
    )
    expect(wrapper.find('[data-testid="item-modal-kp"]').text()).toContain('7.5')
    expect(wrapper.find('[data-testid="item-modal-imdb"]').text()).toContain(
      '8.1',
    )
    expect(
      wrapper.find('[data-testid="item-modal-description"]').text(),
    ).toContain('A description')
    expect(
      wrapper.find('[data-testid="item-modal-releases"]').text(),
    ).toContain('release-1')
  })

  it('renders external-source links when IDs/URLs are present', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(DETAIL))
    authorise()
    const items = useItemsStore()
    const wrapper = mount(ItemCardModal)
    await items.open(42, baseItem())
    await flushPromises()
    expect(
      wrapper.find('[data-testid="item-modal-link-rutor"]').attributes('href'),
    ).toContain('rutor.info')
    expect(
      wrapper.find('[data-testid="item-modal-link-kp"]').attributes('href'),
    ).toContain('kinopoisk.ru/film/111')
    expect(
      wrapper.find('[data-testid="item-modal-link-rezka"]').attributes('href'),
    ).toContain('rezka/x')
    expect(
      wrapper.find('[data-testid="item-modal-link-imdb"]').attributes('href'),
    ).toContain('imdb.com/title/tt001')
  })

  it('hides external-source link chips that have no underlying value', async () => {
    const detail = {
      ...DETAIL,
      item: baseItem({ kp_id: null, imdb_id: null, rezka_url: null }),
    }
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(detail))
    authorise()
    const items = useItemsStore()
    const wrapper = mount(ItemCardModal)
    await items.open(42, baseItem({ kp_id: null, imdb_id: null, rezka_url: null }))
    await flushPromises()
    expect(wrapper.find('[data-testid="item-modal-link-kp"]').exists()).toBe(
      false,
    )
    expect(wrapper.find('[data-testid="item-modal-link-rezka"]').exists()).toBe(
      false,
    )
    expect(wrapper.find('[data-testid="item-modal-link-imdb"]').exists()).toBe(
      false,
    )
    // Title-based RUTOR search still works without IDs.
    expect(wrapper.find('[data-testid="item-modal-link-rutor"]').exists()).toBe(
      true,
    )
  })

  it('shows the edit-IDs panel only after the user clicks the toggle', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(DETAIL))
    authorise()
    const items = useItemsStore()
    const wrapper = mount(ItemCardModal)
    await items.open(42, baseItem())
    await flushPromises()

    expect(wrapper.find('[data-testid="item-modal-ids"]').exists()).toBe(false)
    await wrapper
      .find('[data-testid="item-modal-toggle-edit-ids"]')
      .trigger('click')
    expect(wrapper.find('[data-testid="item-modal-ids"]').exists()).toBe(true)
    expect(
      (wrapper.find('[data-testid="item-modal-input-kp"]').element as HTMLInputElement)
        .value,
    ).toBe('111')
  })

  it('saves IDs through the items store when the user clicks Save', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson(DETAIL))
      .mockResolvedValueOnce(mockJson({ status: 'success' }))
    authorise()
    const items = useItemsStore()
    const wrapper = mount(ItemCardModal)
    await items.open(42, baseItem())
    await flushPromises()

    await wrapper
      .find('[data-testid="item-modal-toggle-edit-ids"]')
      .trigger('click')
    await wrapper
      .find('[data-testid="item-modal-input-kp"]')
      .setValue('999')
    await wrapper.find('[data-testid="item-modal-save-ids"]').trigger('click')
    await flushPromises()

    const setIdsCall = vi.mocked(globalThis.fetch).mock.calls[1]
    expect(String(setIdsCall[0])).toBe('/api/set_ids/42')
    expect(setIdsCall[1]?.body).toBe(JSON.stringify({ kp_id: '999' }))
  })

  it('shows the rebind button when "Перепривязка" is checked', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(DETAIL))
    authorise()
    const items = useItemsStore()
    const wrapper = mount(ItemCardModal)
    await items.open(42, baseItem())
    await flushPromises()
    await wrapper
      .find('[data-testid="item-modal-toggle-edit-ids"]')
      .trigger('click')

    expect(wrapper.find('[data-testid="item-modal-save-ids"]').exists()).toBe(
      true,
    )
    await wrapper
      .find('[data-testid="item-modal-rebind-checkbox"]')
      .setValue(true)
    expect(wrapper.find('[data-testid="item-modal-save-ids"]').exists()).toBe(
      false,
    )
    expect(wrapper.find('[data-testid="item-modal-rebind"]').exists()).toBe(
      true,
    )
  })

  it('disables the «🔄 Обновить» button while single_update is running', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(DETAIL))
    authorise()
    const sync = useSyncStore()
    sync.statuses.single_update = 'running'
    const items = useItemsStore()
    const wrapper = mount(ItemCardModal)
    await items.open(42, baseItem())
    await flushPromises()
    const btn = wrapper.find('[data-testid="item-modal-reprocess"]').element as
      | HTMLButtonElement
      | undefined
    expect(btn?.disabled).toBe(true)
    expect(
      wrapper.find('[data-testid="item-modal-reprocess"]').text(),
    ).toContain('🔄')
  })

  it('opens the reset dialog when the header button is clicked', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(DETAIL))
    authorise()
    const items = useItemsStore()
    const wrapper = mount(ItemCardModal)
    await items.open(42, baseItem())
    await flushPromises()
    expect(
      wrapper.find('[data-testid="item-modal-reset-dialog"]').exists(),
    ).toBe(false)
    await wrapper
      .find('[data-testid="item-modal-open-reset"]')
      .trigger('click')
    expect(
      wrapper.find('[data-testid="item-modal-reset-dialog"]').exists(),
    ).toBe(true)
    expect(
      wrapper.find('[data-testid="item-modal-reset-kp_id"]').exists(),
    ).toBe(true)
  })

  it('auto-pops the reset dialog when pendingResetDialog is set before open', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(DETAIL))
    authorise()
    const items = useItemsStore()
    const wrapper = mount(ItemCardModal)
    items.pendingResetDialog = true
    await items.open(42, baseItem())
    await flushPromises()
    expect(
      wrapper.find('[data-testid="item-modal-reset-dialog"]').exists(),
    ).toBe(true)
    // Flag consumed so a subsequent open doesn't keep re-popping it.
    expect(items.pendingResetDialog).toBe(false)
  })

  it('fires reset_item with selected fields and closes the dialog', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson(DETAIL))
      .mockResolvedValueOnce(mockJson({ status: 'success' }))
      .mockResolvedValueOnce(mockJson(DETAIL))
    authorise()
    const items = useItemsStore()
    const wrapper = mount(ItemCardModal)
    await items.open(42, baseItem())
    await flushPromises()
    await wrapper
      .find('[data-testid="item-modal-open-reset"]')
      .trigger('click')
    await wrapper
      .find('[data-testid="item-modal-reset-poster_url"]')
      .setValue(true)
    await wrapper
      .find('[data-testid="item-modal-reset-fields"]')
      .trigger('click')
    await flushPromises()
    const resetCall = vi.mocked(globalThis.fetch).mock.calls[1]
    expect(String(resetCall[0])).toBe('/api/reset_item/42')
    expect(resetCall[1]?.body).toBe(
      JSON.stringify({ fields: ['poster_url'] }),
    )
    expect(
      wrapper.find('[data-testid="item-modal-reset-dialog"]').exists(),
    ).toBe(false)
  })

  it('clicking the ✕ button toggles ignore and closes the modal', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson(DETAIL))
      .mockResolvedValueOnce(mockJson({ status: 'success' }))
      .mockResolvedValueOnce(mockJson(DETAIL))
    authorise()
    const items = useItemsStore()
    const wrapper = mount(ItemCardModal)
    await items.open(42, baseItem())
    await flushPromises()
    expect(items.isOpen).toBe(true)
    await wrapper.find('[data-testid="item-modal-close"]').trigger('click')
    await flushPromises()
    expect(items.isOpen).toBe(false)
    const calls = vi
      .mocked(globalThis.fetch)
      .mock.calls.map(([u]) => String(u))
    expect(calls.some((u) => u === '/api/ignore/42')).toBe(true)
  })

  it('does not double-toggle ignore when the item is already ignored', async () => {
    const detail = { ...DETAIL, item: baseItem({ is_ignored: 1 }) }
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(detail))
    authorise()
    const items = useItemsStore()
    const wrapper = mount(ItemCardModal)
    await items.open(42, baseItem({ is_ignored: 1 }))
    await flushPromises()
    await wrapper.find('[data-testid="item-modal-close"]').trigger('click')
    const calls = vi
      .mocked(globalThis.fetch)
      .mock.calls.map(([u]) => String(u))
    // /api/ignore/42 should NOT have been called — only the initial
    // /api/item/42 fetch.
    expect(calls.some((u) => u === '/api/ignore/42')).toBe(false)
    expect(items.isOpen).toBe(false)
  })

  it('renders an action error inline', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(DETAIL))
    authorise()
    const items = useItemsStore()
    const wrapper = mount(ItemCardModal)
    await items.open(42, baseItem())
    items.actionError = 'Сохранение ID: bad payload'
    await flushPromises()
    expect(
      wrapper.find('[data-testid="item-modal-action-error"]').text(),
    ).toContain('bad payload')
  })

  it('shows the ignored badge when the item is hidden', async () => {
    const detail = { ...DETAIL, item: baseItem({ is_ignored: 1 }) }
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(detail))
    authorise()
    const items = useItemsStore()
    const wrapper = mount(ItemCardModal)
    await items.open(42, baseItem({ is_ignored: 1 }))
    await flushPromises()
    expect(wrapper.find('[data-testid="item-modal-ignored"]').exists()).toBe(
      true,
    )
    expect(
      wrapper.find('[data-testid="item-modal-toggle-ignore"]').exists(),
    ).toBe(true)
  })
})
