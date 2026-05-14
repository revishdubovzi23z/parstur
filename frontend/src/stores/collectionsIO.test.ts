// ROADMAP Stage 10.7i — collections IO store tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  type CollectionsImportReport,
  useCollectionsIOStore,
} from './collectionsIO'
import { useSessionStore } from './session'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function authDisabled(): void {
  useSessionStore().$patch({ status: 'disabled' })
}

const REPORT: CollectionsImportReport = {
  created_collections: 2,
  updated_collections: 1,
  added_items: 17,
  missing_items: 3,
}

interface DownloadHarness {
  createObjectURL: ReturnType<typeof vi.fn>
  revokeObjectURL: ReturnType<typeof vi.fn>
  anchorClick: ReturnType<typeof vi.fn>
  capturedAnchor: HTMLAnchorElement | null
  restore: () => void
}

/**
 * Patch `URL.createObjectURL` / `revokeObjectURL` (jsdom doesn't ship
 * them) and intercept `document.createElement('a')` so we can assert
 * the filename + `click()` were applied without actually navigating.
 */
function installDownloadHarness(): DownloadHarness {
  const createObjectURL = vi.fn(() => 'blob:fake')
  const revokeObjectURL = vi.fn()
  const originalURL = globalThis.URL
  Object.defineProperty(globalThis, 'URL', {
    configurable: true,
    value: Object.assign(originalURL, {
      createObjectURL,
      revokeObjectURL,
    }),
  })

  const anchorClick = vi.fn()
  let capturedAnchor: HTMLAnchorElement | null = null
  const realCreateElement = document.createElement.bind(document)
  const createElementSpy = vi
    .spyOn(document, 'createElement')
    .mockImplementation((tag: string) => {
      const el = realCreateElement(tag)
      if (tag === 'a') {
        ;(el as HTMLAnchorElement).click = anchorClick
        capturedAnchor = el as HTMLAnchorElement
      }
      return el
    })

  return {
    createObjectURL,
    revokeObjectURL,
    anchorClick,
    get capturedAnchor() {
      return capturedAnchor
    },
    restore() {
      createElementSpy.mockRestore()
    },
  } as DownloadHarness
}

describe('useCollectionsIOStore — exportCollections()', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('is a no-op when the session cannot call the API', async () => {
    useSessionStore().$patch({ status: 'unauthenticated' })
    const io = useCollectionsIOStore()
    const result = await io.exportCollections('json')
    expect(result.tone).toBe('error')
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('downloads the JSON blob with a timestamped filename', async () => {
    authDisabled()
    const blob = new Blob(['{"version":1}'], { type: 'application/json' })
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(blob, { status: 200 }),
    )
    const harness = installDownloadHarness()
    try {
      const io = useCollectionsIOStore()
      const result = await io.exportCollections('json')
      expect(result.tone).toBe('success')
      expect(globalThis.fetch).toHaveBeenCalledWith(
        '/api/collections/export?fmt=json',
        expect.anything(),
      )
      expect(harness.createObjectURL).toHaveBeenCalledWith(blob)
      expect(harness.revokeObjectURL).toHaveBeenCalledWith('blob:fake')
      expect(harness.anchorClick).toHaveBeenCalledOnce()
      // Filename: collections-<ISO date prefix>.json
      expect(harness.capturedAnchor?.download).toMatch(
        /^collections-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.json$/,
      )
    } finally {
      harness.restore()
    }
  })

  it('downloads the CSV blob when fmt=csv', async () => {
    authDisabled()
    const blob = new Blob(['header\n'], { type: 'text/csv' })
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(blob, { status: 200 }),
    )
    const harness = installDownloadHarness()
    try {
      const io = useCollectionsIOStore()
      await io.exportCollections('csv')
      expect(globalThis.fetch).toHaveBeenCalledWith(
        '/api/collections/export?fmt=csv',
        expect.anything(),
      )
      expect(harness.capturedAnchor?.download.endsWith('.csv')).toBe(true)
    } finally {
      harness.restore()
    }
  })

  it('reports HTTP failure with the status code', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('nope', { status: 500 }),
    )
    const io = useCollectionsIOStore()
    const result = await io.exportCollections('json')
    expect(result.tone).toBe('error')
    expect(result.message).toContain('500')
    expect(io.exportBusy).toBe(false)
  })

  it('surfaces network exceptions as error', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockRejectedValueOnce(new Error('offline'))
    const io = useCollectionsIOStore()
    const result = await io.exportCollections('json')
    expect(result.tone).toBe('error')
    expect(result.message).toContain('offline')
  })
})

describe('useCollectionsIOStore — importCollections()', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  function jsonFile(body: unknown, name = 'collections.json'): File {
    const text = typeof body === 'string' ? body : JSON.stringify(body)
    return new File([text], name, { type: 'application/json' })
  }

  it('is a no-op when the session cannot call the API', async () => {
    useSessionStore().$patch({ status: 'unauthenticated' })
    const io = useCollectionsIOStore()
    const result = await io.importCollections(jsonFile({ collections: [] }))
    expect(result.tone).toBe('error')
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('POSTs the envelope shape verbatim and reports the breakdown', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(REPORT))
    const io = useCollectionsIOStore()
    const file = jsonFile({
      collections: [{ name: 'Watched', items: [] }],
    })
    const result = await io.importCollections(file, 'merge')
    expect(result.tone).toBe('success')
    expect(result.report).toEqual(REPORT)
    expect(result.message).toContain('17')

    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/api/collections/import')
    expect((init as RequestInit).method).toBe('POST')
    const headers = (init as RequestInit).headers as Headers
    expect(headers.get('Content-Type')).toBe('application/json')
    const sent = JSON.parse(String((init as RequestInit).body))
    expect(sent.collections).toEqual([{ name: 'Watched', items: [] }])
    expect(sent.replace).toBe(false)
  })

  it('accepts a bare array and wraps it into the envelope shape', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(REPORT))
    const io = useCollectionsIOStore()
    const file = jsonFile([{ name: 'A' }, { name: 'B' }])
    await io.importCollections(file, 'replace')
    const init = vi.mocked(globalThis.fetch).mock.calls[0][1] as RequestInit
    const sent = JSON.parse(String(init.body))
    expect(sent.collections).toEqual([{ name: 'A' }, { name: 'B' }])
    expect(sent.replace).toBe(true)
  })

  it('rejects an unparseable JSON body without firing fetch', async () => {
    authDisabled()
    const io = useCollectionsIOStore()
    const file = new File(['this is not json'], 'broken.json', {
      type: 'application/json',
    })
    const result = await io.importCollections(file)
    expect(result.tone).toBe('error')
    expect(result.message).toContain('JSON')
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('reports HTTP 400 from the backend', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ error: 'bad payload' }, { status: 400 }),
    )
    const io = useCollectionsIOStore()
    const result = await io.importCollections(jsonFile({ collections: [] }))
    expect(result.tone).toBe('error')
    expect(result.message).toContain('400')
  })

  it('reports HTTP 500 from the backend', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('boom', { status: 500 }),
    )
    const io = useCollectionsIOStore()
    const result = await io.importCollections(jsonFile({ collections: [] }))
    expect(result.tone).toBe('error')
    expect(result.message).toContain('500')
  })

  it('clears `importBusy` even when the request throws', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockRejectedValueOnce(new Error('boom'))
    const io = useCollectionsIOStore()
    const result = await io.importCollections(jsonFile({ collections: [] }))
    expect(result.tone).toBe('error')
    expect(io.importBusy).toBe(false)
  })
})

describe('useCollectionsIOStore — importCollectionsCsv()', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  function csvFile(body: string, name = 'collections.csv'): File {
    return new File([body], name, { type: 'text/csv' })
  }

  it('POSTs raw CSV body with text/csv and replace=false on merge', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(REPORT))
    const io = useCollectionsIOStore()
    const file = csvFile(
      'collection_name,kp_id\nWatched,42\n',
      'collections.csv',
    )
    const result = await io.importCollectionsCsv(file, 'merge')
    expect(result.tone).toBe('success')
    expect(result.report).toEqual(REPORT)

    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/api/collections/import_csv?replace=false')
    const headers = (init as RequestInit).headers as Headers
    expect(headers.get('Content-Type')).toBe('text/csv')
    expect((init as RequestInit).body).toBe(
      'collection_name,kp_id\nWatched,42\n',
    )
  })

  it('forwards replace=true when mode=replace', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(REPORT))
    const io = useCollectionsIOStore()
    await io.importCollectionsCsv(csvFile('x\n'), 'replace')
    const url = vi.mocked(globalThis.fetch).mock.calls[0][0]
    expect(url).toBe('/api/collections/import_csv?replace=true')
  })

  it('reports HTTP 400 as error', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('bad columns', { status: 400 }),
    )
    const io = useCollectionsIOStore()
    const result = await io.importCollectionsCsv(csvFile('x\n'))
    expect(result.tone).toBe('error')
    expect(result.message).toContain('400')
  })

  it('reports HTTP 500 as error', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('kaboom', { status: 500 }),
    )
    const io = useCollectionsIOStore()
    const result = await io.importCollectionsCsv(csvFile('x\n'))
    expect(result.tone).toBe('error')
    expect(result.message).toContain('500')
  })

  it('accepts a large (>1 MB) file without choking', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(REPORT))
    // 1.2 MB CSV stub — verifies that file.text() + body forwarding
    // doesn't bottleneck on the happy path. Not a perf benchmark.
    const big = 'a,b\n' + 'row,value\n'.repeat(120_000)
    const io = useCollectionsIOStore()
    const result = await io.importCollectionsCsv(csvFile(big, 'big.csv'))
    expect(result.tone).toBe('success')
    const init = vi.mocked(globalThis.fetch).mock.calls[0][1] as RequestInit
    expect(String(init.body).length).toBeGreaterThan(1_000_000)
  })

  it('is a no-op when session.canCallApi is false', async () => {
    useSessionStore().$patch({ status: 'unauthenticated' })
    const io = useCollectionsIOStore()
    const result = await io.importCollectionsCsv(csvFile('x\n'))
    expect(result.tone).toBe('error')
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('clears importBusy even when fetch throws', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockRejectedValueOnce(new Error('offline'))
    const io = useCollectionsIOStore()
    await io.importCollectionsCsv(csvFile('x\n'))
    expect(io.importBusy).toBe(false)
  })
})

describe('useCollectionsIOStore — clearResult()', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('drops the last result so the banner can hide', () => {
    const io = useCollectionsIOStore()
    io.$patch({ lastResult: { tone: 'success', message: 'hi' } })
    io.clearResult()
    expect(io.lastResult).toBeNull()
  })
})
