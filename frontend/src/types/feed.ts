// ROADMAP Stage 10.4 — feed item / list types.
//
// Mirrors the columns returned by `db.get_feed` (see
// `db/items.py:get_feed`) and the response envelope from
// `routes/feed.py:get_feed` (`{items, totalPages}`).
//
// The shape is intentionally permissive: get_feed returns the full
// `items` row plus optional decorators (`releases`, `latest_release`,
// `has_new_release`, `matched_rules`). The SPA only renders the
// subset it needs today; everything else stays loosely typed so
// future stages (10.5 collections, 10.6 admin) can lift more
// fields without breaking the contract.

export interface FeedRelease {
  id: number
  item_id: number
  date_added: string | null
  magnet?: string | null
  title?: string | null
  quality?: string | null
  size?: string | null
  link?: string | null
}

export interface FeedItem {
  id: number
  title: string
  year: number | null
  category_id: number
  poster_url: string | null
  description: string | null
  title_norm?: string | null
  kp_rating: number | null
  imdb_rating: number | null
  kp_id: string | null
  imdb_id: string | null
  rezka_url: string | null
  /** kino.pub item id (numeric) once the row is bound. Set via
   * the `/api/kinopub/bind/{item_id}` endpoint or the future
   * sync_kinopub matcher. */
  kinopub_id?: number | null
  /** Cached `https://kino.pub/item/<id>` URL for the "open on
   * kino.pub" chip — saves a round-trip to render the link. */
  kinopub_url?: string | null
  /** "movie" | "serial" | "3D" | ... — used by the player to pick
   * the correct UI tab. */
  kinopub_type?: string | null
  original_title: string | null
  is_ignored?: number | null
  user_rating?: number | null
  is_watched?: number | null
  watched_at?: string | null
  latest_release?: string | null
  releases?: FeedRelease[]
  has_new_release?: boolean
  latest_season?: number | null
  latest_episode?: number | null
  matched_rules?: string[]
}

export interface FeedResponse {
  items: FeedItem[]
  totalPages: number
}

export interface FeedFilters {
  categoryId: number
  collectionId: number | null
  search: string
  minYear: number | null
  maxYear: number | null
  /** YYYY-MM-DD strings (or null when blank). */
  minDate: string | null
  maxDate: string | null
  minKp: number
  maxKp: number
  minImdb: number
  maxImdb: number
  hideRated: boolean
  hideCollected: boolean
  sortBy: 'date_desc' | 'kp_desc' | 'kp_asc' | 'imdb_desc' | 'imdb_asc'
}

export const DEFAULT_FEED_FILTERS: FeedFilters = {
  categoryId: -1,
  collectionId: null,
  search: '',
  minYear: null,
  maxYear: null,
  minDate: null,
  maxDate: null,
  minKp: 0,
  maxKp: 10,
  minImdb: 0,
  maxImdb: 10,
  hideRated: false,
  hideCollected: false,
  sortBy: 'date_desc',
}

// ROADMAP Stage 10.5 — types for the categories and collections side
// of the UI. `CategoryEntry` mirrors the response of `/api/categories`
// (`db/items.py:get_categories_with_counts`). `Collection` mirrors a
// row from `/api/collections` (`db/collections.py:get_collections`).
export interface CategoryEntry {
  id: number
  name: string
  count: number
}

export interface Collection {
  id: number
  name: string
  sort_order?: number | null
  count?: number
}

export type ItemCollectionsMap = Record<number, number[]>
