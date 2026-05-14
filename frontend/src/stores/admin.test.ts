// ROADMAP Stage 10.6 — admin store unit tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAdminStore } from './admin'
import { useSessionStore } from './session'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function authorise(): void {
  const session = useSessionStore()
  session.$patch({ status: 'disabled' })
}

describe('useAdminStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('selfUpdate', () => {
    it('reports success and flags restart on status=updated', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ status: 'updated', message: 'Fast-forwarded' }),
      )
      authorise()
      const admin = useAdminStore()

      const result = await admin.selfUpdate()
      expect(result.tone).toBe('success')
      expect(result.willRestart).toBe(true)
      expect(admin.lastResult).toEqual(result)
      expect(admin.selfUpdateBusy).toBe(false)
    })

    it('reports info on status=up_to_date without restart', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ status: 'up_to_date' }),
      )
      authorise()
      const admin = useAdminStore()

      const result = await admin.selfUpdate()
      expect(result.tone).toBe('info')
      expect(result.willRestart).toBe(false)
    })

    it('surfaces backend error messages', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ status: 'error', message: 'rebase failed' }),
      )
      authorise()
      const admin = useAdminStore()

      const result = await admin.selfUpdate()
      expect(result.tone).toBe('error')
      expect(result.message).toBe('rebase failed')
    })

    it('refuses to call when the session is not callable', async () => {
      const session = useSessionStore()
      session.$patch({ status: 'unauthenticated' })
      const admin = useAdminStore()

      const result = await admin.selfUpdate()
      expect(result.tone).toBe('error')
      expect(globalThis.fetch).not.toHaveBeenCalled()
    })
  })

  describe('resetDatabase', () => {
    it('chains token → confirm and flags restart on success', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson({ token: 'abc' }))
        .mockResolvedValueOnce(mockJson({ status: 'success', message: 'gone' }))
      authorise()
      const admin = useAdminStore()

      const result = await admin.resetDatabase()
      expect(result.tone).toBe('success')
      expect(result.willRestart).toBe(true)

      const calls = vi.mocked(globalThis.fetch).mock.calls.map(([u]) => String(u))
      expect(calls[0]).toBe('/api/reset_database/token')
      expect(calls[1]).toBe('/api/reset_database?confirm=abc')
    })

    it('bails out when the token endpoint fails', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        new Response('boom', { status: 500 }),
      )
      authorise()
      const admin = useAdminStore()

      const result = await admin.resetDatabase()
      expect(result.tone).toBe('error')
      expect(result.message).toContain('500')
      expect(globalThis.fetch).toHaveBeenCalledTimes(1)
    })

    it('forwards a non-success status as error', async () => {
      vi.mocked(globalThis.fetch)
        .mockResolvedValueOnce(mockJson({ token: 'abc' }))
        .mockResolvedValueOnce(mockJson({ status: 'error', message: 'no go' }))
      authorise()
      const admin = useAdminStore()

      const result = await admin.resetDatabase()
      expect(result.tone).toBe('error')
      expect(result.message).toBe('no go')
    })
  })

  describe('importDatabase', () => {
    it('POSTs multipart and flags restart on success', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ status: 'success', message: 'imported' }),
      )
      authorise()
      const admin = useAdminStore()
      const file = new File([new Uint8Array([1, 2, 3])], 'a.db', {
        type: 'application/x-sqlite3',
      })

      const result = await admin.importDatabase(file)
      expect(result.tone).toBe('success')
      expect(result.willRestart).toBe(true)

      const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
      expect(String(url)).toBe('/api/database_import')
      const opts = init as RequestInit
      expect(opts.method).toBe('POST')
      expect(opts.body).toBeInstanceOf(FormData)
      const formField = (opts.body as FormData).get('file')
      expect(formField).toBeInstanceOf(File)
    })

    it('reads the error body on a 400 (e.g. bad magic bytes)', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        new Response(
          JSON.stringify({ status: 'error', message: 'bad header' }),
          {
            status: 400,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      )
      authorise()
      const admin = useAdminStore()
      const file = new File([new Uint8Array([0])], 'a.db')

      const result = await admin.importDatabase(file)
      expect(result.tone).toBe('error')
      expect(result.message).toBe('bad header')
    })
  })

  describe('exportDatabase', () => {
    it('triggers a client-side download via a temporary anchor', async () => {
      const blob = new Blob([new Uint8Array([1, 2, 3])], {
        type: 'application/x-sqlite3',
      })
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        new Response(blob, {
          status: 200,
          headers: { 'Content-Type': 'application/x-sqlite3' },
        }),
      )
      // jsdom doesn't ship URL.createObjectURL.
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
      const realCreateElement = document.createElement.bind(document)
      const createElementSpy = vi
        .spyOn(document, 'createElement')
        .mockImplementation((tag: string) => {
          const el = realCreateElement(tag)
          if (tag === 'a') {
            ;(el as HTMLAnchorElement).click = anchorClick
          }
          return el
        })

      authorise()
      const admin = useAdminStore()
      const result = await admin.exportDatabase()

      expect(result.tone).toBe('success')
      expect(createObjectURL).toHaveBeenCalledWith(blob)
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:fake')
      expect(anchorClick).toHaveBeenCalled()

      createElementSpy.mockRestore()
    })

    it('reports HTTP errors verbatim', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        new Response('boom', { status: 503 }),
      )
      authorise()
      const admin = useAdminStore()

      const result = await admin.exportDatabase()
      expect(result.tone).toBe('error')
      expect(result.message).toContain('503')
    })
  })

  it('clearResult drops the last banner', async () => {
    authorise()
    const admin = useAdminStore()
    admin.lastResult = { tone: 'info', message: 'old', willRestart: false }
    admin.clearResult()
    expect(admin.lastResult).toBeNull()
  })

  /** Shared blob-download test scaffold for `downloadBackup` /
   *  `exportItems`. Returns the spy on the anchor's `click` so the
   *  caller can assert the file was triggered. */
  function stubAnchorDownload(): {
    anchorClick: ReturnType<typeof vi.fn>
    createObjectURL: ReturnType<typeof vi.fn>
    revokeObjectURL: ReturnType<typeof vi.fn>
    restore: () => void
  } {
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
    const realCreateElement = document.createElement.bind(document)
    const createElementSpy = vi
      .spyOn(document, 'createElement')
      .mockImplementation((tag: string) => {
        const el = realCreateElement(tag)
        if (tag === 'a') {
          ;(el as HTMLAnchorElement).click = anchorClick
        }
        return el
      })

    return {
      anchorClick,
      createObjectURL,
      revokeObjectURL,
      restore: () => createElementSpy.mockRestore(),
    }
  }

  describe('downloadBackup', () => {
    it('GETs /api/backup/download and triggers a client download with the server-provided filename', async () => {
      const blob = new Blob([new Uint8Array([4, 2])], {
        type: 'application/octet-stream',
      })
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        new Response(blob, {
          status: 200,
          headers: {
            'Content-Type': 'application/octet-stream',
            'Content-Disposition': 'attachment; filename="app_data-20250101.db"',
          },
        }),
      )
      const stubs = stubAnchorDownload()

      authorise()
      const admin = useAdminStore()
      const result = await admin.downloadBackup()

      expect(result.tone).toBe('success')
      expect(result.message).toContain('app_data-20250101.db')
      expect(stubs.anchorClick).toHaveBeenCalled()
      expect(stubs.createObjectURL).toHaveBeenCalledWith(blob)
      expect(stubs.revokeObjectURL).toHaveBeenCalledWith('blob:fake')

      const [url] = vi.mocked(globalThis.fetch).mock.calls[0]
      expect(String(url)).toBe('/api/backup/download')

      stubs.restore()
    })

    it('reports HTTP errors and skips the anchor click', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        new Response('nope', { status: 500 }),
      )
      authorise()
      const admin = useAdminStore()
      const result = await admin.downloadBackup()
      expect(result.tone).toBe('error')
      expect(result.message).toContain('500')
    })
  })

  describe('exportItems', () => {
    it('GETs /api/export with fmt + category_id query params', async () => {
      const blob = new Blob(['{}'], { type: 'application/json' })
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        new Response(blob, {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'Content-Disposition': 'attachment; filename=export.json',
          },
        }),
      )
      const stubs = stubAnchorDownload()

      authorise()
      const admin = useAdminStore()
      const result = await admin.exportItems('json', -1)

      expect(result.tone).toBe('success')
      const [url] = vi.mocked(globalThis.fetch).mock.calls[0]
      expect(String(url)).toContain('/api/export?')
      expect(String(url)).toContain('fmt=json')
      expect(String(url)).toContain('category_id=-1')
      expect(stubs.anchorClick).toHaveBeenCalled()

      stubs.restore()
    })

    it('uses csv format and category_id from the args', async () => {
      const blob = new Blob(['a,b'], { type: 'text/csv' })
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        new Response(blob, {
          status: 200,
          headers: { 'Content-Type': 'text/csv' },
        }),
      )
      const stubs = stubAnchorDownload()

      authorise()
      const admin = useAdminStore()
      await admin.exportItems('csv', 1)

      const [url] = vi.mocked(globalThis.fetch).mock.calls[0]
      expect(String(url)).toContain('fmt=csv')
      expect(String(url)).toContain('category_id=1')

      stubs.restore()
    })
  })

  describe('rebuildFts', () => {
    it('POSTs /api/rebuild_fts and reports success', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        mockJson({ status: 'success' }),
      )
      authorise()
      const admin = useAdminStore()
      const result = await admin.rebuildFts()
      expect(result.tone).toBe('success')
      const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
      expect(String(url)).toBe('/api/rebuild_fts')
      expect((init as RequestInit).method).toBe('POST')
    })

    it('reports HTTP errors verbatim', async () => {
      vi.mocked(globalThis.fetch).mockResolvedValueOnce(
        new Response('boom', { status: 500 }),
      )
      authorise()
      const admin = useAdminStore()
      const result = await admin.rebuildFts()
      expect(result.tone).toBe('error')
      expect(result.message).toContain('500')
    })
  })
})
