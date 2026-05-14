// ROADMAP Stage 10.7h — filter-rules store tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useRulesStore, type FilterRule } from './rules'
import { useSessionStore } from './session'
import { useToastStore } from './toast'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function authDisabled(): void {
  useSessionStore().$patch({ status: 'disabled' })
}

const RULE_TITLE: FilterRule = {
  id: 1,
  name: 'Hide reality',
  field: 'title',
  pattern: 'reality',
  action: 'hide',
  enabled: true,
}

const RULE_DESC: FilterRule = {
  id: 2,
  name: 'Highlight DC',
  field: 'description',
  pattern: '\\bDC\\b',
  action: 'highlight',
  enabled: false,
}

describe('useRulesStore — refresh()', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('is a no-op when the session cannot call the API', async () => {
    useSessionStore().$patch({ status: 'unauthenticated' })
    const rules = useRulesStore()
    await rules.refresh()
    expect(globalThis.fetch).not.toHaveBeenCalled()
    expect(rules.rules).toEqual([])
  })

  it('loads the rule list from /api/filter_rules', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson([RULE_TITLE, RULE_DESC]),
    )
    const rules = useRulesStore()
    await rules.refresh()
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/filter_rules',
      expect.anything(),
    )
    expect(rules.rules).toHaveLength(2)
    expect(rules.enabledCount).toBe(1)
    expect(rules.error).toBeNull()
    expect(rules.loading).toBe(false)
  })

  it('tolerates an empty array', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson([]))
    const rules = useRulesStore()
    await rules.refresh()
    expect(rules.rules).toEqual([])
    expect(rules.error).toBeNull()
  })

  it('records server error string from a 5xx body', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ error: 'db locked' }, { status: 503 }),
    )
    const rules = useRulesStore()
    await rules.refresh()
    expect(rules.error).toBe('db locked')
    expect(rules.rules).toEqual([])
  })

  it('falls back to HTTP <status> when body has no error key', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('boom', { status: 500 }),
    )
    const rules = useRulesStore()
    await rules.refresh()
    expect(rules.error).toBe('HTTP 500')
  })
})

describe('useRulesStore — create()', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(globalThis, 'fetch')
    authDisabled()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('POSTs the new rule and refreshes the list on success', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson({ id: 7 })) // POST
      .mockResolvedValueOnce(mockJson([{ ...RULE_TITLE, id: 7 }])) // refresh
    const rules = useRulesStore()
    const id = await rules.create({
      name: 'Hide reality',
      field: 'title',
      pattern: 'reality',
      action: 'hide',
    })
    expect(id).toBe(7)
    const calls = vi.mocked(globalThis.fetch).mock.calls
    expect(calls[0][0]).toBe('/api/filter_rules')
    expect(calls[0][1]).toMatchObject({ method: 'POST' })
    const body = JSON.parse(String(calls[0][1]?.body ?? '{}'))
    expect(body).toMatchObject({
      name: 'Hide reality',
      field: 'title',
      action: 'hide',
    })
    expect(rules.rules).toHaveLength(1)
    expect(useToastStore().current?.tone).toBe('success')
  })

  it('surfaces 400 error from the server and skips refresh', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ error: 'bad regex' }, { status: 400 }),
    )
    const rules = useRulesStore()
    const id = await rules.create({
      name: 'x',
      field: 'title',
      pattern: '(',
      action: 'hide',
    })
    expect(id).toBeNull()
    expect(rules.error).toBe('bad regex')
    expect(vi.mocked(globalThis.fetch).mock.calls).toHaveLength(1)
    expect(useToastStore().current?.tone).toBe('error')
  })
})

describe('useRulesStore — update() and toggle()', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(globalThis, 'fetch')
    authDisabled()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('PUTs a partial patch and refreshes', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson({ status: 'success' })) // PUT
      .mockResolvedValueOnce(mockJson([{ ...RULE_TITLE, name: 'renamed' }]))
    const rules = useRulesStore()
    const ok = await rules.update(1, { name: 'renamed' })
    expect(ok).toBe(true)
    const calls = vi.mocked(globalThis.fetch).mock.calls
    expect(calls[0][0]).toBe('/api/filter_rules/1')
    expect(calls[0][1]).toMatchObject({ method: 'PUT' })
    const body = JSON.parse(String(calls[0][1]?.body ?? '{}'))
    expect(body).toEqual({ name: 'renamed' })
    expect(rules.rules[0]?.name).toBe('renamed')
  })

  it('returns false and toasts on 404', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ error: 'rule not found' }, { status: 404 }),
    )
    const rules = useRulesStore()
    const ok = await rules.update(99, { enabled: true })
    expect(ok).toBe(false)
    expect(rules.error).toBe('rule not found')
    expect(useToastStore().current?.tone).toBe('error')
  })

  it('toggle() flips the enabled flag and forwards through update', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson({ status: 'success' })) // PUT
      .mockResolvedValueOnce(mockJson([{ ...RULE_TITLE, enabled: false }]))
    const rules = useRulesStore()
    const ok = await rules.toggle(RULE_TITLE)
    expect(ok).toBe(true)
    const calls = vi.mocked(globalThis.fetch).mock.calls
    const body = JSON.parse(String(calls[0][1]?.body ?? '{}'))
    expect(body).toEqual({ enabled: false })
  })
})

describe('useRulesStore — remove()', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(globalThis, 'fetch')
    authDisabled()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('DELETEs and refreshes on success', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson({ status: 'success' })) // DELETE
      .mockResolvedValueOnce(mockJson([])) // refresh
    const rules = useRulesStore()
    rules.rules = [RULE_TITLE]
    const ok = await rules.remove(1)
    expect(ok).toBe(true)
    expect(rules.rules).toEqual([])
    const calls = vi.mocked(globalThis.fetch).mock.calls
    expect(calls[0][0]).toBe('/api/filter_rules/1')
    expect(calls[0][1]).toMatchObject({ method: 'DELETE' })
    expect(useToastStore().current?.tone).toBe('success')
  })

  it('returns false on 404 and keeps local state', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ error: 'rule not found' }, { status: 404 }),
    )
    const rules = useRulesStore()
    rules.rules = [RULE_TITLE]
    const ok = await rules.remove(1)
    expect(ok).toBe(false)
    expect(rules.rules).toEqual([RULE_TITLE])
    expect(rules.error).toBe('rule not found')
  })

  it('returns false and toasts on network failure', async () => {
    vi.mocked(globalThis.fetch).mockRejectedValueOnce(new Error('offline'))
    const rules = useRulesStore()
    const ok = await rules.remove(1)
    expect(ok).toBe(false)
    expect(rules.error).toBe('offline')
    expect(useToastStore().current?.tone).toBe('error')
  })
})
