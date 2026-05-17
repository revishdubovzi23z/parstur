// ROADMAP Stage 10.4 — FeedView component tests.
// ROADMAP Stage 10.5 — extended to cover the fan-out refresh that also
// hydrates the categories + collections stores on mount and on
// session-state transitions.
//
// The view is mostly a render-state machine on top of `useFeedStore`,
// `useCategoriesStore`, and `useCollectionsStore`. These tests cover
// the four feed branches (loading / connection error / empty /
// populated) plus the pagination interaction and the auto-fetch.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

import FeedView from './FeedView.vue'
import { useFeedStore } from '../../stores/feed'
import { useSessionStore } from '../../stores/session'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

/**
 * Helper that routes fetch calls to per-URL handlers. The default
 * handler returns 200 with `[]` so categories / collections / batch
 * never blow up the test on incidental refresh calls — individual
 * tests override the `/api/feed` handler to assert on grid state.
 */
function installFetchRouter(
  feed: (req: { url: string; init?: RequestInit }) => Response | Promise<Response>,
): void {
  vi.mocked(globalThis.fetch).mockImplementation(
    (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : (input as URL).toString()
      if (url.startsWith('/api/feed')) {
        return Promise.resolve(feed({ url, init }))
      }
      return Promise.resolve(mockJson([]))
    },
  )
}

describe('FeedView.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the loading state while the first fetch is in flight', async () => {
    let resolveFetch: (value: Response) => void = () => {}
    const feedPromise = new Promise<Response>((resolve) => {
      resolveFetch = resolve
    })
    installFetchRouter(() => feedPromise)

    const session = useSessionStore()
    session.$patch({ status: 'disabled' })

    const wrapper = mount(FeedView)
    await flushPromises()

    expect(wrapper.find('[data-testid="feed-loading"]').exists()).toBe(true)

    resolveFetch(mockJson({ items: [], totalPages: 1 }))
    await flushPromises()

    expect(wrapper.find('[data-testid="feed-loading"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="feed-empty"]').exists()).toBe(true)
  })

  it('shows the empty state when the backend returns no items', async () => {
    installFetchRouter(() => mockJson({ items: [], totalPages: 1 }))

    const session = useSessionStore()
    session.$patch({ status: 'disabled' })

    const wrapper = mount(FeedView)
    await flushPromises()

    expect(wrapper.find('[data-testid="feed-empty"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="feed-grid"]').exists()).toBe(false)
  })

  it('renders the grid + pagination when items arrive', async () => {
    installFetchRouter(() =>
      mockJson({
        items: [
          {
            id: 1,
            title: 'Aaa',
            year: 2024,
            kp_rating: 8.1,
            poster_url: 'http://x/a.jpg',
          },
          { id: 2, title: 'Bbb', year: 2023, kp_rating: 0, poster_url: null },
        ],
        totalPages: 3,
      }),
    )

    const session = useSessionStore()
    session.$patch({ status: 'disabled' })

    const wrapper = mount(FeedView)
    await flushPromises()

    expect(wrapper.find('[data-testid="feed-grid"]').exists()).toBe(true)
    const cards = wrapper.findAll('[data-testid="feed-item-card"]')
    expect(cards).toHaveLength(2)
    expect(cards[0].attributes('data-item-id')).toBe('1')
    expect(wrapper.find('[data-testid="feed-pagination"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="feed-page-indicator"]').text()).toContain(
      '1 / 3',
    )
  })

  it('hides pagination when there is only one page', async () => {
    installFetchRouter(() =>
      mockJson({ items: [{ id: 1, title: 'Only' }], totalPages: 1 }),
    )

    const session = useSessionStore()
    session.$patch({ status: 'disabled' })

    const wrapper = mount(FeedView)
    await flushPromises()

    expect(wrapper.find('[data-testid="feed-pagination"]').exists()).toBe(false)
  })

  it('renders the connection-error banner and retries on click', async () => {
    let attempt = 0
    installFetchRouter(() => {
      attempt += 1
      if (attempt === 1) {
        return new Response('boom', { status: 500 })
      }
      return mockJson({
        items: [{ id: 9, title: 'After retry' }],
        totalPages: 1,
      })
    })

    const session = useSessionStore()
    session.$patch({ status: 'disabled' })

    const wrapper = mount(FeedView)
    await flushPromises()

    expect(wrapper.find('[data-testid="feed-connection-error"]').exists()).toBe(
      true,
    )
    await wrapper.find('[data-testid="feed-retry"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="feed-connection-error"]').exists()).toBe(
      false,
    )
    expect(wrapper.findAll('[data-testid="feed-item-card"]')).toHaveLength(1)
  })

  it('advances to the next page when the Next button is clicked', async () => {
    const feedCalls: string[] = []
    installFetchRouter(({ url }) => {
      feedCalls.push(url)
      if (url.includes('page=2')) {
        return mockJson({
          items: [{ id: 2, title: 'B' }],
          totalPages: 2,
        })
      }
      return mockJson({ items: [{ id: 1, title: 'A' }], totalPages: 2 })
    })

    const session = useSessionStore()
    session.$patch({ status: 'disabled' })

    const wrapper = mount(FeedView)
    await flushPromises()
    expect(wrapper.find('[data-testid="feed-page-indicator"]').text()).toContain(
      '1 / 2',
    )

    await wrapper.find('[data-testid="feed-next"]').trigger('click')
    await flushPromises()

    const feed = useFeedStore()
    expect(feed.page).toBe(2)
    expect(feedCalls.some((u) => u.includes('page=2'))).toBe(true)
  })

  it('does not fetch on mount when session is unauthenticated', async () => {
    const session = useSessionStore()
    session.$patch({ status: 'unauthenticated' })

    mount(FeedView)
    await flushPromises()

    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('auto-fetches when the session flips into a callable state', async () => {
    installFetchRouter(() =>
      mockJson({ items: [{ id: 5, title: 'Late' }], totalPages: 1 }),
    )

    const session = useSessionStore()
    session.$patch({ status: 'unauthenticated' })

    const wrapper = mount(FeedView)
    await flushPromises()
    expect(globalThis.fetch).not.toHaveBeenCalled()

    session.$patch({ status: 'authenticated', token: 'tok' })
    await flushPromises()

    const calls = vi.mocked(globalThis.fetch).mock.calls.map(([u]) => String(u))
    expect(calls.some((u) => u.startsWith('/api/feed'))).toBe(true)
    expect(wrapper.findAll('[data-testid="feed-item-card"]')).toHaveLength(1)
  })

  it('applies filters via the form and resets to page 1', async () => {
    const feedCalls: string[] = []
    installFetchRouter(({ url }) => {
      feedCalls.push(url)
      return mockJson({ items: [{ id: 1, title: 'A' }], totalPages: 3 })
    })

    const session = useSessionStore()
    session.$patch({ status: 'disabled' })

    const wrapper = mount(FeedView)
    await flushPromises()

    const feed = useFeedStore()
    feed.page = 2 // pretend we'd scrolled forward

    await wrapper.find('[data-testid="feed-filters-search"]').setValue('Дюна')
    await wrapper.find('[data-testid="feed-filters"]').trigger('submit.prevent')
    await flushPromises()

    expect(feed.page).toBe(1)
    expect(feed.filters.search).toBe('Дюна')
    expect(
      feedCalls.some((u) => u.includes('search=%D0%94%D1%8E%D0%BD%D0%B0')),
    ).toBe(true)
  })
})
