// ROADMAP Stage 10.7g — PlayerModal component tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import PlayerModal from './PlayerModal.vue'
import { useItemPlayerStore } from '../stores/player'
import { useSessionStore } from '../stores/session'

// hls.js is mocked out — we never actually attach a HLS stream in
// unit tests (happy-dom <video> doesn't implement HLS). The store
// stays the source of truth for what's been resolved; we just check
// the component reads the correct getters.
vi.mock('hls.js', () => {
  class FakeHls {
    static isSupported(): boolean {
      return true
    }
    static Events = {
      AUDIO_TRACK_SWITCHED: 'AUDIO_TRACK_SWITCHED',
      LEVEL_LOADED: 'LEVEL_LOADED',
      MANIFEST_PARSED: 'MANIFEST_PARSED',
    }
    loadSource(): void {}
    attachMedia(): void {}
    destroy(): void {}
    on(): void {}
  }
  return { default: FakeHls }
})

let objectUrlSeq = 0
const createObjectURL = vi.fn(() => `blob:subtitle-${objectUrlSeq++}`)
const revokeObjectURL = vi.fn()

Object.defineProperty(URL, 'createObjectURL', {
  value: createObjectURL,
  configurable: true,
})

Object.defineProperty(URL, 'revokeObjectURL', {
  value: revokeObjectURL,
  configurable: true,
})

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function authorise(): void {
  useSessionStore().$patch({ status: 'disabled' })
}

function openRezkaStream(): ReturnType<typeof useItemPlayerStore> {
  const player = useItemPlayerStore()
  player.itemId = 42
  player.mode = 'stream'
  player.source = 'rezka'
  player.activeTab = 'rezka'
  return player
}

describe('PlayerModal.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    objectUrlSeq = 0
    createObjectURL.mockClear()
    revokeObjectURL.mockClear()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('does not render when the player store is closed', () => {
    const wrapper = mount(PlayerModal)
    expect(wrapper.find('[data-testid="player-modal"]').exists()).toBe(false)
  })

  it('renders the trailer surface with an iframe when a candidate is loaded', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({
        youtube_key: 'abc123',
        candidates: [
          { youtube_key: 'abc123', name: 'Official', type: 'Trailer', official: true },
        ],
      }),
    )
    authorise()
    const player = useItemPlayerStore()
    player.openTrailer(42, 'A Movie')
    const wrapper = mount(PlayerModal)
    await flushPromises()

    expect(wrapper.find('[data-testid="player-modal"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="player-modal-title"]').text()).toContain(
      'A Movie',
    )
    const frame = wrapper.find('[data-testid="player-trailer-frame"]')
    expect(frame.exists()).toBe(true)
    expect(frame.attributes('src')).toContain('youtube.com/embed/abc123')
    // No cycle button when only one candidate.
    expect(wrapper.find('[data-testid="player-trailer-cycle"]').exists()).toBe(
      false,
    )
  })

  it('shows the cycle button only when multiple candidates exist', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({
        youtube_key: 'abc',
        candidates: [
          { youtube_key: 'abc', name: 'Official', type: 'Trailer', official: true },
          { youtube_key: 'def', name: 'Teaser', type: 'Teaser', official: false },
        ],
      }),
    )
    authorise()
    const player = useItemPlayerStore()
    player.openTrailer(42, 'A Movie')
    const wrapper = mount(PlayerModal)
    await flushPromises()
    const cycleBtn = wrapper.find('[data-testid="player-trailer-cycle"]')
    expect(cycleBtn.exists()).toBe(true)
    await cycleBtn.trigger('click')
    expect(player.currentTrailerKey).toBe('def')
  })

  it('surfaces trailerError when the backend has nothing', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ error: 'no trailer available' }, { status: 404 }),
    )
    authorise()
    const player = useItemPlayerStore()
    player.openTrailer(42, 'A Movie')
    const wrapper = mount(PlayerModal)
    await flushPromises()
    expect(wrapper.find('[data-testid="player-trailer-error"]').text()).toContain(
      'no trailer available',
    )
    expect(wrapper.find('[data-testid="player-trailer-frame"]').exists()).toBe(
      false,
    )
  })

  it('renders translator + season + episode selects for series', async () => {
    authorise()
    const player = openRezkaStream()
    player.itemTitle = 'A Series'
    player.info = {
      type: 'series',
      name: 'A Series',
      translators: { '111': { name: 'Дубляж', premium: false } },
      series_info: {
        '111': {
          name: 'Дубляж',
          premium: false,
          seasons: { '1': 'Сезон 1', '2': 'Сезон 2' },
          episodes: {
            '1': { '1': 'Серия 1' },
            '2': { '1': 'Серия 1', '2': 'Серия 2' },
          },
        },
      },
    }
    player.translatorId = '111'
    player.season = '1'
    player.episode = '1'

    const wrapper = mount(PlayerModal)
    await flushPromises()

    const selects = wrapper.find('[data-testid="player-section-rezka"]').findAll('select')
    expect(selects).toHaveLength(3)
    expect(selects[0].text()).toContain('Дубляж')
    expect(selects[1].text()).toContain('Сезон 1')
    expect(selects[2].text()).toContain('Серия 1')
    expect(wrapper.find('[data-testid="player-section-rezka"]').text()).toContain(
      'Отметить сезон как просмотренный',
    )
  })

  it('hides series controls for movie content', async () => {
    authorise()
    const player = openRezkaStream()
    player.itemTitle = 'A Movie'
    player.info = {
      type: 'movie',
      name: 'A Movie',
      translators: { '111': { name: 'Дубляж', premium: false } },
    }
    player.translatorId = '111'

    const wrapper = mount(PlayerModal)
    await flushPromises()

    const selects = wrapper.find('[data-testid="player-section-rezka"]').findAll('select')
    expect(selects).toHaveLength(1)
    expect(selects[0].text()).toContain('Дубляж')
    expect(wrapper.find('[data-testid="player-section-rezka"]').text()).not.toContain(
      'Сезон',
    )
    expect(wrapper.find('[data-testid="player-section-rezka"]').text()).not.toContain(
      'Отметить просмотренной',
    )
  })

  it('shows the "no translators" hint when translators is empty', async () => {
    authorise()
    const player = openRezkaStream()
    player.info = { type: 'movie', name: 'X', translators: {} }

    const wrapper = mount(PlayerModal)
    await flushPromises()

    expect(
      wrapper.find('[data-testid="player-section-rezka"]').text(),
    ).toContain('Встроенный плеер активен')
    expect(wrapper.find('[data-testid="player-section-rezka"]').findAll('select')).toHaveLength(0)
  })

  it('renders alternative online sources when present', async () => {
    authorise()
    const player = openRezkaStream()
    player.activeTab = 'kinohub'
    player.sources = [
      { type: 'Alloha', iframeUrl: 'https://alloha/x' },
      { type: 'Collaps', iframeUrl: 'https://collaps/x' },
    ]
    player.sourcesPageUrl = 'https://fbdomen/x'

    const wrapper = mount(PlayerModal)
    await flushPromises()

    expect(wrapper.find('[data-testid="player-section-kinohub"]').exists()).toBe(
      true,
    )
    expect(
      wrapper.find('[data-testid="player-stream-source-Alloha"]').attributes('href'),
    ).toBe('https://alloha/x')
    expect(
      wrapper.find('[data-testid="player-stream-page-url"]').attributes('href'),
    ).toBe('https://fbdomen/x')
  })

  it('renders the video + VLC links when a stream URL is resolved', async () => {
    authorise()
    const player = openRezkaStream()
    player.itemTitle = 'A Movie'
    player.info = {
      type: 'movie',
      name: 'A Movie',
      translators: { '111': { name: 'Дубляж' } },
    }
    player.translatorId = '111'
    player.streamUrl = 'https://rezka/cdn/x.m3u8'
    player.streamQuality = '1080p'
    player.streamIsHls = true
    player.subtitles = {
      ru: { title: 'Русские', link: 'https://rezka/subs/ru.vtt' },
    }
    player.streamConfirmed = true
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nТекст', {
        headers: { 'Content-Type': 'text/vtt' },
      }),
    )

    const wrapper = mount(PlayerModal)
    await flushPromises()
    await flushPromises()

    const video = wrapper.find('[data-testid="player-stream-video"]')
    expect(video.exists()).toBe(true)
    const m3u = wrapper.find('[data-testid="player-stream-m3u"]')
    expect(m3u.attributes('href')).toContain('/api/stream_m3u/42?quality=1080p')
    expect(m3u.attributes('href')).toContain('translator=111')
    // Subtitle <track> should reference subtitle_proxy.
    const tracks = wrapper.findAll('track')
    expect(tracks.length).toBe(1)
    expect(tracks[0].attributes('src')).toBe('blob:subtitle-0')
    expect(String(vi.mocked(globalThis.fetch).mock.calls[0][0])).toContain(
      '/api/subtitle_proxy?url=',
    )
  })

  it('fetches subtitle tracks with bearer token before rendering them', async () => {
    authorise()
    window.sessionStorage.setItem('authToken', 'test-token')
    const player = useItemPlayerStore()
    player.itemId = 42
    player.itemTitle = 'A Movie'
    player.mode = 'stream'
    player.source = 'kinopub'
    player.activeTab = 'kinopub'
    player.streamUrl = 'https://cdn.kino.pub/movie.m3u8'
    player.streamQuality = '1080p'
    player.streamIsHls = true
    player.streamConfirmed = true
    player.kinopubVideoIdx = 0
    player.kinopubSubtitleLang = 'ru'
    player.kinopubInfo = {
      id: 555,
      title: 'A Movie',
      year: 2024,
      type: 'movie',
      url: 'https://kino.pub/item/555',
      seasons: [],
      videos: [
        {
          number: 1,
          title: null,
          duration: null,
          files: [{ url: 'https://cdn.kino.pub/movie.m3u8', quality: '1080p', codec: null }],
          audios: [],
          subtitles: [{ url: 'https://cdn.kino.pub/subs/ru.srt', lang: 'ru', shift: 0, embed: false }],
        },
      ],
    }
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nТекст', {
        headers: { 'Content-Type': 'text/vtt' },
      }),
    )

    const wrapper = mount(PlayerModal)
    await flushPromises()
    await flushPromises()

    expect(String(vi.mocked(globalThis.fetch).mock.calls[0][0])).toContain(
      encodeURIComponent('https://cdn.kino.pub/subs/ru.srt'),
    )
    const init = vi.mocked(globalThis.fetch).mock.calls[0][1] as RequestInit
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer test-token')
    const tracks = wrapper.findAll('track')
    expect(tracks).toHaveLength(1)
    expect(tracks[0].attributes('src')).toBe('blob:subtitle-0')
    expect(tracks[0].attributes('default')).toBe('')
  })

  it('clicking the close button calls player.close()', async () => {
    authorise()
    const player = useItemPlayerStore()
    player.itemId = 42
    player.itemTitle = 'A Movie'
    player.mode = 'stream'

    const wrapper = mount(PlayerModal)
    await flushPromises()
    expect(player.isOpen).toBe(true)
    await wrapper.find('[data-testid="player-modal-close"]').trigger('click')
    expect(player.isOpen).toBe(false)
  })

  it('translator select triggers selectTranslator', async () => {
    authorise()
    const player = openRezkaStream()
    player.info = {
      type: 'movie',
      name: 'A Movie',
      translators: {
        '111': { name: 'Дубляж' },
        '222': { name: 'Оригинал' },
      },
    }
    player.translatorId = '111'

    // Mock the subsequent stream + subtitle fetches triggered by
    // selectTranslator → loadStream.
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(
        mockJson({
          url: 'https://rezka/x.m3u8',
          quality: '1080p',
          title: 'A Movie',
          is_hls: true,
        }),
      )
      .mockResolvedValueOnce(mockJson({ subtitles: {} }))
      .mockResolvedValueOnce(new Response('WEBVTT\n\n'))

    const wrapper = mount(PlayerModal)
    await flushPromises()
    const select = wrapper.find('[data-testid="player-section-rezka"]').find('select')
    await select.setValue('222')
    await flushPromises()
    expect(player.translatorId).toBe('222')
    expect(
      String(vi.mocked(globalThis.fetch).mock.calls[0][0]),
    ).toContain('translator=222')
  })
})
