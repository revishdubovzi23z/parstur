// ROADMAP Stage 10.4 — feed store unit tests.
//
// Covers URL building (the backend contract), pagination guards,
// fetch happy path, connection errors, and 401 hand-off into the
// session store.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { buildFeedUrl, useFeedStore } from './feed'
import { useSessionStore } from './session'
import { DEFAULT_FEED_FILTERS } from '../types/feed'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

describe('buildFeedUrl', () => {
  it('emits only required params when filters are default', () => {
    const url = buildFeedUrl(1, 20, { ...DEFAULT_FEED_FILTERS })
    expect(url).toBe('/api/feed?page=1&limit=20&category_id=-1')
  })

  it('includes optional filters when they deviate from defaults', () => {
    const url = buildFeedUrl(3, 40, {
      ...DEFAULT_FEED_FILTERS,
      categoryId: 5,
      collectionId: 7,
      search: ' Дюна  ',
      minYear: 2010,
      maxYear: 2024,
      minKp: 6,
      maxKp: 9,
      minImdb: 5,
      maxImdb: 8,
      hideRated: true,
      hideCollected: true,
    })
    const parsed = new URL(url, 'http://x')
    expect(parsed.pathname).toBe('/api/feed')
    expect(parsed.searchParams.get('page')).toBe('3')
    expect(parsed.searchParams.get('limit')).toBe('40')
    expect(parsed.searchParams.get('category_id')).toBe('5')
    expect(parsed.searchParams.get('collection_id')).toBe('7')
    expect(parsed.searchParams.get('search')).toBe('Дюна')
    expect(parsed.searchParams.get('min_year')).toBe('2010')
    expect(parsed.searchParams.get('max_year')).toBe('2024')
    expect(parsed.searchParams.get('min_kp')).toBe('6')
    expect(parsed.searchParams.get('max_kp')).toBe('9')
    expect(parsed.searchParams.get('min_imdb')).toBe('5')
    expect(parsed.searchParams.get('max_imdb')).toBe('8')
    expect(parsed.searchParams.get('hide_rated')).toBe('true')
    expect(parsed.searchParams.get('hide_collected')).toBe('true')
  })

  it('forwards min_date/max_date when set', () => {
    const url = buildFeedUrl(1, 20, {
      ...DEFAULT_FEED_FILTERS,
      minDate: '2024-01-01',
      maxDate: '2024-12-31',
    })
    const parsed = new URL(url, 'http://x')
    expect(parsed.searchParams.get('min_date')).toBe('2024-01-01')
    expect(parsed.searchParams.get('max_date')).toBe('2024-12-31')
  })

  it('omits rating params when they sit at the default extremes', () => {
    const url = buildFeedUrl(1, 20, {
      ...DEFAULT_FEED_FILTERS,
      minKp: 0,
      maxKp: 10,
      minImdb: 0,
      maxImdb: 10,
    })
    const parsed = new URL(url, 'http://x')
    expect(parsed.searchParams.has('min_kp')).toBe(false)
    expect(parsed.searchParams.has('max_kp')).toBe(false)
    expect(parsed.searchParams.has('min_imdb')).toBe(false)
    expect(parsed.searchParams.has('max_imdb')).toBe(false)
  })
})

describe('useFeedStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('overrides min_date with the last visit when "new only" is on', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ items: [], totalPages: 1 }),
    )
    const feed = useFeedStore()
    const session = useSessionStore()
    session.$patch({ status: 'disabled' })
    // Lazy import keeps the dependency cycle out of the module graph
    // until pinia is active.
    const { useVisitStore } = await import('./visits')
    const visits = useVisitStore()
    visits.$patch({ showNewOnly: true, lastVisit: '2024-08-01 12:00:00' })
    feed.filters.minDate = '2024-01-01'

    await feed.fetchFeed()

    const feedCall = vi
      .mocked(globalThis.fetch)
      .mock.calls.map(([u]) => String(u))
      .find((u) => u.startsWith('/api/feed'))
    expect(feedCall).toContain('min_date=2024-08-01')
    expect(feedCall).not.toContain('min_date=2024-01-01')
  })

  it('skips the request when the session cannot call the API', async () => {
    const feed = useFeedStore()
    const session = useSessionStore()
    session.$patch({ status: 'unauthenticated' })

    await feed.fetchFeed()

    expect(globalThis.fetch).not.toHaveBeenCalled()
    expect(feed.items).toEqual([])
    expect(feed.loading).toBe(false)
  })

  it('fetches and stores items + totalPages on success', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({
        items: [
          { id: 1, title: 'A', year: 2024, kp_rating: 7.5, imdb_rating: 8 },
          { id: 2, title: 'B', year: null, kp_rating: 0, imdb_rating: 0 },
        ],
        totalPages: 4,
      }),
    )
    const feed = useFeedStore()
    const session = useSessionStore()
    session.$patch({ status: 'disabled' })

    await feed.fetchFeed()

    // Stage 10.5 — a successful fetch fans out into the collections
    // store for the bookmark badge. Assert on the feed URL specifically
    // instead of the total call count.
    const calls = vi
      .mocked(globalThis.fetch)
      .mock.calls.map(([u]) => String(u))
    expect(calls).toContain('/api/feed?page=1&limit=20&category_id=-1')
    expect(feed.items.map((it) => it.id)).toEqual([1, 2])
    expect(feed.totalPages).toBe(4)
    expect(feed.connectionError).toBe(false)
    expect(feed.loading).toBe(false)
    expect(feed.hasItems).toBe(true)
  })

  it('flips connectionError on non-2xx responses', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('boom', { status: 500 }),
    )
    const feed = useFeedStore()
    const session = useSessionStore()
    session.$patch({ status: 'disabled' })

    await feed.fetchFeed()

    expect(feed.connectionError).toBe(true)
    expect(feed.items).toEqual([])
    expect(feed.loading).toBe(false)
  })

  it('routes 401 into the session store and clears feed state', async () => {
    window.sessionStorage.setItem('authToken', 'stale')
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('Unauthorized', { status: 401 }),
    )
    const feed = useFeedStore()
    const session = useSessionStore()
    session.$patch({ status: 'authenticated', token: 'stale' })

    await feed.fetchFeed()

    expect(session.status).toBe('unauthenticated')
    expect(feed.connectionError).toBe(false)
    expect(feed.items).toEqual([])
    expect(window.sessionStorage.getItem('authToken')).toBeNull()
  })

  it('paginates via nextPage / prevPage with guards', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(
        mockJson({ items: [{ id: 1, title: 'A' }], totalPages: 3 }),
      )
      .mockResolvedValueOnce(
        mockJson({ items: [{ id: 2, title: 'B' }], totalPages: 3 }),
      )
      .mockResolvedValueOnce(
        mockJson({ items: [{ id: 1, title: 'A' }], totalPages: 3 }),
      )

    const feed = useFeedStore()
    const session = useSessionStore()
    session.$patch({ status: 'disabled' })

    await feed.fetchFeed()
    expect(feed.page).toBe(1)
    expect(feed.canPrevPage).toBe(false)
    expect(feed.canNextPage).toBe(true)

    await feed.nextPage()
    expect(feed.page).toBe(2)
    // After Stage 10.5 the feed also fans out into
    // /api/batch_item_collections; pick the feed calls explicitly so
    // the assertion stays meaningful regardless of side-effect order.
    const feedCalls = vi
      .mocked(globalThis.fetch)
      .mock.calls.map(([u]) => String(u))
      .filter((u) => u.startsWith('/api/feed'))
    expect(feedCalls.some((u) => u.includes('page=2'))).toBe(true)

    await feed.prevPage()
    expect(feed.page).toBe(1)
  })

  it('setFilters resets to page 1', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(
      mockJson({ items: [], totalPages: 5 }),
    )
    const feed = useFeedStore()
    const session = useSessionStore()
    session.$patch({ status: 'disabled' })

    await feed.fetchFeed()
    feed.page = 4
    feed.setFilters({ search: 'Дюна' })

    expect(feed.page).toBe(1)
    expect(feed.filters.search).toBe('Дюна')
  })
})
