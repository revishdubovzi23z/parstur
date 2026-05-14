// ROADMAP Stage 10.7h — AuditPanel modal component tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import AuditPanel from './AuditPanel.vue'
import { useAuditStore, type AuditEntry } from '../stores/audit'
import { useSessionStore } from '../stores/session'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function rebind(overrides?: Partial<AuditEntry>): AuditEntry {
  return {
    id: 10,
    action: 'rebind',
    item_id: 42,
    field: 'kp_id',
    old_value: '111',
    new_value: '222',
    created_at: '2025-05-01 10:00:00',
    undone: 0,
    ...overrides,
  }
}

function category(overrides?: Partial<AuditEntry>): AuditEntry {
  return {
    id: 12,
    action: 'category',
    item_id: 42,
    field: 'category_id',
    old_value: '1',
    new_value: '2',
    created_at: '2025-05-01 11:00:00',
    undone: 0,
    ...overrides,
  }
}

function setup(): { audit: ReturnType<typeof useAuditStore> } {
  setActivePinia(createPinia())
  useSessionStore().$patch({ status: 'disabled' })
  return { audit: useAuditStore() }
}

describe('AuditPanel.vue', () => {
  beforeEach(() => {
    vi.spyOn(globalThis, 'fetch')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('does not render when open=false', () => {
    setup()
    const wrapper = mount(AuditPanel, { props: { open: false } })
    expect(wrapper.find('[data-testid="audit-panel"]').exists()).toBe(false)
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('fetches on open and renders rows + counts', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson([rebind(), category(), rebind({ id: 11, undone: 1 })]),
    )
    setup()
    const wrapper = mount(AuditPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    const rows = wrapper.findAll('[data-testid^="audit-row-"]')
    expect(rows).toHaveLength(3)
    expect(wrapper.find('[data-testid="audit-panel-count"]').text())
      .toMatch(/3 записей · 1 с откатом/)
  })

  it('renders the empty-state when history is empty', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson([]))
    setup()
    const wrapper = mount(AuditPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    expect(wrapper.find('[data-testid="audit-panel-empty"]').exists()).toBe(true)
  })

  it('shows the undo button only for undoable rebind rows', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson([rebind(), rebind({ id: 11, undone: 1 }), category()]),
    )
    setup()
    const wrapper = mount(AuditPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    // rebind id=10 → undo button visible
    expect(wrapper.find('[data-testid="audit-undo-10"]').exists()).toBe(true)
    // rebind id=11 already undone → no undo button, shows "отменено"
    expect(wrapper.find('[data-testid="audit-undo-11"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="audit-undone-11"]').exists()).toBe(true)
    // category → not undoable, no button
    expect(wrapper.find('[data-testid="audit-undo-12"]').exists()).toBe(false)
  })

  it('confirms and POSTs to /undo on click; row turns into "отменено"', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson([rebind()]))
      .mockResolvedValueOnce(mockJson({ status: 'success' }))
      // feed.fetchFeed() trailing call — any payload is fine
      .mockResolvedValueOnce(mockJson({}))
    setup()
    const wrapper = mount(AuditPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    await wrapper.find('[data-testid="audit-undo-10"]').trigger('click')
    await flushPromises()
    expect(window.confirm).toHaveBeenCalled()
    const calls = vi.mocked(globalThis.fetch).mock.calls
    const postCall = calls.find((c) => (c[1] as RequestInit | undefined)?.method === 'POST')
    expect(postCall?.[0]).toBe('/api/audit_log/10/undo')
    // After success the local row should have undone=1 → "отменено" badge
    await flushPromises()
    expect(wrapper.find('[data-testid="audit-undo-10"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="audit-undone-10"]').exists()).toBe(true)
  })

  it('skips undo POST when confirm() returns false', async () => {
    vi.mocked(window.confirm).mockReturnValue(false)
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson([rebind()]))
    setup()
    const wrapper = mount(AuditPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    await wrapper.find('[data-testid="audit-undo-10"]').trigger('click')
    await flushPromises()
    const calls = vi.mocked(globalThis.fetch).mock.calls
    expect(calls.some((c) => (c[1] as RequestInit | undefined)?.method === 'POST')).toBe(false)
  })

  it('toggles between flat and grouped renders', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson([rebind(), category()]),
    )
    setup()
    const wrapper = mount(AuditPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    // Default → flat list exists
    expect(wrapper.find('[data-testid="audit-panel-list"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="audit-group-rebind"]').exists()).toBe(false)
    // Click the group toggle
    await wrapper.find('[data-testid="audit-panel-group-toggle"]').trigger('click')
    expect(wrapper.find('[data-testid="audit-panel-list"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="audit-group-rebind"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="audit-group-category"]').exists()).toBe(true)
  })

  it('emits close when ✕ is clicked', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson([]))
    setup()
    const wrapper = mount(AuditPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    await wrapper.find('[data-testid="audit-panel-close"]').trigger('click')
    expect(wrapper.emitted('close')).toBeTruthy()
  })

  it('surfaces server error on a 5xx refresh', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ error: 'db locked' }, { status: 500 }),
    )
    setup()
    const wrapper = mount(AuditPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    expect(wrapper.find('[data-testid="audit-panel-error"]').text()).toMatch(
      /db locked/,
    )
  })
})
