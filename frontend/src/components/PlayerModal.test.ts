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
    loadSource(): void {}
    attachMedia(): void {}
    destroy(): void {}
  }
  return { default: FakeHls }
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

describe('PlayerModal.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
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
    const player = useItemPlayerStore()
    player.itemId = 42
    player.itemTitle = 'A Series'
    player.mode = 'stream'
    player.source = 'rezka'
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

    expect(wrapper.find('[data-testid="player-stream-translator"]').exists()).toBe(
      true,
    )
    expect(wrapper.find('[data-testid="player-stream-season"]').exists()).toBe(
      true,
    )
    expect(wrapper.find('[data-testid="player-stream-episode"]').exists()).toBe(
      true,
    )
    expect(wrapper.find('[data-testid="player-stream-mark-seen"]').exists()).toBe(
      true,
    )
  })

  it('hides series controls for movie content', async () => {
    authorise()
    const player = useItemPlayerStore()
    player.itemId = 42
    player.itemTitle = 'A Movie'
    player.mode = 'stream'
    player.source = 'rezka'
    player.info = {
      type: 'movie',
      name: 'A Movie',
      translators: { '111': { name: 'Дубляж', premium: false } },
    }
    player.translatorId = '111'

    const wrapper = mount(PlayerModal)
    await flushPromises()

    expect(wrapper.find('[data-testid="player-stream-translator"]').exists()).toBe(
      true,
    )
    expect(wrapper.find('[data-testid="player-stream-season"]').exists()).toBe(
      false,
    )
    expect(wrapper.find('[data-testid="player-stream-episode"]').exists()).toBe(
      false,
    )
    expect(wrapper.find('[data-testid="player-stream-mark-seen"]').exists()).toBe(
      false,
    )
  })

  it('shows the "no translators" hint when translators is empty', async () => {
    authorise()
    const player = useItemPlayerStore()
    player.itemId = 42
    player.mode = 'stream'
    player.source = 'rezka'
    player.info = { type: 'movie', name: 'X', translators: {} }

    const wrapper = mount(PlayerModal)
    await flushPromises()

    expect(wrapper.find('[data-testid="player-stream-translator"]').exists()).toBe(
      false,
    )
    expect(
      wrapper.find('[data-testid="player-stream-no-translators"]').exists(),
    ).toBe(true)
  })

  it('renders alternative online sources when present', async () => {
    authorise()
    const player = useItemPlayerStore()
    player.itemId = 42
    player.mode = 'stream'
    player.source = 'rezka'
    player.sources = [
      { type: 'Alloha', iframeUrl: 'https://alloha/x' },
      { type: 'Collaps', iframeUrl: 'https://collaps/x' },
    ]
    player.sourcesPageUrl = 'https://fbdomen/x'

    const wrapper = mount(PlayerModal)
    await flushPromises()

    expect(wrapper.find('[data-testid="player-stream-online"]').exists()).toBe(
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
    const player = useItemPlayerStore()
    player.itemId = 42
    player.itemTitle = 'A Movie'
    player.mode = 'stream'
    player.source = 'rezka'
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

    const wrapper = mount(PlayerModal)
    await flushPromises()

    const video = wrapper.find('[data-testid="player-stream-video"]')
    expect(video.exists()).toBe(true)
    const m3u = wrapper.find('[data-testid="player-stream-m3u"]')
    expect(m3u.attributes('href')).toContain('/api/stream_m3u/42?quality=1080p')
    expect(m3u.attributes('href')).toContain('translator=111')
    // Subtitle <track> should reference subtitle_proxy.
    const tracks = wrapper.findAll('track')
    expect(tracks.length).toBe(1)
    expect(tracks[0].attributes('src')).toContain('/api/subtitle_proxy?url=')
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
    const player = useItemPlayerStore()
    player.itemId = 42
    player.mode = 'stream'
    player.source = 'rezka'
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

    const wrapper = mount(PlayerModal)
    await flushPromises()
    const select = wrapper.find(
      '[data-testid="player-stream-translator"]',
    ) as ReturnType<typeof wrapper.find>
    await select.setValue('222')
    await flushPromises()
    expect(player.translatorId).toBe('222')
    expect(
      String(vi.mocked(globalThis.fetch).mock.calls[0][0]),
    ).toContain('translator=222')
  })
})
