// ROADMAP Stage 10.7h — audit-log store tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  isUndoable,
  useAuditStore,
  type AuditEntry,
} from './audit'
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

// NOTE: these are factory functions, not constants, because Pinia's
// reactive proxies otherwise mutate the originals across tests when
// an action writes back to a row (e.g. `undone = 1` in undo()).
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

function rebindUndone(overrides?: Partial<AuditEntry>): AuditEntry {
  return rebind({ id: 11, undone: 1, ...overrides })
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

describe('useAuditStore — refresh()', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('is a no-op when the session cannot call the API', async () => {
    useSessionStore().$patch({ status: 'unauthenticated' })
    const audit = useAuditStore()
    await audit.refresh()
    expect(globalThis.fetch).not.toHaveBeenCalled()
    expect(audit.entries).toEqual([])
  })

  it('loads the tail and applies the default limit', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson([rebind(), category()]),
    )
    const audit = useAuditStore()
    await audit.refresh()
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/audit_log?limit=50',
      expect.anything(),
    )
    expect(audit.entries).toHaveLength(2)
    expect(audit.error).toBeNull()
  })

  it('forwards a custom limit and remembers it', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValue(mockJson([]))
    const audit = useAuditStore()
    await audit.refresh(200)
    expect(globalThis.fetch).toHaveBeenLastCalledWith(
      '/api/audit_log?limit=200',
      expect.anything(),
    )
    expect(audit.limit).toBe(200)
    await audit.refresh()
    expect(globalThis.fetch).toHaveBeenLastCalledWith(
      '/api/audit_log?limit=200',
      expect.anything(),
    )
  })

  it('records 500 error', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ error: 'db locked' }, { status: 500 }),
    )
    const audit = useAuditStore()
    await audit.refresh()
    expect(audit.error).toBe('db locked')
    expect(audit.entries).toEqual([])
  })

  it('tolerates an empty list', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson([]))
    const audit = useAuditStore()
    await audit.refresh()
    expect(audit.entries).toEqual([])
    expect(audit.error).toBeNull()
  })
})

describe('useAuditStore — undo()', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(globalThis, 'fetch')
    authDisabled()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('POSTs to /undo and marks the row locally on success', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'success' }),
    )
    const audit = useAuditStore()
    audit.entries = [rebind(), category()]
    const ok = await audit.undo(10)
    expect(ok).toBe(true)
    const calls = vi.mocked(globalThis.fetch).mock.calls
    expect(calls[0][0]).toBe('/api/audit_log/10/undo')
    expect(calls[0][1]).toMatchObject({ method: 'POST' })
    const target = audit.entries.find((e) => e.id === 10)
    expect(target?.undone).toBe(1)
    expect(useToastStore().current?.tone).toBe('success')
  })

  it('returns false and surfaces error on 400 (undo not supported)', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson(
        { error: 'undo not supported for action: category' },
        { status: 400 },
      ),
    )
    const audit = useAuditStore()
    audit.entries = [category()]
    const ok = await audit.undo(12)
    expect(ok).toBe(false)
    expect(audit.error).toMatch(/undo not supported/)
    expect(audit.entries[0]?.undone).toBe(0)
    expect(useToastStore().current?.tone).toBe('error')
  })

  it('returns false on 404 and keeps state untouched', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ error: 'audit entry not found' }, { status: 404 }),
    )
    const audit = useAuditStore()
    const ok = await audit.undo(999)
    expect(ok).toBe(false)
    expect(audit.error).toBe('audit entry not found')
  })

  it('returns false on network failure', async () => {
    vi.mocked(globalThis.fetch).mockRejectedValueOnce(new Error('offline'))
    const audit = useAuditStore()
    const ok = await audit.undo(10)
    expect(ok).toBe(false)
    expect(audit.error).toBe('offline')
  })
})

describe('useAuditStore — groupedByAction + isUndoable', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('groups entries by action, preserving original order in buckets', () => {
    const audit = useAuditStore()
    audit.entries = [rebind(), category(), rebindUndone()]
    const groups = audit.groupedByAction
    expect(Object.keys(groups)).toEqual(['rebind', 'category'])
    expect(groups.rebind?.map((e) => e.id)).toEqual([10, 11])
    expect(groups.category?.map((e) => e.id)).toEqual([12])
  })

  it('undoableCount only counts unsupported actions out', () => {
    const audit = useAuditStore()
    audit.entries = [rebind(), rebindUndone(), category()]
    // rebind() is undoable; rebindUndone() has undone=1; category()
    // has unsupported action.
    expect(audit.undoableCount).toBe(1)
  })

  it('isUndoable returns false for already-undone or unsupported rows', () => {
    expect(isUndoable(rebind())).toBe(true)
    expect(isUndoable(rebindUndone())).toBe(false)
    expect(isUndoable(category())).toBe(false)
  })

  it('treats undone as a boolean true the same as 1', () => {
    expect(isUndoable(rebind({ undone: true }))).toBe(false)
  })
})
