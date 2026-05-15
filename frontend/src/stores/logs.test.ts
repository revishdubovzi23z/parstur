// ROADMAP Stage 10.7e — logs store tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  LOG_TYPE_FILENAMES,
  PROCESS_TO_LOG,
  useLogsStore,
} from './logs'
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

describe('useLogsStore — refresh()', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    useLogsStore().stopPolling()
    vi.restoreAllMocks()
  })

  it('is a no-op when the session cannot call the API', async () => {
    useSessionStore().$patch({ status: 'unauthenticated' })
    const logs = useLogsStore()
    await logs.refresh()
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('writes data.log into content on a 200 response', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ log: 'hello world', filename: 'reprocess_log.txt' }),
    )
    const logs = useLogsStore()
    await logs.refresh()
    expect(logs.content).toBe('hello world')
    expect(logs.error).toBeNull()
  })

  it('targets the selected type as the log_type query', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson({ log: '' }))
    const logs = useLogsStore()
    logs.selectedType = 'kinopub'
    await logs.refresh()
    expect(vi.mocked(globalThis.fetch).mock.calls[0][0]).toBe(
      '/api/sync_log?log_type=kinopub',
    )
  })

  it('records non-2xx as error', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('boom', { status: 503 }),
    )
    const logs = useLogsStore()
    await logs.refresh()
    expect(logs.error).toMatch(/503/)
  })
})

describe('useLogsStore — selectType / userSelected', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(globalThis, 'fetch')
    authDisabled()
  })

  afterEach(() => {
    useLogsStore().stopPolling()
    vi.restoreAllMocks()
  })

  it('flips userSelected when the user clicks a tab', async () => {
    const logs = useLogsStore()
    await logs.selectType('cleanup')
    expect(logs.userSelected).toBe(true)
    expect(logs.selectedType).toBe('cleanup')
  })

  it('refreshes only when the panel is open', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson({ log: 'x' }))
    const logs = useLogsStore()
    await logs.selectType('cleanup')
    expect(globalThis.fetch).not.toHaveBeenCalled()
    logs.panelOpen = true
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson({ log: 'x' }))
    await logs.selectType('rezka')
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
  })
})

describe('useLogsStore — autoFollow', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    authDisabled()
  })

  afterEach(() => {
    useLogsStore().stopPolling()
  })

  it('switches to the running process if user has not pinned a tab', () => {
    const logs = useLogsStore()
    logs.autoFollow(['kinopub'])
    expect(logs.selectedType).toBe(PROCESS_TO_LOG.kinopub)
  })

  it('does NOT switch when the user has pinned a tab manually', () => {
    const logs = useLogsStore()
    logs.userSelected = true
    logs.selectedType = 'cleanup'
    logs.autoFollow(['sync_video'])
    expect(logs.selectedType).toBe('cleanup')
  })

  it('ignores process keys without a log mapping', () => {
    const logs = useLogsStore()
    logs.selectedType = 'reprocess'
    logs.autoFollow(['full_pipeline'])
    expect(logs.selectedType).toBe('reprocess')
  })
})

describe('useLogsStore — appendChunk', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    authDisabled()
  })

  afterEach(() => {
    useLogsStore().stopPolling()
  })

  it('appends chunks that match the active tab', () => {
    const logs = useLogsStore()
    logs.selectedType = 'video'
    logs.content = 'a'
    logs.appendChunk('sync_video', 'b')
    expect(logs.content).toBe('ab')
  })

  it('drops chunks targeted at a different tab', () => {
    const logs = useLogsStore()
    logs.selectedType = 'video'
    logs.content = 'a'
    logs.appendChunk('cleanup', 'b')
    expect(logs.content).toBe('a')
  })
})

describe('useLogsStore — clear / download', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    authDisabled()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    useLogsStore().stopPolling()
    vi.restoreAllMocks()
  })

  it('clear() POSTs /api/clear_log, then re-refreshes', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson({ status: 'success' }))
      .mockResolvedValueOnce(mockJson({ log: '' }))
    const logs = useLogsStore()
    const ok = await logs.clear()
    expect(ok).toBe(true)
    const calls = vi.mocked(globalThis.fetch).mock.calls.map((c) => String(c[0]))
    expect(calls[0]).toMatch(/\/api\/clear_log\?log_type=reprocess/)
    expect(calls[1]).toMatch(/\/api\/sync_log/)
  })

  it('surfaces HTTP failures from clear() via toast', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('nope', { status: 500 }),
    )
    const logs = useLogsStore()
    const toast = useToastStore()
    const ok = await logs.clear()
    expect(ok).toBe(false)
    expect(toast.current?.tone).toBe('error')
  })

  it('download() fetches and triggers a blob URL anchor click', async () => {
    const blob = new Blob(['log body'], { type: 'text/plain' })
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(blob, { status: 200 }),
    )
    const createUrlSpy = vi
      .spyOn(window.URL, 'createObjectURL')
      .mockReturnValue('blob:fake')
    const revokeSpy = vi
      .spyOn(window.URL, 'revokeObjectURL')
      .mockImplementation(() => {})
    const clickSpy = vi
      .spyOn(window.HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {})
    const logs = useLogsStore()
    await logs.download()
    expect(createUrlSpy).toHaveBeenCalled()
    expect(clickSpy).toHaveBeenCalled()
    expect(revokeSpy).toHaveBeenCalledWith('blob:fake')
  })
})

describe('useLogsStore — polling', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    authDisabled()
    vi.useFakeTimers()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    useLogsStore().close()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('starts polling on open() and stops on close()', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(mockJson({ log: '' }))
    const logs = useLogsStore()
    logs.open()
    // Initial refresh.
    await Promise.resolve()
    await Promise.resolve()
    expect(logs.panelOpen).toBe(true)
    const before = vi.mocked(globalThis.fetch).mock.calls.length
    vi.advanceTimersByTime(2100)
    expect(vi.mocked(globalThis.fetch).mock.calls.length).toBeGreaterThan(
      before,
    )
    logs.close()
    const after = vi.mocked(globalThis.fetch).mock.calls.length
    vi.advanceTimersByTime(5000)
    expect(vi.mocked(globalThis.fetch).mock.calls.length).toBe(after)
  })
})

describe('LOG_TYPE_FILENAMES', () => {
  it('covers every log type in LOG_TYPE_LABELS', () => {
    expect(Object.keys(LOG_TYPE_FILENAMES).sort()).toEqual(
      [
        'cleanup',
        'fix',
        'fix_poiskkino',
        'kinopub',
        'other',
        'reprocess',
        'rezka',
        'rezka_collections',
        'single_update',
        'user',
        'video',
      ].sort(),
    )
  })
})
