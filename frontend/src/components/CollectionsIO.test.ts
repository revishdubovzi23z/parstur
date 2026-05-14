// ROADMAP Stage 10.7i — CollectionsIO.vue component tests.

import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CollectionsIO from './CollectionsIO.vue'
import { useCollectionsStore } from '../stores/collections'
import { useFeedStore } from '../stores/feed'
import { useSessionStore } from '../stores/session'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function authDisabled(): void {
  useSessionStore().$patch({ status: 'disabled' })
}

const REPORT = {
  created_collections: 1,
  updated_collections: 2,
  added_items: 7,
  missing_items: 0,
}

interface AnchorHarness {
  anchorClick: ReturnType<typeof vi.fn>
  capturedAnchor: HTMLAnchorElement | null
  restore: () => void
}

function installDownloadHarness(): AnchorHarness {
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
  const spy = vi
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
    anchorClick,
    get capturedAnchor() {
      return capturedAnchor
    },
    restore() {
      spy.mockRestore()
    },
  } as AnchorHarness
}

/**
 * Stuff a File into the change handler of a hidden input. jsdom's
 * `input.files` setter is read-only, so we redefine the property on
 * the specific element under test.
 */
async function pickFile(
  selector: string,
  file: File,
  wrapper: ReturnType<typeof mount>,
): Promise<void> {
  const inputWrapper = wrapper.get(selector)
  const input = inputWrapper.element as HTMLInputElement
  Object.defineProperty(input, 'files', {
    configurable: true,
    value: [file],
  })
  await inputWrapper.trigger('change')
  await flushPromises()
}

describe('CollectionsIO.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the export + import controls and starts in merge mode', () => {
    authDisabled()
    const wrapper = mount(CollectionsIO)
    expect(wrapper.find('[data-testid="collections-io"]').exists()).toBe(true)
    expect(
      wrapper.find('[data-testid="collections-export-json"]').exists(),
    ).toBe(true)
    expect(
      wrapper.find('[data-testid="collections-export-csv"]').exists(),
    ).toBe(true)
    expect(
      wrapper.find('[data-testid="collections-import-json"]').exists(),
    ).toBe(true)
    expect(
      wrapper.find('[data-testid="collections-import-csv"]').exists(),
    ).toBe(true)
    // Merge is the default mode.
    const merge = wrapper.find<HTMLInputElement>(
      '[data-testid="collections-mode-merge"]',
    )
    expect(merge.element.checked).toBe(true)
  })

  it('clicking «⬇ JSON» dispatches GET /export?fmt=json', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(new Blob(['{}'])),
    )
    const harness = installDownloadHarness()
    try {
      const wrapper = mount(CollectionsIO)
      await wrapper.find('[data-testid="collections-export-json"]').trigger('click')
      await flushPromises()
      expect(globalThis.fetch).toHaveBeenCalledWith(
        '/api/collections/export?fmt=json',
        expect.anything(),
      )
      expect(harness.anchorClick).toHaveBeenCalled()
      const banner = wrapper.find('[data-testid="collections-io-result"]')
      expect(banner.exists()).toBe(true)
      expect(banner.text()).toContain('Экспорт начат')
    } finally {
      harness.restore()
    }
  })

  it('clicking «⬇ CSV» dispatches GET /export?fmt=csv', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response(new Blob(['a\n'])),
    )
    const harness = installDownloadHarness()
    try {
      const wrapper = mount(CollectionsIO)
      await wrapper.find('[data-testid="collections-export-csv"]').trigger('click')
      await flushPromises()
      expect(globalThis.fetch).toHaveBeenCalledWith(
        '/api/collections/export?fmt=csv',
        expect.anything(),
      )
    } finally {
      harness.restore()
    }
  })

  it('picking a JSON file POSTs to /import with replace=false and emits imported', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch)
      // first call: import endpoint
      .mockResolvedValueOnce(mockJson(REPORT))
      // collections.refresh()
      .mockResolvedValueOnce(mockJson([]))
      // feed.fetchFeed()
      .mockResolvedValueOnce(mockJson({ items: [], total_pages: 0 }))

    const wrapper = mount(CollectionsIO)
    const collections = useCollectionsStore()
    const feed = useFeedStore()
    const refreshSpy = vi.spyOn(collections, 'refresh').mockResolvedValue()
    const fetchFeedSpy = vi.spyOn(feed, 'fetchFeed').mockResolvedValue()

    const file = new File([JSON.stringify({ collections: [] })], 'snap.json', {
      type: 'application/json',
    })
    await pickFile('[data-testid="collections-import-json-input"]', file, wrapper)

    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/api/collections/import')
    const body = JSON.parse(String((init as RequestInit).body))
    expect(body.replace).toBe(false)
    expect(refreshSpy).toHaveBeenCalled()
    expect(fetchFeedSpy).toHaveBeenCalled()
    expect(wrapper.emitted('imported')).toBeTruthy()
  })

  it('switching to «Replace» mode flips the body flag', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(REPORT))
    const wrapper = mount(CollectionsIO)
    vi.spyOn(useCollectionsStore(), 'refresh').mockResolvedValue()
    vi.spyOn(useFeedStore(), 'fetchFeed').mockResolvedValue()

    await wrapper
      .find('[data-testid="collections-mode-replace"]')
      .setValue(true)

    const file = new File([JSON.stringify([])], 'a.json', {
      type: 'application/json',
    })
    await pickFile('[data-testid="collections-import-json-input"]', file, wrapper)

    const init = vi.mocked(globalThis.fetch).mock.calls[0][1] as RequestInit
    const body = JSON.parse(String(init.body))
    expect(body.replace).toBe(true)
  })

  it('rejects a non-JSON file with an inline error banner', async () => {
    authDisabled()
    const wrapper = mount(CollectionsIO)

    const file = new File(['this is not json'], 'broken.json', {
      type: 'application/json',
    })
    await pickFile('[data-testid="collections-import-json-input"]', file, wrapper)

    expect(globalThis.fetch).not.toHaveBeenCalled()
    const banner = wrapper.find('[data-testid="collections-io-result"]')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('JSON')
  })

  it('picking a CSV file POSTs the raw body to /import_csv', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(REPORT))
    const wrapper = mount(CollectionsIO)
    vi.spyOn(useCollectionsStore(), 'refresh').mockResolvedValue()
    vi.spyOn(useFeedStore(), 'fetchFeed').mockResolvedValue()

    const csv = 'collection_name,kp_id\nWatched,42\n'
    const file = new File([csv], 'snap.csv', { type: 'text/csv' })
    await pickFile('[data-testid="collections-import-csv-input"]', file, wrapper)

    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/api/collections/import_csv?replace=false')
    expect((init as RequestInit).body).toBe(csv)
  })

  it('renders the backend error banner without emitting imported', async () => {
    authDisabled()
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('boom', { status: 500 }),
    )
    const wrapper = mount(CollectionsIO)
    const collections = useCollectionsStore()
    const refreshSpy = vi.spyOn(collections, 'refresh').mockResolvedValue()

    const file = new File([JSON.stringify([])], 'a.json', {
      type: 'application/json',
    })
    await pickFile('[data-testid="collections-import-json-input"]', file, wrapper)

    const banner = wrapper.find('[data-testid="collections-io-result"]')
    expect(banner.text()).toContain('500')
    expect(refreshSpy).not.toHaveBeenCalled()
    expect(wrapper.emitted('imported')).toBeFalsy()
  })

  it('disables all controls while exportBusy is true', async () => {
    authDisabled()
    const wrapper = mount(CollectionsIO)
    const exportJson = wrapper.find<HTMLButtonElement>(
      '[data-testid="collections-export-json"]',
    )
    expect(exportJson.element.disabled).toBe(false)
    // Mock fetch to a never-resolving promise so the button stays busy.
    let resolve: (r: Response) => void = () => {}
    vi.mocked(globalThis.fetch).mockReturnValueOnce(
      new Promise<Response>((r) => {
        resolve = r
      }),
    )
    const harness = installDownloadHarness()
    try {
      const click = exportJson.trigger('click')
      await flushPromises()
      expect(exportJson.element.disabled).toBe(true)
      resolve(new Response(new Blob(['{}'])))
      await click
      await flushPromises()
      expect(exportJson.element.disabled).toBe(false)
    } finally {
      harness.restore()
    }
  })
})
