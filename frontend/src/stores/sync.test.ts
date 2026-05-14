// ROADMAP Stage 10.7d — sync/cleanup/full-pipeline + WS store tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  PROCESS_KEYS,
  buildSyncQuery,
  useSyncStore,
  type ProcessKey,
} from './sync'
import { useItemsStore } from './items'
import { useLogsStore } from './logs'
import { useSessionStore } from './session'
import { useToastStore } from './toast'
import { AUTH_TOKEN_STORAGE_KEY } from '../api/client'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function setupAuthDisabled(): void {
  const session = useSessionStore()
  session.$patch({ status: 'disabled' })
}

function setupAuthenticated(token = 'tok-abc'): void {
  window.sessionStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token)
  const session = useSessionStore()
  session.$patch({ status: 'authenticated', token })
}

/**
 * Minimal WebSocket double that captures the most recent instance so
 * tests can drive `onopen` / `onmessage` / `onclose` manually. happy-dom
 * does not ship a usable WebSocket, so we have to provide one.
 */
class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

  readyState = FakeWebSocket.CONNECTING
  url: string
  onopen: ((ev?: Event) => void) | null = null
  onclose: ((ev?: CloseEvent) => void) | null = null
  onerror: ((ev?: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  closed = false

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  close(): void {
    this.closed = true
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.(new CloseEvent('close'))
  }

  // Helpers used by tests.
  emitOpen(): void {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.(new Event('open'))
  }

  emitMessage(data: unknown): void {
    const raw = typeof data === 'string' ? data : JSON.stringify(data)
    this.onmessage?.(new MessageEvent('message', { data: raw }))
  }

  emitClose(): void {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.(new CloseEvent('close'))
  }
}

describe('buildSyncQuery', () => {
  it('returns empty string when nothing is set', () => {
    expect(buildSyncQuery({})).toBe('')
  })

  it('includes only the populated fields', () => {
    expect(
      buildSyncQuery({ minYear: 2010, maxYear: null, minDate: null }),
    ).toBe('?min_year=2010')
    expect(
      buildSyncQuery({ minYear: 2010, maxYear: 2020, minDate: '2024-01-01' }),
    ).toBe('?min_year=2010&max_year=2020&min_date=2024-01-01')
  })

  it('encodes minDate', () => {
    expect(buildSyncQuery({ minDate: '2024 01 01' })).toContain('%20')
  })
})

describe('useSyncStore — message handling', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
  })

  it('initialises with idle statuses for every PROCESS_KEY', () => {
    const sync = useSyncStore()
    for (const key of PROCESS_KEYS) {
      expect(sync.statuses[key]).toBe('idle')
      expect(sync.progress[key]).toEqual({ current: 0, total: 0 })
    }
  })

  it('applies a single status update', () => {
    const sync = useSyncStore()
    sync.handleMessage(
      JSON.stringify({ type: 'status', key: 'sync_video', value: 'running' }),
    )
    expect(sync.statuses.sync_video).toBe('running')
    expect(sync.isRunning.sync_video).toBe(true)
    expect(sync.anyBusy).toBe(true)
  })

  it('applies a full status snapshot + progress + rezka_session', () => {
    const sync = useSyncStore()
    sync.handleMessage(
      JSON.stringify({
        type: 'status',
        statuses: { sync_video: 'running', cleanup: 'idle' },
        progress: { sync_video: { current: 5, total: 10 } },
        rezka_session: 'up',
      }),
    )
    expect(sync.statuses.sync_video).toBe('running')
    expect(sync.statuses.cleanup).toBe('idle')
    expect(sync.progress.sync_video).toEqual({ current: 5, total: 10 })
    expect(sync.rezkaSession).toBe('up')
  })

  it('rejects unknown process keys without throwing', () => {
    const sync = useSyncStore()
    sync.handleMessage(
      JSON.stringify({
        type: 'status',
        statuses: { not_a_real_key: 'running' },
      }),
    )
    expect(sync.anyBusy).toBe(false)
  })

  it('applies a progress message (per-key + full snapshot)', () => {
    const sync = useSyncStore()
    sync.handleMessage(
      JSON.stringify({ type: 'progress', key: 'cleanup', current: 3, total: 4 }),
    )
    expect(sync.progress.cleanup).toEqual({ current: 3, total: 4 })

    sync.handleMessage(
      JSON.stringify({
        type: 'progress',
        progress: { rezka: { current: 1, total: 2 } },
      }),
    )
    expect(sync.progress.rezka).toEqual({ current: 1, total: 2 })
  })

  it('updates rezkaSession on a standalone rezka_session message', () => {
    const sync = useSyncStore()
    sync.handleMessage(JSON.stringify({ type: 'rezka_session', state: 'connecting' }))
    expect(sync.rezkaSession).toBe('connecting')
  })

  it('surfaces rezka_sync_error as a toast', () => {
    const sync = useSyncStore()
    const toast = useToastStore()
    sync.handleMessage(
      JSON.stringify({ type: 'rezka_sync_error', message: 'oops' }),
    )
    expect(toast.current?.message).toBe('oops')
    expect(toast.current?.tone).toBe('error')
  })

  it('ignores malformed JSON', () => {
    const sync = useSyncStore()
    expect(() => sync.handleMessage('{ not json')).not.toThrow()
  })

  it('forwards `log` messages to the logs store appendChunk', () => {
    const sync = useSyncStore()
    const logs = useLogsStore()
    logs.selectedType = 'video'
    logs.content = '[start]'
    sync.handleMessage(
      JSON.stringify({ type: 'log', key: 'sync_video', data: 'chunk\n' }),
    )
    expect(logs.content).toBe('[start]chunk\n')
  })

  it('autoFollows running process into the logs store on status snapshot', () => {
    const sync = useSyncStore()
    const logs = useLogsStore()
    expect(logs.selectedType).toBe('reprocess')
    sync.handleMessage(
      JSON.stringify({
        type: 'status',
        statuses: { sync_video: 'running' },
      }),
    )
    expect(logs.selectedType).toBe('video')
  })

  it('forwards single_update terminal transitions to the items store', () => {
    const sync = useSyncStore()
    // First flip single_update to running.
    sync.applyStatusMessage({
      type: 'status',
      key: 'single_update',
      value: 'running',
    })
    const items = useItemsStore()
    const spy = vi.spyOn(items, 'onSingleUpdateCompleted')
    sync.applyStatusMessage({
      type: 'status',
      key: 'single_update',
      value: 'completed',
    })
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('does not call items.onSingleUpdateCompleted when transitioning from idle', () => {
    const sync = useSyncStore()
    const items = useItemsStore()
    const spy = vi.spyOn(items, 'onSingleUpdateCompleted')
    sync.applyStatusMessage({
      type: 'status',
      key: 'single_update',
      value: 'completed',
    })
    expect(spy).not.toHaveBeenCalled()
  })
})

describe('useSyncStore — fetchStatus polling fallback', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('is a no-op when the session cannot call the API', async () => {
    useSessionStore().$patch({ status: 'unauthenticated' })
    const sync = useSyncStore()
    await sync.fetchStatus()
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('merges the snapshot into store state', async () => {
    setupAuthDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({
        statuses: { sync_video: 'running', cleanup: 'queued' },
        progress: { sync_video: { current: 2, total: 10 } },
      }),
    )
    const sync = useSyncStore()
    await sync.fetchStatus()
    expect(sync.statuses.sync_video).toBe('running')
    expect(sync.statuses.cleanup).toBe('queued')
    expect(sync.progress.sync_video).toEqual({ current: 2, total: 10 })
  })

  it('swallows network errors silently', async () => {
    setupAuthDisabled()
    vi.mocked(globalThis.fetch).mockRejectedValueOnce(new Error('boom'))
    const sync = useSyncStore()
    await expect(sync.fetchStatus()).resolves.toBeUndefined()
  })
})

describe('useSyncStore — mutating actions', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch')
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('returns true and clears lastError on a 200 start', async () => {
    setupAuthDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'started' }),
    )
    const sync = useSyncStore()
    const ok = await sync.startFullPipeline()
    expect(ok).toBe(true)
    expect(sync.lastError).toBeNull()
    expect(
      vi.mocked(globalThis.fetch).mock.calls[0][0],
    ).toBe('/api/start_full_pipeline')
  })

  it('passes filters through to /api/start_sync_video', async () => {
    setupAuthDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'started' }),
    )
    const sync = useSyncStore()
    await sync.startSyncVideo({ minYear: 2010, maxYear: 2020 })
    expect(
      vi.mocked(globalThis.fetch).mock.calls[0][0],
    ).toBe('/api/start_sync_video?min_year=2010&max_year=2020')
  })

  it('forces ?force=true for startReprocess(true)', async () => {
    setupAuthDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'started' }),
    )
    const sync = useSyncStore()
    await sync.startReprocess(true)
    expect(
      vi.mocked(globalThis.fetch).mock.calls[0][0],
    ).toBe('/api/start_reprocess?force=true')
  })

  it('surfaces a 400 detail as a toast and returns false', async () => {
    setupAuthDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ detail: 'Другой процесс (sync_video) уже запущен' }, { status: 400 }),
    )
    const sync = useSyncStore()
    const toast = useToastStore()
    const ok = await sync.startCleanup()
    expect(ok).toBe(false)
    expect(sync.lastError).toMatch(/sync_video/)
    expect(toast.current?.tone).toBe('error')
  })

  it('falls back to a generic message on non-2xx without detail', async () => {
    setupAuthDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('boom', { status: 500 }),
    )
    const sync = useSyncStore()
    const ok = await sync.startCleanup()
    expect(ok).toBe(false)
    expect(sync.lastError).toMatch(/500/)
  })

  it('targets /api/stop/{key} for stop()', async () => {
    setupAuthDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'stopped' }),
    )
    const sync = useSyncStore()
    const key: ProcessKey = 'sync_video'
    const ok = await sync.stop(key)
    expect(ok).toBe(true)
    expect(
      vi.mocked(globalThis.fetch).mock.calls[0][0],
    ).toBe('/api/stop/sync_video')
  })

  it('schedules a follow-up fetchStatus when WS is offline', async () => {
    setupAuthDisabled()
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson({ status: 'started' }))
      .mockResolvedValueOnce(mockJson({ statuses: { sync_video: 'running' } }))
    const sync = useSyncStore()
    await sync.startSyncVideo()
    vi.advanceTimersByTime(600)
    await Promise.resolve()
    await Promise.resolve()
    expect(
      vi.mocked(globalThis.fetch).mock.calls.map((c) => String(c[0])),
    ).toContain('/api/process_status')
  })
})

describe('useSyncStore — connect/disconnect', () => {
  let originalWs: typeof globalThis.WebSocket

  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    FakeWebSocket.instances = []
    originalWs = globalThis.WebSocket
    Object.defineProperty(globalThis, 'WebSocket', {
      configurable: true,
      writable: true,
      value: FakeWebSocket,
    })
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    // Reset the module-scoped `currentSocket` / reconnect / poll
    // timer state so the next test starts from a clean slate.
    try {
      useSyncStore().disconnect()
    } catch {
      /* noop */
    }
    Object.defineProperty(globalThis, 'WebSocket', {
      configurable: true,
      writable: true,
      value: originalWs,
    })
    vi.restoreAllMocks()
  })

  it('does not open a socket when the session cannot call the API', async () => {
    useSessionStore().$patch({ status: 'unauthenticated' })
    const sync = useSyncStore()
    await sync.connect()
    expect(FakeWebSocket.instances).toHaveLength(0)
  })

  it('opens the ws:/wss: URL without any query when auth is disabled', async () => {
    setupAuthDisabled()
    const sync = useSyncStore()
    await sync.connect()
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(FakeWebSocket.instances[0].url).toMatch(/\/ws$/)
  })

  it('negotiates a ticket and uses ?ticket=... when authenticated', async () => {
    setupAuthenticated('tok-xyz')
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ ticket: 't-9' }),
    )
    const sync = useSyncStore()
    await sync.connect()
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(FakeWebSocket.instances[0].url).toContain('?ticket=t-9')
  })

  it('falls back to ?token=... if the ticket endpoint fails', async () => {
    setupAuthenticated('tok-fb')
    vi.mocked(globalThis.fetch).mockRejectedValueOnce(new Error('nope'))
    const sync = useSyncStore()
    await sync.connect()
    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(FakeWebSocket.instances[0].url).toContain('?token=tok-fb')
  })

  it('flips wsConnected and routes status messages on open', async () => {
    setupAuthDisabled()
    const sync = useSyncStore()
    await sync.connect()
    const ws = FakeWebSocket.instances[0]
    ws.emitOpen()
    expect(sync.wsConnected).toBe(true)
    ws.emitMessage({ type: 'status', key: 'sync_video', value: 'running' })
    expect(sync.statuses.sync_video).toBe('running')
  })

  it('flips wsConnected back to false on close', async () => {
    setupAuthDisabled()
    const sync = useSyncStore()
    await sync.connect()
    const ws = FakeWebSocket.instances[0]
    ws.emitOpen()
    expect(sync.wsConnected).toBe(true)
    ws.emitClose()
    expect(sync.wsConnected).toBe(false)
  })

  it('disconnect() closes the socket and does not schedule a reconnect', async () => {
    setupAuthDisabled()
    vi.useFakeTimers()
    const sync = useSyncStore()
    await sync.connect()
    const ws = FakeWebSocket.instances[0]
    ws.emitOpen()
    sync.disconnect()
    expect(ws.closed).toBe(true)
    expect(sync.wsConnected).toBe(false)
    // Advance well past the reconnect delay; no new instance should appear.
    vi.advanceTimersByTime(10000)
    expect(FakeWebSocket.instances).toHaveLength(1)
    vi.useRealTimers()
  })
})
