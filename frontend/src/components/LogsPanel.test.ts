// ROADMAP Stage 10.7e — LogsPanel modal component tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import LogsPanel from './LogsPanel.vue'
import { useLogsStore } from '../stores/logs'
import { useSessionStore } from '../stores/session'
import { useSyncStore } from '../stores/sync'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function setup(): { logs: ReturnType<typeof useLogsStore>; sync: ReturnType<typeof useSyncStore> } {
  setActivePinia(createPinia())
  useSessionStore().$patch({ status: 'disabled' })
  return { logs: useLogsStore(), sync: useSyncStore() }
}

describe('LogsPanel.vue', () => {
  beforeEach(() => {
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    try {
      useLogsStore().close()
    } catch {
      /* noop */
    }
    vi.restoreAllMocks()
  })

  it('does not render when open=false', () => {
    setup()
    const wrapper = mount(LogsPanel, { props: { open: false } })
    expect(wrapper.find('[data-testid="logs-panel"]').exists()).toBe(false)
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('triggers refresh on open via store.open()', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ log: 'hello' }),
    )
    setup()
    const wrapper = mount(LogsPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    expect(globalThis.fetch).toHaveBeenCalled()
    expect(wrapper.find('[data-testid="logs-body"]').text()).toContain('hello')
  })

  it('renders one tab per LOG_TYPE_LABELS entry', async () => {
    setup()
    const wrapper = mount(LogsPanel, { props: { open: true } })
    await flushPromises()
    const tabs = wrapper.findAll('[data-testid^="logs-tab-"]')
    expect(tabs).toHaveLength(15)
    expect(wrapper.find('[data-testid="logs-tab-kinopub"]').exists()).toBe(true)
    expect(
      wrapper.find('[data-testid="logs-tab-kinopub_collections"]').exists(),
    ).toBe(true)
  })

  it('switches selectedType when a tab is clicked', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson({ log: 'one' }))
      .mockResolvedValueOnce(mockJson({ log: 'two' }))
    const { logs } = setup()
    const wrapper = mount(LogsPanel, { props: { open: true } })
    await flushPromises()
    await wrapper.find('[data-testid="logs-tab-cleanup"]').trigger('click')
    await flushPromises()
    expect(logs.selectedType).toBe('cleanup')
    expect(logs.userSelected).toBe(true)
  })

  it('shows the Stop button only when the current process is running', async () => {
    const { sync } = setup()
    sync.statuses.reprocess = 'running'
    const wrapper = mount(LogsPanel, { props: { open: true } })
    await flushPromises()
    expect(wrapper.find('[data-testid="logs-stop"]').exists()).toBe(true)
    sync.statuses.reprocess = 'idle'
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="logs-stop"]').exists()).toBe(false)
  })

  it('calls store.clear() when the clear button is clicked', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson({ log: '' })) // initial refresh
      .mockResolvedValueOnce(mockJson({ status: 'success' })) // clear
      .mockResolvedValueOnce(mockJson({ log: '' })) // re-refresh
    setup()
    const wrapper = mount(LogsPanel, { props: { open: true } })
    await flushPromises()
    await wrapper.find('[data-testid="logs-clear"]').trigger('click')
    await flushPromises()
    expect(
      vi.mocked(globalThis.fetch).mock.calls.map((c) => String(c[0])),
    ).toContain('/api/clear_log?log_type=reprocess')
  })

  it('emits close on the × button', async () => {
    setup()
    const wrapper = mount(LogsPanel, { props: { open: true } })
    await flushPromises()
    await wrapper.find('[data-testid="logs-close"]').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('renders the on-disk filename for the active tab', async () => {
    setup()
    const wrapper = mount(LogsPanel, { props: { open: true } })
    await flushPromises()
    expect(wrapper.find('[data-testid="logs-filename"]').text()).toBe(
      'reprocess_log.txt',
    )
  })

  it('shows error text when the store has an error', async () => {
    const { logs } = setup()
    logs.error = 'connection refused'
    const wrapper = mount(LogsPanel, { props: { open: true } })
    await flushPromises()
    expect(wrapper.find('[data-testid="logs-error"]').text()).toContain(
      'connection refused',
    )
  })

  it('falls back to "Пусто" placeholder when content is empty', async () => {
    setup()
    const wrapper = mount(LogsPanel, { props: { open: true } })
    await flushPromises()
    expect(wrapper.find('[data-testid="logs-body"]').text()).toContain('Пусто')
  })
})
