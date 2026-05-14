// ROADMAP Stage 10.7g — useItemPlayerStore unit tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'

import { useItemPlayerStore } from './player'
import { useSessionStore } from './session'
import { useToastStore } from './toast'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function authorise(): void {
  useSessionStore().$patch({ status: 'disabled' })
}

const TRAILER_PAYLOAD = {
  youtube_key: 'abc123',
  name: 'Official Trailer',
  type: 'Trailer',
  official: true,
  candidates: [
    { youtube_key: 'abc123', name: 'Official Trailer', type: 'Trailer', official: true },
    { youtube_key: 'def456', name: 'Teaser', type: 'Teaser', official: false },
  ],
}

const SOURCES_PAYLOAD = {
  sources: [
    { type: 'Alloha', iframeUrl: 'https://alloha/x', translations: [] },
    { type: 'Collaps', iframeUrl: 'https://collaps/x' },
  ],
  pageUrl: 'https://fbdomen.cfd/film/111/',
}

const MOVIE_INFO_PAYLOAD = {
  type: 'movie',
  name: 'A Movie',
  translators: {
    '111': { name: 'Дубляж', premium: false },
    '222': { name: 'Оригинал', premium: true },
  },
}

const SERIES_INFO_PAYLOAD = {
  type: 'series',
  name: 'A Series',
  translators: {
    '111': { name: 'Дубляж', premium: false },
  },
  series_info: {
    '111': {
      name: 'Дубляж',
      premium: false,
      seasons: { '1': 'Сезон 1', '2': 'Сезон 2' },
      episodes: {
        '1': { '1': 'Серия 1', '2': 'Серия 2' },
        '2': { '1': 'Серия 1' },
      },
    },
  },
}

const STREAM_URL_PAYLOAD = {
  url: 'https://rezka/cdn/x.m3u8',
  quality: '1080p',
  title: 'A Series',
  is_hls: true,
}

const STREAM_FULL_PAYLOAD = {
  videos: { '1080p': 'https://rezka/cdn/x.m3u8' },
  subtitles: {
    ru: { title: 'Русские', link: 'https://rezka/subs/ru.vtt' },
  },
  translator_id: '111',
}

describe('useItemPlayerStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('openTrailer / loadTrailer', () => {
    it('hydrates trailerCandidates from the ranked list', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(TRAILER_PAYLOAD))
      authorise()
      const player = useItemPlayerStore()
      player.openTrailer(42, 'A Movie')
      await flushPromises()
      expect(player.mode).toBe('trailer')
      expect(player.itemId).toBe(42)
      expect(player.itemTitle).toBe('A Movie')
      expect(player.trailerCandidates).toHaveLength(2)
      expect(player.currentTrailerKey).toBe('abc123')
    })

    it('falls back to the flat shape when `candidates` is missing', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({
          youtube_key: 'flat-only',
          name: 'Flat',
          type: 'Trailer',
          official: false,
        }),
      )
      authorise()
      const player = useItemPlayerStore()
      await player.loadTrailer(42)
      expect(player.trailerCandidates).toEqual([
        { youtube_key: 'flat-only', name: 'Flat', type: 'Trailer', official: false },
      ])
      expect(player.currentTrailerKey).toBe('flat-only')
    })

    it('surfaces trailerError on 404 with empty body', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ error: 'no trailer available' }, { status: 404 }),
      )
      authorise()
      const player = useItemPlayerStore()
      await player.loadTrailer(42)
      expect(player.trailerError).toBe('no trailer available')
      expect(player.trailerCandidates).toHaveLength(0)
    })

    it('surfaces a generic message when the backend returns no candidate', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson({}))
      authorise()
      const player = useItemPlayerStore()
      await player.loadTrailer(42)
      expect(player.trailerError).toBe('Трейлер не найден')
    })

    it('cycleTrailerCandidate wraps through the candidate list', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(TRAILER_PAYLOAD))
      authorise()
      const player = useItemPlayerStore()
      await player.loadTrailer(42)
      expect(player.currentTrailerKey).toBe('abc123')
      player.cycleTrailerCandidate()
      expect(player.currentTrailerKey).toBe('def456')
      player.cycleTrailerCandidate()
      expect(player.currentTrailerKey).toBe('abc123')
    })

    it('skips network calls when the session is unauthenticated', async () => {
      useSessionStore().$patch({ status: 'unauthenticated' })
      const player = useItemPlayerStore()
      await player.loadTrailer(42)
      expect(globalThis.fetch).not.toHaveBeenCalled()
    })
  })

  describe('loadSources', () => {
    it('populates sources + pageUrl on a happy response', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(SOURCES_PAYLOAD))
      authorise()
      const player = useItemPlayerStore()
      player.itemId = 42
      await player.loadSources(42)
      expect(player.sources).toHaveLength(2)
      expect(player.sourcesPageUrl).toBe('https://fbdomen.cfd/film/111/')
      expect(player.sourcesError).toBeNull()
    })

    it('handles empty source lists gracefully (no error)', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ sources: [], pageUrl: null }),
      )
      authorise()
      const player = useItemPlayerStore()
      await player.loadSources(42)
      expect(player.sources).toEqual([])
      expect(player.sourcesPageUrl).toBeNull()
      expect(player.sourcesError).toBeNull()
    })

    it('records sourcesError on non-2xx response', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ error: 'kinobox down' }, { status: 500 }),
      )
      authorise()
      const player = useItemPlayerStore()
      await player.loadSources(42)
      expect(player.sourcesError).toBe('kinobox down')
    })
  })

  describe('loadInfo', () => {
    it('forwards source + translator query params', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(MOVIE_INFO_PAYLOAD))
      authorise()
      const player = useItemPlayerStore()
      await player.loadInfo(42, 'rezka', '222')
      const call = vi.mocked(globalThis.fetch).mock.calls[0]
      expect(String(call[0])).toBe(
        '/api/stream_info/42?source=rezka&translator_id=222',
      )
      expect(player.info?.type).toBe('movie')
      expect(player.translatorId).toBe('222')
      expect(player.season).toBeNull()
      expect(player.episode).toBeNull()
    })

    it('auto-seeds season + episode for a series', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson(SERIES_INFO_PAYLOAD),
      )
      authorise()
      const player = useItemPlayerStore()
      await player.loadInfo(42, 'rezka')
      expect(player.translatorId).toBe('111')
      expect(player.season).toBe('1')
      expect(player.episode).toBe('1')
      expect(player.isSeries).toBe(true)
      expect(player.seasonsMap).toEqual({ '1': 'Сезон 1', '2': 'Сезон 2' })
      expect(player.episodesMap).toEqual({ '1': 'Серия 1', '2': 'Серия 2' })
    })

    it('handles the empty-translators payload without crashing', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ type: 'movie', name: 'A', translators: {} }),
      )
      authorise()
      const player = useItemPlayerStore()
      await player.loadInfo(42, 'rezka')
      expect(player.info?.translators).toEqual({})
      expect(player.translatorId).toBeNull()
    })

    it('records infoError when the backend returns {error}', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ error: 'no rezka_url' }),
      )
      authorise()
      const player = useItemPlayerStore()
      await player.loadInfo(42, 'rezka')
      expect(player.infoError).toBe('no rezka_url')
      expect(player.info).toBeNull()
    })

    it('records infoError on a non-2xx response', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        new Response('boom', { status: 502 }),
      )
      authorise()
      const player = useItemPlayerStore()
      await player.loadInfo(42, 'rezka')
      expect(player.infoError).toBe('HTTP 502')
    })
  })

  describe('loadStream', () => {
    it('resolves stream + fetches subtitles in one call', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson(STREAM_URL_PAYLOAD))
        .mockResolvedValueOnce(mockJson(STREAM_FULL_PAYLOAD))
      authorise()
      const player = useItemPlayerStore()
      await player.loadStream(42, 'rezka', '111', '1', '2')
      expect(player.streamUrl).toBe('https://rezka/cdn/x.m3u8')
      expect(player.streamQuality).toBe('1080p')
      expect(player.streamIsHls).toBe(true)
      expect(player.subtitles.ru?.title).toBe('Русские')

      const calls = vi.mocked(globalThis.fetch).mock.calls
      expect(String(calls[0][0])).toBe(
        '/api/stream_url/42?translator=111&season=1&episode=2',
      )
      expect(String(calls[1][0])).toBe(
        '/api/stream/42?translator=111&season=1&episode=2',
      )
    })

    it('still works when subtitles fetch fails (subs cleared)', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson(STREAM_URL_PAYLOAD))
        .mockResolvedValueOnce(new Response('boom', { status: 500 }))
      authorise()
      const player = useItemPlayerStore()
      await player.loadStream(42, 'rezka', '111')
      expect(player.streamUrl).toBe('https://rezka/cdn/x.m3u8')
      expect(player.subtitles).toEqual({})
    })

    it('records streamError when the backend returns {error}', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ error: 'no stream url' }, { status: 502 }),
      )
      authorise()
      const player = useItemPlayerStore()
      await player.loadStream(42, 'rezka', '111')
      expect(player.streamError).toBe('no stream url')
      expect(player.streamUrl).toBeNull()
    })

    it('builds streamM3uUrl from the current selection', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson(STREAM_URL_PAYLOAD))
        .mockResolvedValueOnce(mockJson(STREAM_FULL_PAYLOAD))
      authorise()
      const player = useItemPlayerStore()
      player.itemId = 42
      await player.loadStream(42, 'rezka', '111', '1', '2')
      expect(player.streamM3uUrl).toBe(
        '/api/stream_m3u/42?quality=1080p&translator=111&season=1&episode=2',
      )
    })

    it('streamM3uUrl is null when no quality is resolved yet', () => {
      const player = useItemPlayerStore()
      player.itemId = 42
      expect(player.streamM3uUrl).toBeNull()
    })
  })

  describe('selectTranslator / selectSeason / selectEpisode', () => {
    it('reloads stream when picking a different translator', async () => {
      authorise()
      const player = useItemPlayerStore()
      // Pre-seed state so we can exercise selectTranslator in
      // isolation (avoids juggling openStream's parallel fetches).
      player.itemId = 42
      player.source = 'rezka'
      player.info = MOVIE_INFO_PAYLOAD as never
      player.translatorId = '111'

      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson(STREAM_URL_PAYLOAD))
        .mockResolvedValueOnce(mockJson(STREAM_FULL_PAYLOAD))
      await player.selectTranslator('222')
      expect(player.translatorId).toBe('222')
      const call = vi.mocked(globalThis.fetch).mock.calls[0]
      expect(String(call[0])).toContain('translator=222')
    })

    it('selectSeason auto-seeds first episode + reloads stream', async () => {
      authorise()
      const player = useItemPlayerStore()
      // Hand-seed series info so we can exercise the selector in
      // isolation without juggling openStream's parallel fetches.
      player.itemId = 42
      player.source = 'rezka'
      player.info = SERIES_INFO_PAYLOAD as never
      player.translatorId = '111'
      player.season = '1'
      player.episode = '1'

      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson(STREAM_URL_PAYLOAD))
        .mockResolvedValueOnce(mockJson(STREAM_FULL_PAYLOAD))

      await player.selectSeason('2')
      expect(player.season).toBe('2')
      expect(player.episode).toBe('1')
      const call = vi.mocked(globalThis.fetch).mock.calls[0]
      expect(String(call[0])).toContain('season=2')
      expect(String(call[0])).toContain('episode=1')
    })
  })

  describe('markSeasonSeen', () => {
    it('POSTs and toasts on success', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ status: 'success' }),
      )
      authorise()
      const player = useItemPlayerStore()
      const toast = useToastStore()
      player.itemId = 42
      const ok = await player.markSeasonSeen('2', '3')
      expect(ok).toBe(true)
      expect(toast.current?.tone).toBe('success')
      const call = vi.mocked(globalThis.fetch).mock.calls[0]
      expect(String(call[0])).toBe('/api/mark_season_seen/42')
      expect((call[1] as RequestInit | undefined)?.method).toBe('POST')
      expect((call[1] as RequestInit | undefined)?.body).toBe(
        JSON.stringify({ season: '2', episode: '3' }),
      )
    })

    it('returns false + toasts when the backend errors', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        new Response('boom', { status: 500 }),
      )
      authorise()
      const player = useItemPlayerStore()
      const toast = useToastStore()
      player.itemId = 42
      const ok = await player.markSeasonSeen()
      expect(ok).toBe(false)
      expect(toast.current?.tone).toBe('error')
    })

    it('no-ops when no item is bound', async () => {
      authorise()
      const player = useItemPlayerStore()
      const ok = await player.markSeasonSeen()
      expect(ok).toBe(false)
      expect(globalThis.fetch).not.toHaveBeenCalled()
    })
  })

  describe('openTrailer / openStream / close', () => {
    it('openStream kicks off info + sources in parallel', async () => {
      vi.mocked(globalThis.fetch)
        // openStream fires loadSources + loadInfo in parallel; the
        // order they resolve in is best-effort, but both happy here.
        .mockResolvedValue(mockJson({}))
      authorise()
      const player = useItemPlayerStore()
      player.openStream(42, 'X')
      expect(player.mode).toBe('stream')
      expect(player.itemId).toBe(42)
      expect(player.source).toBe('rezka')
      await flushPromises()
      expect(vi.mocked(globalThis.fetch).mock.calls.length).toBeGreaterThanOrEqual(2)
    })

    it('close() resets all state', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(TRAILER_PAYLOAD))
      authorise()
      const player = useItemPlayerStore()
      player.openTrailer(42, 'X')
      await flushPromises()
      expect(player.isOpen).toBe(true)
      player.close()
      expect(player.isOpen).toBe(false)
      expect(player.itemId).toBeNull()
      expect(player.mode).toBeNull()
      expect(player.trailerCandidates).toEqual([])
    })

    it('isTrailerOpen / isStreamOpen reflect mode', () => {
      const player = useItemPlayerStore()
      expect(player.isOpen).toBe(false)
      player.mode = 'trailer'
      expect(player.isTrailerOpen).toBe(true)
      expect(player.isStreamOpen).toBe(false)
      player.mode = 'stream'
      expect(player.isTrailerOpen).toBe(false)
      expect(player.isStreamOpen).toBe(true)
    })
  })

  // ── PR 5: kino.pub playback ──────────────────────────────────────
  describe('openKinopubStream / loadKinopubInfo', () => {
    const KINOPUB_MOVIE = {
      id: 1234,
      title: 'Kino Movie',
      year: 2024,
      type: 'movie',
      url: 'https://kino.pub/item/1234',
      videos: [
        {
          number: 1,
          title: null,
          duration: 5400,
          files: [
            { url: 'https://cdn.kino.pub/720.mp4', quality: '720p', codec: 'h264' },
            { url: 'https://cdn.kino.pub/1080.mp4', quality: '1080p', codec: 'h264' },
            { url: 'https://cdn.kino.pub/2160.mp4', quality: '2160p', codec: 'hevc' },
          ],
          audios: [
            { lang: 'ru', author: 'Дубляж', type: 'translate' },
          ],
          subtitles: [
            { url: 'https://cdn.kino.pub/subs/ru.vtt', lang: 'ru', shift: 0, embed: false },
          ],
        },
      ],
      seasons: [],
    }

    const KINOPUB_SERIAL = {
      id: 5678,
      title: 'Kino Show',
      year: 2024,
      type: 'serial',
      url: 'https://kino.pub/item/5678',
      videos: [],
      seasons: [
        {
          number: 1,
          episodes: [
            {
              number: 1,
              title: 'Pilot',
              duration: 2700,
              files: [
                { url: 'https://cdn.kino.pub/s1e1-1080.mp4', quality: '1080p', codec: 'h264' },
              ],
              audios: [],
              subtitles: [],
            },
            {
              number: 2,
              title: 'Second',
              duration: 2700,
              files: [
                { url: 'https://cdn.kino.pub/s1e2-1080.mp4', quality: '1080p', codec: 'h264' },
              ],
              audios: [],
              subtitles: [],
            },
          ],
        },
      ],
    }

    it('seeds the first video and best quality file for movies', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(KINOPUB_MOVIE))
      authorise()
      const player = useItemPlayerStore()
      player.openKinopubStream(1234, 'Kino Movie')
      expect(player.mode).toBe('stream')
      expect(player.source).toBe('kinopub')
      expect(player.itemId).toBe(1234)
      await flushPromises()
      expect(player.kinopubInfo?.id).toBe(1234)
      // Picks 2160p (rank=7) over 1080p (rank=4) over 720p (rank=3).
      expect(player.kinopubFileIdx).toBe(2)
      expect(player.streamUrl).toBe('https://cdn.kino.pub/2160.mp4')
      expect(player.streamQuality).toBe('2160p')
      expect(player.streamIsHls).toBe(false)
    })

    it('seeds first season + first episode for serials', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(KINOPUB_SERIAL))
      authorise()
      const player = useItemPlayerStore()
      player.openKinopubStream(5678, 'Kino Show')
      await flushPromises()
      expect(player.isKinopubSeries).toBe(true)
      expect(player.kinopubSeasonNumber).toBe(1)
      expect(player.kinopubEpisodeNumber).toBe(1)
      expect(player.streamUrl).toBe('https://cdn.kino.pub/s1e1-1080.mp4')
    })

    it('selectKinopubEpisode swaps the active video', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(KINOPUB_SERIAL))
      authorise()
      const player = useItemPlayerStore()
      player.openKinopubStream(5678, 'Kino Show')
      await flushPromises()
      player.selectKinopubEpisode(2)
      expect(player.kinopubEpisodeNumber).toBe(2)
      expect(player.streamUrl).toBe('https://cdn.kino.pub/s1e2-1080.mp4')
    })

    it('selectKinopubFile honours the override', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(KINOPUB_MOVIE))
      authorise()
      const player = useItemPlayerStore()
      player.openKinopubStream(1234, 'Kino Movie')
      await flushPromises()
      player.selectKinopubFile(0)
      expect(player.streamUrl).toBe('https://cdn.kino.pub/720.mp4')
      expect(player.streamQuality).toBe('720p')
    })

    it('streamM3uUrl points at /api/kinopub/m3u/ with episode params', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(KINOPUB_SERIAL))
      authorise()
      const player = useItemPlayerStore()
      player.openKinopubStream(5678, 'Kino Show')
      await flushPromises()
      const url = player.streamM3uUrl ?? ''
      expect(url.startsWith('/api/kinopub/m3u/5678?')).toBe(true)
      expect(url).toContain('season=1')
      expect(url).toContain('episode=1')
      expect(url).toContain('quality=1080p')
    })

    it('detects HLS streams by .m3u8 extension', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({
          ...KINOPUB_MOVIE,
          videos: [
            {
              ...KINOPUB_MOVIE.videos[0],
              files: [
                { url: 'https://cdn.kino.pub/master.m3u8?token=x', quality: '1080p', codec: 'h264' },
              ],
            },
          ],
        }),
      )
      authorise()
      const player = useItemPlayerStore()
      player.openKinopubStream(1234)
      await flushPromises()
      expect(player.streamIsHls).toBe(true)
    })

    it('surfaces error from a failed stream_info fetch', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ detail: 'item is not bound to a kino.pub id' }, { status: 409 }),
      )
      authorise()
      const player = useItemPlayerStore()
      player.openKinopubStream(1234)
      await flushPromises()
      expect(player.kinopubError).toBe('item is not bound to a kino.pub id')
      expect(player.streamUrl).toBeNull()
    })

    it('skips network calls when unauthenticated', async () => {
      useSessionStore().$patch({ status: 'unauthenticated' })
      const player = useItemPlayerStore()
      player.openKinopubStream(1234)
      await flushPromises()
      expect(globalThis.fetch).not.toHaveBeenCalled()
    })

    it('selectKinopubSubtitle updates the active language', () => {
      const player = useItemPlayerStore()
      player.selectKinopubSubtitle('ru')
      expect(player.kinopubSubtitleLang).toBe('ru')
      player.selectKinopubSubtitle('')
      expect(player.kinopubSubtitleLang).toBe('')
    })
  })
})
