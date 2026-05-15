// Follow-up to ROADMAP 10.7z — auto-apply behaviour for FeedFilters.
//
// The legacy SPA refreshed the feed automatically when the user
// flipped a toggle or changed a rating slider; only "Применить" was
// needed for free-text search.  These tests pin that behaviour for
// the migrated component: toggle changes fire `fetchFeed` straight
// away, numeric inputs wait for the debounce window, and the
// (now hidden) submit button still works for Enter / programmatic
// submission.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import FeedFilters from './FeedFilters.vue'
import { useFeedStore } from '../../stores/feed'
import { useSessionStore } from '../../stores/session'

function installAuthedSession(): void {
  useSessionStore().$patch({ status: 'disabled' })
}

function feedCalls(): string[] {
  return vi
    .mocked(globalThis.fetch)
    .mock.calls.map(([u]) => String(u))
    .filter((u) => u.startsWith('/api/feed'))
}

describe('FeedFilters auto-apply', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    installAuthedSession()
    vi.useFakeTimers()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ items: [], totalPages: 1 }), {
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('refreshes the feed immediately when "Скрыть просмотренные" is toggled', async () => {
    const feed = useFeedStore()
    const wrapper = mount(FeedFilters)
    await flushPromises()
    const baseline = feedCalls().length

    await wrapper
      .find('[data-testid="feed-filters-hide-rated"]')
      .setValue(true)
    // Toggles fire without debounce — flush microtasks, no timer
    // advance needed.
    await flushPromises()

    const calls = feedCalls()
    expect(calls.length).toBe(baseline + 1)
    expect(calls.at(-1)).toContain('hide_rated=true')
    expect(feed.filters.hideRated).toBe(true)
  })

  it('refreshes immediately when rating sliders change', async () => {
    const wrapper = mount(FeedFilters)
    await flushPromises()
    const baseline = feedCalls().length

    await wrapper.find('[data-testid="feed-filters-min-kp"]').setValue('5')
    await wrapper.find('[data-testid="feed-filters-max-kp"]').setValue('9')
    await flushPromises()

    const kpCalls = feedCalls()
    expect(kpCalls.length).toBe(baseline + 2)
    expect(kpCalls.at(-1)).toContain('min_kp=5')
    expect(kpCalls.at(-1)).toContain('max_kp=9')

    await wrapper.find('[data-testid="feed-filters-min-imdb"]').setValue('6')
    await flushPromises()

    const calls = feedCalls()
    expect(calls.length).toBe(baseline + 3)
    expect(calls.at(-1)).toContain('min_imdb=6')
  })

  it('debounces year input edits and only fetches once after the window', async () => {
    const wrapper = mount(FeedFilters)
    await flushPromises()
    const baseline = feedCalls().length

    await wrapper.find('[data-testid="feed-filters-min-year"]').setValue('2000')
    await wrapper.find('[data-testid="feed-filters-max-year"]').setValue('2020')
    expect(feedCalls().length).toBe(baseline)

    vi.advanceTimersByTime(400)
    await flushPromises()

    const calls = feedCalls()
    expect(calls.length).toBe(baseline + 1)
    expect(calls.at(-1)).toContain('min_year=2000')
    expect(calls.at(-1)).toContain('max_year=2020')
  })

  it('submitting the form (Enter) applies pending values immediately', async () => {
    const wrapper = mount(FeedFilters)
    await flushPromises()
    const baseline = feedCalls().length

    await wrapper.find('[data-testid="feed-filters-search"]').setValue('Дюна')
    expect(feedCalls().length).toBe(baseline)

    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    const calls = feedCalls()
    expect(calls.length).toBeGreaterThan(baseline)
    expect(calls.at(-1)).toContain('search=')
  })

  it('resets filters and refetches when «Сбросить» is clicked', async () => {
    const feed = useFeedStore()
    feed.filters.minKp = 7
    feed.filters.hideRated = true
    const wrapper = mount(FeedFilters)
    await flushPromises()
    const baseline = feedCalls().length

    await wrapper
      .find('[data-testid="feed-filters-reset"]')
      .trigger('click')
    await flushPromises()

    expect(feed.filters.minKp).toBe(0)
    expect(feed.filters.hideRated).toBe(false)
    expect(feedCalls().length).toBeGreaterThan(baseline)
  })
})
