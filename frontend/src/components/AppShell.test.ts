// Follow-up to ROADMAP 10.7z — AppShell shell-level behaviours.
//
// The legacy `index.html` had a clickable title that reset the user
// back to the main feed (first page, no filters) and an auto-tab
// switch on the logs overlay whenever the user kicked off a parser.
// These tests pin both behaviours for the migrated shell.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import AppShell from './AppShell.vue'
import { useFeedStore } from '../stores/feed'
import { useLogsStore } from '../stores/logs'
import { useSessionStore } from '../stores/session'
import { useVisitStore } from '../stores/visits'

function jsonResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

describe('AppShell.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    useSessionStore().$patch({ status: 'disabled' })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse({ items: [], totalPages: 1, statuses: {}, progress: {} }),
    )
    // Stub scrollTo + WebSocket so jsdom doesn't choke when the
    // shell mounts its modals on first paint.
    Object.defineProperty(window, 'scrollTo', { value: vi.fn(), writable: true })
    class FakeSocket {
      readyState = 0
      onopen: (() => void) | null = null
      onclose: (() => void) | null = null
      onerror: (() => void) | null = null
      onmessage: ((ev: MessageEvent) => void) | null = null
      close(): void {
        this.readyState = 3
      }
      send(): void {}
    }
    // jsdom doesn't ship a WebSocket constructor; the sync store
    // tries to call `new WebSocket(...)` once the shell mounts.
    ;(globalThis as { WebSocket?: unknown }).WebSocket = FakeSocket
    ;(window as unknown as { WebSocket?: unknown }).WebSocket = FakeSocket
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders a clickable "Antigravity Tracker" logo in the header', () => {
    const wrapper = mount(AppShell)
    const logo = wrapper.find('[data-testid="logo-home"]')
    expect(logo.exists()).toBe(true)
    expect(logo.element.tagName).toBe('BUTTON')
    expect(logo.text()).toContain('Antigravity Tracker')
  })

  it('logo click resets filters / page / collection and refetches', async () => {
    const feed = useFeedStore()
    feed.filters.search = 'something'
    feed.filters.minKp = 7
    feed.filters.hideRated = true
    feed.page = 4

    const wrapper = mount(AppShell)
    await flushPromises()

    await wrapper.find('[data-testid="logo-home"]').trigger('click')
    await flushPromises()

    expect(feed.filters.search).toBe('')
    expect(feed.filters.minKp).toBe(0)
    expect(feed.filters.hideRated).toBe(false)
    expect(feed.page).toBe(1)
  })

  it('logo click also clears the "только новое" toggle if it was on', async () => {
    const visits = useVisitStore()
    visits.$patch({ showNewOnly: true, lastVisit: '2025-01-01' })

    const wrapper = mount(AppShell)
    await flushPromises()

    await wrapper.find('[data-testid="logo-home"]').trigger('click')
    await flushPromises()

    expect(visits.showNewOnly).toBe(false)
  })

  it('auto-opens the logs overlay on a sync-started event with the right log type', async () => {
    const logs = useLogsStore()
    const wrapper = mount(AppShell)
    await flushPromises()

    // Emit "started" from the mounted SyncPanel child directly.
    const syncPanel = wrapper.findComponent({ name: 'SyncPanel' })
    expect(syncPanel.exists()).toBe(true)
    syncPanel.vm.$emit('started', 'sync_video')
    await flushPromises()

    expect(logs.selectedType).toBe('video')
    expect(logs.userSelected).toBe(true)
    // The overlay is now mounted with open=true.
    const logsPanel = wrapper.findComponent({ name: 'LogsPanel' })
    expect(logsPanel.props('open')).toBe(true)
  })

  it('ignores unknown process keys and does not open the logs overlay', async () => {
    const wrapper = mount(AppShell)
    await flushPromises()
    const syncPanel = wrapper.findComponent({ name: 'SyncPanel' })

    syncPanel.vm.$emit('started', 'some-unknown-key')
    await flushPromises()

    const logsPanel = wrapper.findComponent({ name: 'LogsPanel' })
    expect(logsPanel.props('open')).toBe(false)
  })
})
