// ROADMAP Stage 10.7a — persist feed filters across reloads.
//
// Legacy `index.html` (~lines 1294-1305 and `loadFilters` ~1522) wires
// each filter field to its own sessionStorage key via watchers. We
// replicate that contract here so users can refresh `/beta` without
// losing their search/year/date/etc.
//
// Why sessionStorage (not localStorage): the legacy chose
// sessionStorage so the filter resets when the user closes the tab,
// which matches the "tab is a session" mental model people already
// have with `f_*` keys. Same key namespace so a user on `/` ↔ `/beta`
// during the coexistence window sees consistent state.

import { watch } from 'vue'

import { useFeedStore } from '../stores/feed'
import type { FeedFilters } from '../types/feed'
import { DEFAULT_FEED_FILTERS } from '../types/feed'

/** sessionStorage key namespace — kept identical to the legacy code. */
export const FILTER_STORAGE_KEYS = {
  categoryId: 'f_cat',
  collectionId: 'f_coll',
  search: 'f_search',
  minYear: 'f_minY',
  maxYear: 'f_maxY',
  minDate: 'f_dateF',
  maxDate: 'f_dateT',
  minKp: 'f_minKp',
  maxKp: 'f_maxKp',
  minImdb: 'f_minImdb',
  maxImdb: 'f_maxImdb',
  hideRated: 'f_hideRated',
  hideCollected: 'f_hideCollected',
  sortBy: 'f_sortBy',
  page: 'f_page',
} as const

function read(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key)
  } catch {
    return null
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null || value === '') {
      window.sessionStorage.removeItem(key)
    } else {
      window.sessionStorage.setItem(key, value)
    }
  } catch {
    /* private mode etc. — silently ignore */
  }
}

function parseNumberOrNull(raw: string | null): number | null {
  if (raw === null || raw === '' || raw === 'null') return null
  const n = Number(raw)
  return Number.isFinite(n) ? n : null
}

function parseNumberOrDefault(raw: string | null, fallback: number): number {
  const n = parseNumberOrNull(raw)
  return n === null ? fallback : n
}

function parseDateOrNull(raw: string | null): string | null {
  if (!raw || raw === 'null') return null
  return raw
}

function parseBool(raw: string | null, fallback: boolean): boolean {
  if (raw === 'true') return true
  if (raw === 'false') return false
  return fallback
}

/**
 * Read the persisted filter state from sessionStorage, falling back
 * to {@link DEFAULT_FEED_FILTERS} for anything missing.
 */
export function loadPersistedFilters(): FeedFilters {
  return {
    categoryId: parseNumberOrDefault(
      read(FILTER_STORAGE_KEYS.categoryId),
      DEFAULT_FEED_FILTERS.categoryId,
    ),
    collectionId: parseNumberOrNull(read(FILTER_STORAGE_KEYS.collectionId)),
    search: read(FILTER_STORAGE_KEYS.search) ?? DEFAULT_FEED_FILTERS.search,
    minYear: parseNumberOrNull(read(FILTER_STORAGE_KEYS.minYear)),
    maxYear: parseNumberOrNull(read(FILTER_STORAGE_KEYS.maxYear)),
    minDate: parseDateOrNull(read(FILTER_STORAGE_KEYS.minDate)),
    maxDate: parseDateOrNull(read(FILTER_STORAGE_KEYS.maxDate)),
    minKp: parseNumberOrDefault(
      read(FILTER_STORAGE_KEYS.minKp),
      DEFAULT_FEED_FILTERS.minKp,
    ),
    maxKp: parseNumberOrDefault(
      read(FILTER_STORAGE_KEYS.maxKp),
      DEFAULT_FEED_FILTERS.maxKp,
    ),
    minImdb: parseNumberOrDefault(
      read(FILTER_STORAGE_KEYS.minImdb),
      DEFAULT_FEED_FILTERS.minImdb,
    ),
    maxImdb: parseNumberOrDefault(
      read(FILTER_STORAGE_KEYS.maxImdb),
      DEFAULT_FEED_FILTERS.maxImdb,
    ),
    hideRated: parseBool(
      read(FILTER_STORAGE_KEYS.hideRated),
      DEFAULT_FEED_FILTERS.hideRated,
    ),
    hideCollected: parseBool(
      read(FILTER_STORAGE_KEYS.hideCollected),
      DEFAULT_FEED_FILTERS.hideCollected,
    ),
    sortBy: (read(FILTER_STORAGE_KEYS.sortBy) as FeedFilters['sortBy']) ?? DEFAULT_FEED_FILTERS.sortBy,
  }
}

/**
 * Hydrate the feed store from sessionStorage and start watchers that
 * write back any subsequent mutation. Returns a function that detaches
 * all watchers (mostly useful for tests).
 */
export function attachFilterPersistence(): () => void {
  const feed = useFeedStore()
  feed.filters = loadPersistedFilters()
  const persistedPage = parseNumberOrDefault(
    read(FILTER_STORAGE_KEYS.page),
    1,
  )
  feed.page = Math.max(1, persistedPage)

  const stops: Array<() => void> = []
  const watchField = <T>(
    getter: () => T,
    serialize: (v: T) => string | null,
    key: string,
  ): void => {
    stops.push(
      watch(getter, (val) => {
        write(key, serialize(val))
      }),
    )
  }

  watchField(
    () => feed.filters.categoryId,
    (v) => String(v),
    FILTER_STORAGE_KEYS.categoryId,
  )
  watchField(
    () => feed.filters.collectionId,
    (v) => (v == null ? null : String(v)),
    FILTER_STORAGE_KEYS.collectionId,
  )
  watchField(
    () => feed.filters.search,
    (v) => v,
    FILTER_STORAGE_KEYS.search,
  )
  watchField(
    () => feed.filters.minYear,
    (v) => (v == null ? null : String(v)),
    FILTER_STORAGE_KEYS.minYear,
  )
  watchField(
    () => feed.filters.maxYear,
    (v) => (v == null ? null : String(v)),
    FILTER_STORAGE_KEYS.maxYear,
  )
  watchField(
    () => feed.filters.minDate,
    (v) => v,
    FILTER_STORAGE_KEYS.minDate,
  )
  watchField(
    () => feed.filters.maxDate,
    (v) => v,
    FILTER_STORAGE_KEYS.maxDate,
  )
  watchField(
    () => feed.filters.minKp,
    (v) => String(v),
    FILTER_STORAGE_KEYS.minKp,
  )
  watchField(
    () => feed.filters.maxKp,
    (v) => String(v),
    FILTER_STORAGE_KEYS.maxKp,
  )
  watchField(
    () => feed.filters.minImdb,
    (v) => String(v),
    FILTER_STORAGE_KEYS.minImdb,
  )
  watchField(
    () => feed.filters.maxImdb,
    (v) => String(v),
    FILTER_STORAGE_KEYS.maxImdb,
  )
  watchField(
    () => feed.filters.hideRated,
    (v) => String(v),
    FILTER_STORAGE_KEYS.hideRated,
  )
  watchField(
    () => feed.filters.hideCollected,
    (v) => String(v),
    FILTER_STORAGE_KEYS.hideCollected,
  )
  watchField(
    () => feed.filters.sortBy,
    (v) => v,
    FILTER_STORAGE_KEYS.sortBy,
  )
  watchField(
    () => feed.page,
    (v) => String(v),
    FILTER_STORAGE_KEYS.page,
  )

  return () => stops.forEach((stop) => stop())
}
