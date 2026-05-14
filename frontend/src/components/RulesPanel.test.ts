// ROADMAP Stage 10.7h — RulesPanel modal component tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import RulesPanel from './RulesPanel.vue'
import { useRulesStore, type FilterRule } from '../stores/rules'
import { useSessionStore } from '../stores/session'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function rule(overrides?: Partial<FilterRule>): FilterRule {
  return {
    id: 1,
    name: 'Hide reality',
    field: 'title',
    pattern: 'reality',
    action: 'hide',
    enabled: true,
    ...overrides,
  }
}

function setup(): { rules: ReturnType<typeof useRulesStore> } {
  setActivePinia(createPinia())
  useSessionStore().$patch({ status: 'disabled' })
  return { rules: useRulesStore() }
}

describe('RulesPanel.vue', () => {
  beforeEach(() => {
    vi.spyOn(globalThis, 'fetch')
    vi.spyOn(window, 'confirm').mockReturnValue(true)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('does not render when open=false', () => {
    setup()
    const wrapper = mount(RulesPanel, { props: { open: false } })
    expect(wrapper.find('[data-testid="rules-panel"]').exists()).toBe(false)
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('fetches rules on open and renders rows + count', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson([rule(), rule({ id: 2, name: 'Highlight DC', enabled: false })]),
    )
    setup()
    const wrapper = mount(RulesPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    const rows = wrapper.findAll('[data-testid^="rules-row-"]')
    expect(rows).toHaveLength(2)
    expect(wrapper.find('[data-testid="rules-panel-count"]').text())
      .toMatch(/1\s*\/\s*2/)
  })

  it('renders the empty-state when no rules exist', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson([]))
    setup()
    const wrapper = mount(RulesPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    expect(wrapper.find('[data-testid="rules-panel-empty"]').exists()).toBe(true)
  })

  it('emits close when the backdrop or close button is clicked', async () => {
    setup()
    const wrapper = mount(RulesPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    await wrapper.find('[data-testid="rules-panel-close"]').trigger('click')
    expect(wrapper.emitted('close')).toBeTruthy()
  })

  it('validates required fields before submitting a new rule', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson([]))
    setup()
    const wrapper = mount(RulesPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    // Hit submit without typing anything.
    await wrapper.find('[data-testid="rules-new-submit"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="rules-new-error"]').text()).toMatch(
      /Имя и regex обязательны/,
    )
    // Only the initial refresh should have happened (no POST).
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
  })

  it('POSTs the new rule and resets the form on success', async () => {
    vi.mocked(globalThis.fetch)
      // initial refresh on open
      .mockResolvedValueOnce(mockJson([]))
      // POST /api/filter_rules
      .mockResolvedValueOnce(mockJson({ id: 99 }))
      // refresh after success
      .mockResolvedValueOnce(mockJson([rule({ id: 99 })]))
      // optional feed.fetchFeed() — return any payload
      .mockResolvedValueOnce(mockJson({}))
    setup()
    const wrapper = mount(RulesPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    await wrapper.find('[data-testid="rules-new-name"]').setValue('Hide reality')
    await wrapper.find('[data-testid="rules-new-pattern"]').setValue('reality')
    await wrapper.find('[data-testid="rules-new-submit"]').trigger('click')
    await flushPromises()
    const calls = vi.mocked(globalThis.fetch).mock.calls
    const postCall = calls.find((c) => (c[1] as RequestInit | undefined)?.method === 'POST')
    expect(postCall?.[0]).toBe('/api/filter_rules')
    const body = JSON.parse(String((postCall?.[1] as RequestInit | undefined)?.body ?? '{}'))
    expect(body).toMatchObject({
      name: 'Hide reality',
      field: 'title',
      pattern: 'reality',
      action: 'hide',
      enabled: true,
    })
    // Form fields were reset.
    const nameInput = wrapper.find<HTMLInputElement>('[data-testid="rules-new-name"]')
    expect(nameInput.element.value).toBe('')
  })

  it('toggles a rule via the enable checkbox', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson([rule()]))
      .mockResolvedValueOnce(mockJson({ status: 'success' }))
      .mockResolvedValueOnce(mockJson([rule({ enabled: false })]))
      .mockResolvedValueOnce(mockJson({}))
    setup()
    const wrapper = mount(RulesPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    await wrapper.find('[data-testid="rules-toggle-1"]').trigger('change')
    await flushPromises()
    const calls = vi.mocked(globalThis.fetch).mock.calls
    const putCall = calls.find((c) => (c[1] as RequestInit | undefined)?.method === 'PUT')
    expect(putCall?.[0]).toBe('/api/filter_rules/1')
    const body = JSON.parse(String((putCall?.[1] as RequestInit | undefined)?.body ?? '{}'))
    expect(body).toEqual({ enabled: false })
  })

  it('confirms before delete and DELETEs on confirmation', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson([rule()]))
      .mockResolvedValueOnce(mockJson({ status: 'success' }))
      .mockResolvedValueOnce(mockJson([]))
      .mockResolvedValueOnce(mockJson({}))
    setup()
    const wrapper = mount(RulesPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    await wrapper.find('[data-testid="rules-delete-1"]').trigger('click')
    await flushPromises()
    expect(window.confirm).toHaveBeenCalled()
    const calls = vi.mocked(globalThis.fetch).mock.calls
    const delCall = calls.find((c) => (c[1] as RequestInit | undefined)?.method === 'DELETE')
    expect(delCall?.[0]).toBe('/api/filter_rules/1')
  })

  it('skips DELETE if confirm() returns false', async () => {
    vi.mocked(window.confirm).mockReturnValue(false)
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson([rule()]))
    setup()
    const wrapper = mount(RulesPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    await wrapper.find('[data-testid="rules-delete-1"]').trigger('click')
    await flushPromises()
    const calls = vi.mocked(globalThis.fetch).mock.calls
    expect(calls.some((c) => (c[1] as RequestInit | undefined)?.method === 'DELETE')).toBe(false)
  })

  it('opens inline edit form on ✎ and PUTs patch on save', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson([rule()]))
      .mockResolvedValueOnce(mockJson({ status: 'success' }))
      .mockResolvedValueOnce(mockJson([rule({ name: 'renamed' })]))
      .mockResolvedValueOnce(mockJson({}))
    setup()
    const wrapper = mount(RulesPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    await wrapper.find('[data-testid="rules-edit-1"]').trigger('click')
    expect(wrapper.find('[data-testid="rules-edit-name-1"]').exists()).toBe(true)
    await wrapper.find('[data-testid="rules-edit-name-1"]').setValue('renamed')
    await wrapper.find('[data-testid="rules-edit-save-1"]').trigger('click')
    await flushPromises()
    const calls = vi.mocked(globalThis.fetch).mock.calls
    const putCall = calls.find((c) => (c[1] as RequestInit | undefined)?.method === 'PUT')
    expect(putCall?.[0]).toBe('/api/filter_rules/1')
    const body = JSON.parse(String((putCall?.[1] as RequestInit | undefined)?.body ?? '{}'))
    expect(body).toMatchObject({ name: 'renamed' })
  })
})
