// ROADMAP Stage 10.7g — item player store (trailer + Rezka HLS stream).
//
// Pinia replacement for the legacy data fields that lived on the root
// Vue instance in `index.html`:
//   - trailer modal (8.7): `showTrailerModal`, `trailerItem`,
//     `trailerCandidates`, `trailerCandidateIndex`, `trailerLoading`,
//     `trailerError`,
//   - stream modal (8.6 / 8.12): `showStreamModal`, `streamItem`,
//     `streamInfo`, `streamTranslator`, `streamSeason`,
//     `streamEpisode`, `streamVideos`, `streamSubtitles`,
//     `streamCurrentUrl`, `streamCurrentQuality`, plus the
//     `onlineSources` / `onlinePageUrl` block.
//
// The component layer (`PlayerModal.vue`) is a thin presentational
// shell around this state — the actions wrap the existing backend
// endpoints in `routes/streams.py`:
//   - `GET /api/trailer/{id}` — ranked YouTube candidates,
//   - `GET /api/online_sources/{id}` — kinobox-style iframe players,
//   - `GET /api/stream_info/{id}` — Rezka translators / series tree,
//   - `GET /api/stream_url/{id}` — resolved playable URL + is_hls,
//   - `GET /api/stream_m3u/{id}` — proxy-signed M3U for VLC etc.,
//   - `POST /api/mark_season_seen/{id}` — clears the "новый сезон"
//     badge on the feed card.
//
// The store does NOT touch hls.js itself — that's wired up inside the
// `PlayerModal.vue` component once the resolved stream URL is in
// state. Keeping the side-effecty player attach out of the store
// makes the store unit-testable without a happy-dom <video> element.

import { defineStore } from 'pinia'

import { apiFetch, UnauthorizedError } from '../api/client'
import type {
  KinopubStreamInfo,
  KinopubStreamSeason,
  KinopubStreamVideo,
} from './kinopub'
import { useSessionStore } from './session'
import { useToastStore } from './toast'

/** A single TMDB-ranked YouTube candidate returned by
 *  `/api/trailer/{id}`. The frontend cycles through them when
 *  `onError` fires from the embed (101 / 150 / 153 = embed disabled).
 */
export interface TrailerCandidate {
  youtube_key: string
  name: string
  type: string
  official: boolean
}

/** A single alternative online-cinema iframe player surfaced by
 *  `/api/online_sources/{id}`. Different sources expose different
 *  translation lists; the legacy UI only showed the iframe URL
 *  unmodified, so we mirror that contract here. */
export interface OnlineSource {
  type: string
  iframeUrl: string
  translations?: unknown[]
}

/** Per-translator series tree. Shape mirrors `routes/streams.py:
 *  get_stream_info`. `seasons` is `{season_id: display_name}`,
 *  `episodes` is `{season_id: {episode_id: display_name}}`. */
export interface SeriesTranslatorInfo {
  name: string
  premium: boolean
  seasons: Record<string, string>
  episodes: Record<string, Record<string, string>>
}

/** Response shape for `/api/stream_info/{id}`. */
export interface StreamInfo {
  type: 'movie' | 'series'
  name: string
  translators: Record<string, { name: string; premium?: boolean }>
  series_info?: Record<string, SeriesTranslatorInfo>
  error?: string
}

/** Response shape for `/api/stream_url/{id}`. */
export interface StreamUrlResponse {
  url: string
  quality: string
  title: string
  is_hls: boolean
  error?: string
}

/** Subtitle entry returned by `/api/stream/{id}`. The link goes
 *  through `/api/subtitle_proxy?url=...` so the browser can fetch it
 *  as VTT cross-origin. */
export interface Subtitle {
  title: string
  link: string
}

/** Source identifier for the active playback path. We currently only
 *  resolve playable streams from Rezka; the kinobox-style entries in
 *  `sources` open their iframe in a new tab. The "kind" is still
 *  tracked separately so PlayerModal can show the correct controls
 *  (translator / season / episode pickers only apply to rezka). */
export type StreamSource = 'rezka' | 'kinopub' | string

interface PlayerStoreState {
  /** id of the item the player is bound to (or null when closed). */
  itemId: number | null
  /** Optional display title for the modal header. */
  itemTitle: string | null
  /** Which surface is currently open. `null` keeps the modal hidden. */
  mode: 'trailer' | 'stream' | null

  // ── Trailer surface ────────────────────────────────────────────
  trailerCandidates: TrailerCandidate[]
  trailerIndex: number
  trailerLoading: boolean
  trailerError: string | null

  // ── Online-cinema sources (alternative iframe players) ────────
  sources: OnlineSource[]
  sourcesPageUrl: string | null
  sourcesLoading: boolean
  sourcesError: string | null

  // ── Rezka stream_info (translators + series tree) ─────────────
  info: StreamInfo | null
  infoLoading: boolean
  infoError: string | null

  // ── Current Rezka selection ───────────────────────────────────
  source: StreamSource | null
  translatorId: string | null
  season: string | null
  episode: string | null

  // ── Resolved playable stream (stream_url result) ──────────────
  streamUrl: string | null
  streamQuality: string | null
  streamIsHls: boolean
  streamLoading: boolean
  streamError: string | null
  subtitles: Record<string, Subtitle>
  /** Available qualities for the current Rezka selection. */
  rezkaQualities: string[]
  /** Active tab in the stream surface. */
  activeTab: 'kinohub' | 'rezka' | 'kinopub'
  streamConfirmed: boolean

  // ── kino.pub branch (PR 5) ───────────────────────────────────
  // Populated lazily by `openKinopubStream()`. We don't reuse the
  // rezka `info`/`translatorId`/`season`/`episode` fields because
  // the shapes differ enough that mixing them would force every
  // consumer to type-guard on `source`.
  kinopubInfo: KinopubStreamInfo | null
  /** 0-based index into `kinopubInfo.videos`. Null for serials. */
  kinopubVideoIdx: number | null
  /** Season `number` (1-based) for serials. */
  kinopubSeasonNumber: number | null
  /** Episode `number` (1-based) for serials. */
  kinopubEpisodeNumber: number | null
  /** 0-based index into the active video's `files[]` array. */
  kinopubFileIdx: number | null
  /** Lowercase language code (empty = no subtitle track active). */
  kinopubSubtitleLang: string
  kinopubLoading: boolean
  kinopubError: string | null
  // Master switches from settings.py
  rezkaEnabled: boolean
  kinohubEnabled: boolean
  kinopubEnabled: boolean
}

function emptyState(): PlayerStoreState {
  return {
    itemId: null,
    itemTitle: null,
    mode: null,
    trailerCandidates: [],
    trailerIndex: 0,
    trailerLoading: false,
    trailerError: null,
    sources: [],
    sourcesPageUrl: null,
    sourcesLoading: false,
    sourcesError: null,
    info: null,
    infoLoading: false,
    infoError: null,
    source: null,
    translatorId: null,
    season: null,
    episode: null,
    streamUrl: null,
    streamQuality: null,
    streamIsHls: false,
    streamLoading: false,
    streamError: null,
    subtitles: {},
    kinopubInfo: null,
    kinopubVideoIdx: null,
    kinopubSeasonNumber: null,
    kinopubEpisodeNumber: null,
    kinopubFileIdx: null,
    kinopubSubtitleLang: '',
    kinopubLoading: false,
    kinopubError: null,
    rezkaQualities: [],
    activeTab: 'rezka',
    streamConfirmed: false,
    rezkaEnabled: true,
    kinohubEnabled: true,
    kinopubEnabled: true,
  }
}

/** PR 5 — Walks `kinopubInfo` for the video matching the current
 *  selection. Returns null when the picker hasn't been seeded yet
 *  (e.g. while `loadKinopubInfo()` is in flight). Pure function so
 *  PlayerModal-level computed getters can call it directly. */
function _findKinopubVideo(
  info: KinopubStreamInfo,
  opts: {
    videoIdx: number | null
    season: number | null
    episode: number | null
  },
): KinopubStreamVideo | null {
  if (opts.videoIdx !== null && Array.isArray(info.videos) && info.videos[opts.videoIdx]) {
    return info.videos[opts.videoIdx]
  }
  if (opts.season !== null && Array.isArray(info.seasons)) {
    const s = info.seasons.find((x: KinopubStreamSeason) => x.number === opts.season)
    if (s && Array.isArray(s.episodes)) {
      if (opts.episode !== null) {
        return s.episodes.find((e) => e.number === opts.episode) ?? null
      }
      return s.episodes[0] ?? null
    }
  }
  return null
}

/** Quality-rank table mirroring the backend's `_KINOPUB_QUALITY_RANK`
 *  so the SPA picks the same "best available" entry the m3u endpoint
 *  would. */
const _KINOPUB_QUALITY_RANK: Record<string, number> = {
  '4k': 8,
  '2160p': 7,
  '2k': 6,
  '1440p': 5,
  '1080p': 4,
  '720p': 3,
  '480p': 2,
  '360p': 1,
}

function _bestKinopubFileIdx(video: KinopubStreamVideo): number | null {
  if (!Array.isArray(video.files) || video.files.length === 0) return null
  let bestIdx = 0
  let bestRank = -1
  for (let i = 0; i < video.files.length; i++) {
    const q = (video.files[i]?.quality ?? '').toLowerCase()
    const rank = _KINOPUB_QUALITY_RANK[q] ?? 0
    if (rank > bestRank) {
      bestIdx = i
      bestRank = rank
    }
  }
  return bestIdx
}

/** Build the `/api/stream_m3u/{id}?...` URL for VLC / external
 *  players. Returns null when the caller hasn't picked a quality yet
 *  (no point downloading an empty M3U). */
function buildM3uUrl(
  itemId: number,
  quality: string,
  translatorId: string | null,
  season: string | null,
  episode: string | null,
): string {
  const params = new URLSearchParams()
  params.set('quality', quality)
  if (translatorId) params.set('translator', translatorId)
  if (season) params.set('season', season)
  if (episode) params.set('episode', episode)
  return `/api/stream_m3u/${itemId}?${params.toString()}`
}

export const useItemPlayerStore = defineStore('itemPlayer', {
  state: (): PlayerStoreState => emptyState(),

  getters: {
    /** True while either trailer or stream surface is mounted. */
    isOpen(state): boolean {
      return state.mode !== null
    },
    isTrailerOpen(state): boolean {
      return state.mode === 'trailer'
    },
    isStreamOpen(state): boolean {
      return state.mode === 'stream'
    },
    /** Current YouTube key, or null when the candidate list is
     *  empty / the modal is closed. */
    currentTrailerKey(state): string | null {
      return state.trailerCandidates[state.trailerIndex]?.youtube_key ?? null
    },
    /** True when stream_info says the active content is a TV series. */
    isSeries(state): boolean {
      return state.info?.type === 'series'
    },
    /** Per-translator season map (or null when not applicable). */
    seasonsMap(state): Record<string, string> | null {
      if (!state.info?.series_info || !state.translatorId) return null
      return state.info.series_info[state.translatorId]?.seasons ?? null
    },
    /** Per-season episode map (or null when not applicable). */
    episodesMap(state): Record<string, string> | null {
      if (!state.info?.series_info || !state.translatorId || !state.season) {
        return null
      }
      const tr = state.info.series_info[state.translatorId]
      return tr?.episodes?.[state.season] ?? null
    },
    /** Computed M3U download URL for VLC etc., or null when there's
     *  nothing to download yet. */
    streamM3uUrl(state): string | null {
      if (state.itemId === null || !state.streamQuality) return null
      if (state.source === 'kinopub') {
        const params = new URLSearchParams()
        if (state.kinopubSeasonNumber !== null) {
          params.set('season', String(state.kinopubSeasonNumber))
        }
        if (state.kinopubEpisodeNumber !== null) {
          params.set('episode', String(state.kinopubEpisodeNumber))
        }
        if (state.streamQuality) params.set('quality', state.streamQuality)
        const qs = params.toString()
        return `/api/kinopub/m3u/${state.itemId}${qs ? `?${qs}` : ''}`
      }
      return buildM3uUrl(
        state.itemId,
        state.streamQuality,
        state.translatorId,
        state.season,
        state.episode,
      )
    },

    /** PR 5 — kino.pub helpers. The PlayerModal reads these to render
     *  the picker; falling out of state when no kinopub stream is
     *  loaded keeps the rezka surface unaffected. */
    isKinopubSeries(state): boolean {
      const t = state.kinopubInfo?.type
      return t === 'serial' || t === 'multi'
    },
    kinopubVideo(state): KinopubStreamVideo | null {
      if (!state.kinopubInfo) return null
      return _findKinopubVideo(state.kinopubInfo, {
        videoIdx: state.kinopubVideoIdx,
        season: state.kinopubSeasonNumber,
        episode: state.kinopubEpisodeNumber,
      })
    },
    /** All seasons in the kino.pub payload, with episode counts. */
    kinopubSeasons(state): { number: number; episodeCount: number }[] {
      const seasons = state.kinopubInfo?.seasons ?? []
      return seasons
        .filter((s): s is KinopubStreamSeason => typeof s?.number === 'number')
        .map((s) => ({
          number: s.number as number,
          episodeCount: Array.isArray(s.episodes) ? s.episodes.length : 0,
        }))
    },
  },

  actions: {
    /** Open the trailer surface for the given item. Resets any
     *  previous stream state so the modal renders cleanly. */
    openTrailer(itemId: number, title?: string | null): void {
      Object.assign(this, emptyState())
      this.itemId = itemId
      this.itemTitle = title ?? null
      this.mode = 'trailer'
      void this.loadTrailer(itemId)
    },

    /** Open the stream surface for the given item. Initial source is
     *  'rezka'; the caller can switch via `selectSource()`. */
    async openStream(itemId: number, title?: string | null): Promise<void> {
      Object.assign(this, emptyState())
      this.itemId = itemId
      this.itemTitle = title ?? null
      this.mode = 'stream'
      
      const dbRes = await apiFetch(`/api/item/${itemId}`)
      const dbItem = await dbRes.json()
      
      if (dbItem.config) {
        this.rezkaEnabled = dbItem.config.rezka_enabled !== false
        this.kinohubEnabled = dbItem.config.kinohub_enabled !== false
        this.kinopubEnabled = dbItem.config.kinopub_enabled !== false
      }

      // Default source logic based on availability and master switches
      if (this.rezkaEnabled && (dbItem.item?.rezka_url || !dbItem.item?.kinopub_id)) {
        this.activeTab = 'rezka'
        this.source = 'rezka'
        void this.loadInfo(itemId, 'rezka')
      } else if (this.kinopubEnabled && dbItem.item?.kinopub_id) {
        this.activeTab = 'kinopub'
        this.source = 'kinopub'
        void this.loadKinopubInfo(itemId)
      } else if (this.kinohubEnabled) {
        this.activeTab = 'kinohub'
        this.source = null
      } else if (this.rezkaEnabled) {
        // Fallback to Rezka even if no URL (it might resolve via search)
        this.activeTab = 'rezka'
        this.source = 'rezka'
        void this.loadInfo(itemId, 'rezka')
      }

      void this.loadSources(itemId)
    },

    /** Close the modal and drop all state. */
    close(): void {
      Object.assign(this, emptyState())
    },

    /** `GET /api/trailer/{id}` → populates `trailerCandidates`.
     *  Mirrors the legacy fallback: backends that pre-date 8.7
     *  returned a flat `{youtube_key, name, type, official}` so we
     *  handle both shapes. */
    async loadTrailer(itemId: number): Promise<void> {
      const session = useSessionStore()
      this.trailerError = null
      this.trailerCandidates = []
      this.trailerIndex = 0
      if (!session.canCallApi) return
      this.trailerLoading = true
      try {
        const res = await apiFetch(`/api/trailer/${itemId}`)
        const data = (await res.json().catch(() => ({}))) as {
          candidates?: TrailerCandidate[]
          youtube_key?: string
          name?: string
          type?: string
          official?: boolean
          error?: string
        }
        if (!res.ok) {
          this.trailerError = data.error ?? `HTTP ${res.status}`
          return
        }
        if (Array.isArray(data.candidates) && data.candidates.length > 0) {
          this.trailerCandidates = data.candidates
        } else if (data.youtube_key) {
          this.trailerCandidates = [
            {
              youtube_key: data.youtube_key,
              name: data.name ?? '',
              type: data.type ?? '',
              official: Boolean(data.official),
            },
          ]
        } else {
          this.trailerError = 'Трейлер не найден'
        }
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
          this.trailerError = 'Требуется вход'
          return
        }
        this.trailerError = err instanceof Error ? err.message : String(err)
      } finally {
        this.trailerLoading = false
      }
    },

    /** Cycle to the next TMDB-ranked trailer candidate (used when
     *  the embed reports onError 101/150/153 — owner disabled
     *  embedding). No-op when we're out of candidates. */
    cycleTrailerCandidate(): void {
      if (this.trailerCandidates.length < 2) return
      this.trailerIndex =
        (this.trailerIndex + 1) % this.trailerCandidates.length
    },

    /** `GET /api/online_sources/{id}` → populates `sources` and
     *  `sourcesPageUrl`. Failures are surfaced via
     *  `sourcesError`; the modal can still render the Rezka side. */
    async loadSources(itemId: number): Promise<void> {
      const session = useSessionStore()
      this.sourcesError = null
      this.sources = []
      this.sourcesPageUrl = null
      if (!session.canCallApi) return
      this.sourcesLoading = true
      try {
        const res = await apiFetch(`/api/online_sources/${itemId}`)
        const data = (await res.json().catch(() => ({}))) as {
          sources?: OnlineSource[]
          pageUrl?: string
          error?: string
        }
        if (!res.ok) {
          this.sourcesError = data.error ?? `HTTP ${res.status}`
          return
        }
        this.sources = Array.isArray(data.sources) ? data.sources : []
        this.sourcesPageUrl = data.pageUrl ?? null
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
          this.sourcesError = 'Требуется вход'
          return
        }
        this.sourcesError = err instanceof Error ? err.message : String(err)
      } finally {
        this.sourcesLoading = false
      }
    },

    /** `GET /api/stream_info/{id}?source=…&translator_id=…` →
     *  populates `info`, then auto-seeds the translator / season /
     *  episode selection from the first available value (so the
     *  modal can render selects without an extra round trip).
     *
     *  The `source` and `translator` query params are passed for
     *  future backends that key stream_info off the active player;
     *  the current backend ignores them, which is fine. */
    async loadInfo(
      itemId: number,
      source: StreamSource,
      translator?: string | null,
    ): Promise<void> {
      const session = useSessionStore()
      this.infoError = null
      this.info = null
      if (!session.canCallApi) return
      this.infoLoading = true
      try {
        const params = new URLSearchParams()
        if (source) params.set('source', source)
        if (translator) params.set('translator_id', translator)
        const qs = params.toString()
        const path = `/api/stream_info/${itemId}${qs ? `?${qs}` : ''}`
        const res = await apiFetch(path)
        const data = (await res.json().catch(() => ({}))) as StreamInfo & {
          error?: string
        }
        if (!res.ok || data.error) {
          this.infoError = data.error ?? `HTTP ${res.status}`
          return
        }
        this.info = data
        // Seed the selection so the modal renders something useful
        // on first open. Translator: the one the caller asked for
        // (if it exists), otherwise the first declared.
        const translators = data.translators ?? {}
        const declaredKeys = Object.keys(translators)
        if (translator && translator in translators) {
          this.translatorId = translator
        } else if (declaredKeys.length > 0) {
          this.translatorId = declaredKeys[0] ?? null
        } else {
          this.translatorId = null
        }
        if (data.type === 'series' && data.series_info && this.translatorId) {
          const si = data.series_info[this.translatorId]
          const firstSeason = Object.keys(si?.seasons ?? {})[0] ?? null
          this.season = firstSeason
          const firstEp = firstSeason
            ? Object.keys(si?.episodes?.[firstSeason] ?? {})[0] ?? null
            : null
          this.episode = firstEp
        } else {
          this.season = null
          this.episode = null
        }
        // Auto-load the first stream for Rezka
        if (this.source === 'rezka' && this.translatorId) {
          void this.loadStream(
            itemId,
            'rezka',
            this.translatorId,
            this.season,
            this.episode,
          )
        }
      } catch (err) {
        console.error('[Player] loadInfo failed:', err)
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
          this.infoError = 'Требуется вход'
          return
        }
        this.infoError = err instanceof Error ? err.message : String(err)
      } finally {
        this.infoLoading = false
      }
    },

    /** `GET /api/stream_url/{id}?...` → resolve a playable URL for
     *  the current selection. Also fetches the subtitle list via
     *  `/api/stream/{id}` since `stream_url` itself doesn't return
     *  it; the second call is best-effort (subtitles are nice-to-
     *  have, the player still works without them). */
    async loadStream(
      itemId: number,
      source: StreamSource,
      translator: string | null,
      season?: string | null,
      episode?: string | null,
    ): Promise<void> {
      const session = useSessionStore()
      this.streamError = null
      this.streamUrl = null
      this.streamQuality = null
      this.streamIsHls = false
      this.subtitles = {}
      if (!session.canCallApi) return
      // Remember the params we resolved with so `streamM3uUrl` and
      // any downstream quality switch can rebuild URLs without the
      // caller re-passing them.
      this.itemId = itemId
      this.source = source
      this.translatorId = translator
      this.season = season ?? null
      this.episode = episode ?? null
      this.streamLoading = true
      
      // Load all qualities and subtitles in parallel with resolving the URL
      void this._loadRezkaStreamDetails(itemId, translator, season, episode)

      try {
        const params = new URLSearchParams()
        if (translator) params.set('translator', translator)
        if (season) params.set('season', season)
        if (episode) params.set('episode', episode)
        // `source` is forwarded for future backends; current code
        // ignores it but the legacy URL pattern preserves the
        // contract.
        if (source && source !== 'rezka') params.set('source', source)
        const qs = params.toString()
        const path = `/api/stream_url/${itemId}${qs ? `?${qs}` : ''}`
        const res = await apiFetch(path)
        const data = (await res.json().catch(() => ({}))) as StreamUrlResponse & {
          error?: string
        }
        
        // Safeguard: don't overwrite if the user switched sources while we were fetching
        if (this.source !== source) return

        if (!res.ok || data.error) {
          this.streamError = data.error ?? `HTTP ${res.status}`
          return
        }
        this.streamUrl = data.url
        this.streamQuality = data.quality
        this.streamIsHls = Boolean(data.is_hls)
      } catch (err) {
        console.error('[Player] loadStream failed:', err)
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
          this.streamError = 'Требуется вход'
          return
        }
        this.streamError = err instanceof Error ? err.message : String(err)
      } finally {
        this.streamLoading = false
      }
    },

    /** Fetch all qualities and subtitles for the current Rezka selection. */
    async _loadRezkaStreamDetails(
      itemId: number,
      translator: string | null,
      season?: string | null,
      episode?: string | null,
    ): Promise<void> {
      try {
        const params = new URLSearchParams()
        if (translator) params.set('translator', translator)
        if (season) params.set('season', season)
        if (episode) params.set('episode', episode)
        const qs = params.toString()
        const path = `/api/stream/${itemId}${qs ? `?${qs}` : ''}`
        const res = await apiFetch(path)
        if (!res.ok) return
        const data = (await res.json().catch(() => ({}))) as {
          videos?: Record<string, string>
          subtitles?: Record<string, Subtitle>
          error?: string
        }
        if (data.error) return
        this.subtitles = data.subtitles ?? {}
        this.rezkaQualities = data.videos ? Object.keys(data.videos) : []
        // Sort qualities by rank
        const rank = (q: string) => ({
          '4K': 7,
          '2K': 6,
          '1080p': 5,
          '1080p Ultra': 4,
          '720p': 3,
          '480p': 2,
          '360p': 1,
        }[q] || 0)
        this.rezkaQualities.sort((a, b) => rank(b) - rank(a))
      } catch {
        /* non-fatal */
      }
    },

    async selectRezkaQuality(quality: string): Promise<void> {
      if (!this.itemId || !this.source) return
      this.streamQuality = quality
      this.streamConfirmed = false
      await this.loadStream(
        this.itemId,
        this.source,
        this.translatorId,
        this.season,
        this.episode,
      )
    },

    setActiveTab(tab: 'kinohub' | 'rezka' | 'kinopub'): void {
      this.activeTab = tab
      if (tab === 'kinopub') {
        this.source = 'kinopub'
        if (!this.kinopubInfo && this.itemId) {
          void this.loadKinopubInfo(this.itemId)
        } else {
          this._refreshKinopubStream()
        }
      } else if (tab === 'rezka') {
        this.source = 'rezka'
        if (this.itemId && this.translatorId) {
          void this.loadStream(
            this.itemId,
            'rezka',
            this.translatorId,
            this.season,
            this.episode,
          )
        }
      }
    },

    confirmStream(): void {
      this.streamConfirmed = true
    },

    /** `POST /api/mark_season_seen/{id}` — clears the "новый сезон"
     *  badge on the feed card. The backend currently ignores
     *  season/episode params (it reads `latest_*` off the item row),
     *  but we accept them per the roadmap contract for future
     *  compatibility. Returns `true` on success. */
    async markSeasonSeen(
      season?: string | null,
      episode?: string | null,
    ): Promise<boolean> {
      const id = this.itemId
      if (id === null) return false
      const session = useSessionStore()
      if (!session.canCallApi) return false
      const toast = useToastStore()
      try {
        const body: Record<string, string> = {}
        if (season) body.season = season
        if (episode) body.episode = episode
        const init: RequestInit = { method: 'POST' }
        if (Object.keys(body).length > 0) {
          init.headers = { 'Content-Type': 'application/json' }
          init.body = JSON.stringify(body)
        }
        const res = await apiFetch(`/api/mark_season_seen/${id}`, init)
        if (!res.ok) {
          toast.error(`Не удалось отметить сезон (HTTP ${res.status})`)
          return false
        }
        toast.success('Сезон отмечен как просмотренный')
        return true
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
          toast.error('Требуется вход')
          return false
        }
        toast.error(err instanceof Error ? err.message : String(err))
        return false
      }
    },

    /** Update the active translator and reload everything downstream
     *  (season list + resolved stream). Used by the translator
     *  select in PlayerModal. */
    async selectTranslator(translatorId: string): Promise<void> {
      this.translatorId = translatorId
      this.season = null
      this.episode = null
      this.streamUrl = null
      this.streamQuality = null
      this.streamConfirmed = false
      if (this.isSeries && this.info?.series_info) {
        const si = this.info.series_info[translatorId]
        const firstSeason = Object.keys(si?.seasons ?? {})[0] ?? null
        this.season = firstSeason
        if (firstSeason) {
          const firstEp = Object.keys(si?.episodes?.[firstSeason] ?? {})[0] ?? null
          this.episode = firstEp
        }
      }
      if (this.itemId !== null && this.source) {
        await this.loadStream(
          this.itemId,
          this.source,
          this.translatorId,
          this.season,
          this.episode,
        )
      }
    },

    /** Pick a season + auto-seed first episode + reload stream. */
    async selectSeason(season: string): Promise<void> {
      this.season = season
      this.episode = null
      this.streamUrl = null
      this.streamQuality = null
      this.streamConfirmed = false
      if (this.info?.series_info && this.translatorId) {
        const si = this.info.series_info[this.translatorId]
        const firstEp = Object.keys(si?.episodes?.[season] ?? {})[0] ?? null
        this.episode = firstEp
      }
      if (this.itemId !== null && this.source) {
        await this.loadStream(
          this.itemId,
          this.source,
          this.translatorId,
          this.season,
          this.episode,
        )
      }
    },

    /** Pick an episode + reload stream. */
    async selectEpisode(episode: string): Promise<void> {
      this.episode = episode
      this.streamConfirmed = false
      this.streamUrl = null
      this.streamQuality = null
      if (this.itemId !== null && this.source) {
        await this.loadStream(
          this.itemId,
          this.source,
          this.translatorId,
          this.season,
          this.episode,
        )
      }
    },

    // ── PR 5: kino.pub playback ────────────────────────────────────

    /** Open the stream modal in kino.pub mode. Mirrors `openStream()`
     *  but skips the rezka info / online-sources fetches — the
     *  PlayerModal switches its rendering on `source === 'kinopub'`. */
    openKinopubStream(itemId: number, title?: string | null): void {
      Object.assign(this, emptyState())
      this.itemId = itemId
      this.itemTitle = title ?? null
      this.mode = 'stream'
      this.source = 'kinopub'
      void this.loadKinopubInfo(itemId)
    },

    /** Fetch `/api/kinopub/stream_info/{id}` and seed the first
     *  playable selection so PlayerModal can render the `<video>`
     *  without an extra user click. */
    async loadKinopubInfo(itemId: number): Promise<void> {
      const session = useSessionStore()
      this.kinopubError = null
      this.kinopubInfo = null
      this.kinopubVideoIdx = null
      this.kinopubSeasonNumber = null
      this.kinopubEpisodeNumber = null
      this.kinopubFileIdx = null
      this.kinopubSubtitleLang = ''
      this.streamUrl = null
      this.streamQuality = null
      this.streamIsHls = false
      if (!session.canCallApi) return
      this.kinopubLoading = true
      try {
        const res = await apiFetch(`/api/kinopub/stream_info/${itemId}`)
        const data = (await res.json().catch(() => ({}))) as
          | (KinopubStreamInfo & { detail?: string })
          | { detail?: string }
        if (!res.ok) {
          const detail =
            'detail' in data && typeof data.detail === 'string'
              ? data.detail
              : `HTTP ${res.status}`
          this.kinopubError = detail
          return
        }
        const info = data as KinopubStreamInfo
        this.kinopubInfo = info
        // Auto-seed: movie → videos[0]; serial → first season/episode.
        if (Array.isArray(info.videos) && info.videos.length > 0) {
          this.kinopubVideoIdx = 0
        } else if (Array.isArray(info.seasons) && info.seasons.length > 0) {
          const firstSeason = info.seasons[0]
          if (firstSeason && typeof firstSeason.number === 'number') {
            this.kinopubSeasonNumber = firstSeason.number
            const firstEp = firstSeason.episodes?.[0]
            if (firstEp && typeof firstEp.number === 'number') {
              this.kinopubEpisodeNumber = firstEp.number
            }
          }
        }
        this._refreshKinopubStream()
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
          this.kinopubError = 'Требуется вход'
          return
        }
        this.kinopubError = err instanceof Error ? err.message : String(err)
      } finally {
        this.kinopubLoading = false
      }
    },

    /** Re-pick the playable file based on current selection. Called
     *  internally after a season/episode/quality change so callers
     *  don't have to remember to update `streamUrl`. */
    _refreshKinopubStream(): void {
      const info = this.kinopubInfo
      if (!info) {
        this.streamUrl = null
        this.streamQuality = null
        this.streamIsHls = false
        return
      }
      const video = _findKinopubVideo(info, {
        videoIdx: this.kinopubVideoIdx,
        season: this.kinopubSeasonNumber,
        episode: this.kinopubEpisodeNumber,
      })
      if (!video || !Array.isArray(video.files) || video.files.length === 0) {
        this.streamUrl = null
        this.streamQuality = null
        this.streamIsHls = false
        this.kinopubFileIdx = null
        return
      }
      // Reset file idx when the previous one is out of range for the
      // newly-selected video.
      if (
        this.kinopubFileIdx === null ||
        this.kinopubFileIdx >= video.files.length
      ) {
        this.kinopubFileIdx = _bestKinopubFileIdx(video) ?? 0
      }
      const file = video.files[this.kinopubFileIdx]
      if (!file) {
        this.streamUrl = null
        this.streamQuality = null
        this.streamIsHls = false
        return
      }
      this.streamUrl = file.url
      this.streamQuality = file.quality ?? null
      // kino.pub serves both .m3u8 (HLS) and .mp4. Detect by extension
      // since the JSON doesn't distinguish.
      this.streamIsHls = /\.m3u8(\?.*)?$/i.test(file.url)
    },

    /** Pick a season + auto-seed first episode + refresh stream. */
    selectKinopubSeason(seasonNumber: number): void {
      this.kinopubSeasonNumber = seasonNumber
      this.streamConfirmed = false
      this.kinopubEpisodeNumber = null
      this.kinopubFileIdx = null
      const season = (this.kinopubInfo?.seasons ?? []).find(
        (s) => s.number === seasonNumber,
      )
      const firstEp = season?.episodes?.[0]
      if (firstEp && typeof firstEp.number === 'number') {
        this.kinopubEpisodeNumber = firstEp.number
      }
      this._refreshKinopubStream()
    },

    /** Pick an episode within the current season. */
    selectKinopubEpisode(episodeNumber: number): void {
      this.kinopubEpisodeNumber = episodeNumber
      this.streamConfirmed = false
      this.kinopubFileIdx = null
      this._refreshKinopubStream()
    },

    /** Pick a specific quality file (0-based index). */
    selectKinopubFile(fileIdx: number): void {
      this.kinopubFileIdx = fileIdx
      this.streamConfirmed = false
      this._refreshKinopubStream()
    },

    /** Change the active `<track>` language. Empty string disables
     *  subtitles. PlayerModal renders this back as the `default`
     *  attribute on the matching `<track>` element. */
    selectKinopubSubtitle(lang: string): void {
      this.kinopubSubtitleLang = lang
    },
  },
})
