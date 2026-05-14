// ROADMAP Stage 10.7d — SyncPanel modal component tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import SyncPanel from './SyncPanel.vue'
import { useSyncStore } from '../stores/sync'
import { useSessionStore } from '../stores/session'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function setup(): {
  sync: ReturnType<typeof useSyncStore>
} {
  setActivePinia(createPinia())
  useSessionStore().$patch({ status: 'disabled' })
  return { sync: useSyncStore() }
}

describe('SyncPanel.vue', () => {
  beforeEach(() => {
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('does not render when open=false', () => {
    setup()
    const wrapper = mount(SyncPanel, { props: { open: false } })
    expect(wrapper.find('[data-testid="sync-panel"]').exists()).toBe(false)
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('triggers fetchStatus on open and renders one row per control', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ statuses: { sync_video: 'idle' }, progress: {} }),
    )
    setup()
    const wrapper = mount(SyncPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/process_status',
      expect.any(Object),
    )
    const rows = wrapper.findAll('[data-testid^="sync-row-"]')
    expect(rows.length).toBeGreaterThanOrEqual(9)
  })

  it('shows "Стоп" instead of "Старт" when a process is running, and stop posts /api/stop/{key}', async () => {
    const { sync } = setup()
    sync.statuses.sync_video = 'running'
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'stopped' }),
    )
    const wrapper = mount(SyncPanel, { props: { open: true } })
    await flushPromises()

    expect(wrapper.find('[data-testid="sync-stop-sync_video"]').exists()).toBe(
      true,
    )
    expect(wrapper.find('[data-testid="sync-start-sync_video"]').exists()).toBe(
      false,
    )
    await wrapper.find('[data-testid="sync-stop-sync_video"]').trigger('click')
    await flushPromises()
    expect(
      vi.mocked(globalThis.fetch).mock.calls.map((c) => String(c[0])),
    ).toContain('/api/stop/sync_video')
  })

  it('disables Start buttons when another process is busy', async () => {
    const { sync } = setup()
    sync.statuses.cleanup = 'running'
    const wrapper = mount(SyncPanel, { props: { open: true } })
    await flushPromises()
    const cleanupStop = wrapper.find('[data-testid="sync-stop-cleanup"]')
    expect(cleanupStop.exists()).toBe(true)
    const otherStart = wrapper.find('[data-testid="sync-start-sync_video"]')
    expect(otherStart.attributes('disabled')).toBeDefined()
  })

  it('calls startCleanup when clicking the cleanup Start button', async () => {
    setup()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'started' }),
    )
    const wrapper = mount(SyncPanel, { props: { open: true } })
    await flushPromises()
    await wrapper.find('[data-testid="sync-start-cleanup"]').trigger('click')
    await flushPromises()
    expect(
      vi.mocked(globalThis.fetch).mock.calls.map((c) => String(c[0])),
    ).toContain('/api/start_cleanup')
  })

  it('renders the progress bar text when progress > 0', async () => {
    const { sync } = setup()
    sync.progress.sync_video = { current: 3, total: 10 }
    const wrapper = mount(SyncPanel, { props: { open: true } })
    await flushPromises()
    const progress = wrapper.find('[data-testid="sync-progress-sync_video"]')
    expect(progress.exists()).toBe(true)
    expect(progress.text()).toContain('3 / 10')
  })

  it('emits close on backdrop click and × button', async () => {
    setup()
    const wrapper = mount(SyncPanel, { props: { open: true } })
    await flushPromises()
    await wrapper.find('[data-testid="sync-panel-backdrop"]').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
    await wrapper.find('[data-testid="sync-panel-close"]').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(2)
  })

  it('reflects the rezka_session badge state', async () => {
    const { sync } = setup()
    sync.rezkaSession = 'up'
    const wrapper = mount(SyncPanel, { props: { open: true } })
    await flushPromises()
    expect(wrapper.find('[data-testid="sync-panel-rezka-badge"]').text()).toMatch(
      /\u043e\u043d\u043b\u0430\u0439\u043d/i,
    )
  })

  it('seeds min_year/max_year inputs with 2023 .. currentYear+1 defaults', async () => {
    setup()
    const wrapper = mount(SyncPanel, { props: { open: true } })
    await flushPromises()
    const min = wrapper.find('[data-testid="sync-filter-min-year"]')
      .element as HTMLInputElement
    const max = wrapper.find('[data-testid="sync-filter-max-year"]')
      .element as HTMLInputElement
    expect(min.value).toBe('2023')
    expect(max.value).toBe(String(new Date().getFullYear() + 1))
  })

  it('emits "started" with the process key after a successful start_*', async () => {
    setup()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'started' }),
    )
    const wrapper = mount(SyncPanel, { props: { open: true } })
    await flushPromises()
    await wrapper.find('[data-testid="sync-start-sync_video"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('started')).toEqual([['sync_video']])
  })

  it('does not emit "started" if the start endpoint fails', async () => {
    setup()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ detail: 'Другой процесс уже запущен' }, { status: 400 }),
    )
    const wrapper = mount(SyncPanel, { props: { open: true } })
    await flushPromises()
    await wrapper.find('[data-testid="sync-start-cleanup"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('started')).toBeUndefined()
  })

  it('toggles the WS badge label based on wsConnected', async () => {
    const { sync } = setup()
    sync.wsConnected = true
    const wrapper = mount(SyncPanel, { props: { open: true } })
    await flushPromises()
    expect(wrapper.find('[data-testid="sync-panel-ws-badge"]').text()).toContain(
      'live',
    )
    sync.wsConnected = false
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="sync-panel-ws-badge"]').text()).toContain(
      'polling',
    )
  })

  it('passes filter inputs through to /api/start_sync_video', async () => {
    setup()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'started' }),
    )
    const wrapper = mount(SyncPanel, { props: { open: true } })
    await flushPromises()
    await wrapper.find('[data-testid="sync-filter-min-year"]').setValue('2010')
    await wrapper.find('[data-testid="sync-filter-max-year"]').setValue('2024')
    await wrapper
      .find('[data-testid="sync-filter-min-date"]')
      .setValue('2024-06-01')
    await wrapper.find('[data-testid="sync-start-sync_video"]').trigger('click')
    await flushPromises()
    const call = vi
      .mocked(globalThis.fetch)
      .mock.calls.find((c) => String(c[0]).startsWith('/api/start_sync_video'))
    expect(call).toBeDefined()
    expect(String(call![0])).toContain('min_year=2010')
    expect(String(call![0])).toContain('max_year=2024')
    expect(String(call![0])).toContain('min_date=2024-06-01')
  })

  it('renders sync.lastError when present', async () => {
    const { sync } = setup()
    sync.lastError = 'Cannot start: busy'
    const wrapper = mount(SyncPanel, { props: { open: true } })
    await flushPromises()
    expect(wrapper.find('[data-testid="sync-panel-error"]').text()).toContain(
      'Cannot start: busy',
    )
  })

  it('forwards force=true to start_reprocess when the checkbox is on', async () => {
    setup()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'started' }),
    )
    const wrapper = mount(SyncPanel, { props: { open: true } })
    await flushPromises()
    await wrapper
      .find('[data-testid="sync-reprocess-force"]')
      .setValue(true)
    await wrapper.find('[data-testid="sync-start-reprocess"]').trigger('click')
    await flushPromises()
    const call = vi
      .mocked(globalThis.fetch)
      .mock.calls.find((c) => String(c[0]).startsWith('/api/start_reprocess'))
    expect(call).toBeDefined()
    expect(String(call![0])).toContain('force=true')
  })

  it('omits force=true on reprocess when the checkbox is off', async () => {
    setup()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'started' }),
    )
    const wrapper = mount(SyncPanel, { props: { open: true } })
    await flushPromises()
    await wrapper.find('[data-testid="sync-start-reprocess"]').trigger('click')
    await flushPromises()
    const call = vi
      .mocked(globalThis.fetch)
      .mock.calls.find((c) => String(c[0]).startsWith('/api/start_reprocess'))
    expect(call).toBeDefined()
    expect(String(call![0])).not.toContain('force=true')
  })

  it('exposes the user-CSV sync control and POSTs /api/sync_user', async () => {
    setup()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'started' }),
    )
    const wrapper = mount(SyncPanel, { props: { open: true } })
    await flushPromises()
    expect(wrapper.find('[data-testid="sync-row-user"]').exists()).toBe(true)
    await wrapper.find('[data-testid="sync-start-user"]').trigger('click')
    await flushPromises()
    const call = vi
      .mocked(globalThis.fetch)
      .mock.calls.find((c) => String(c[0]) === '/api/sync_user')
    expect(call).toBeDefined()
    expect((call![1] as RequestInit).method).toBe('POST')
  })
})
