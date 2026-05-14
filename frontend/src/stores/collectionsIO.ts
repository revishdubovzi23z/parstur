// ROADMAP Stage 10.7i — collections export / import store.
//
// Pinia replacement for the legacy `exportCollections()` and
// `onCollectionsImportFile()` methods on the root Vue instance in
// `index.html:1765-1841`. Wraps three backend endpoints defined at
// `routes/collections.py:116-209`:
//
//   - `GET  /api/collections/export?fmt=json|csv`  → blob (download)
//   - `POST /api/collections/import`                → JSON envelope
//   - `POST /api/collections/import_csv?replace=…`  → raw CSV body
//
// The backend's `replace` flag is exposed via a `mode` parameter
// (`'merge' | 'replace'`) to match the legacy `confirm()` prompt and
// to stay readable in calling code.

import { defineStore } from 'pinia'

import { apiFetch } from '../api/client'
import { useSessionStore } from './session'

export type CollectionsExportFormat = 'json' | 'csv'
export type CollectionsImportMode = 'merge' | 'replace'

/**
 * Report shape produced by `db.import_collections()` and forwarded
 * verbatim by both JSON and CSV import endpoints.
 */
export interface CollectionsImportReport {
  created_collections: number
  updated_collections: number
  added_items: number
  missing_items: number
}

export interface CollectionsIOResult {
  tone: 'success' | 'error' | 'info'
  message: string
  /** Populated when the action was a successful import. */
  report?: CollectionsImportReport
}

interface CollectionsIOStoreState {
  exportBusy: boolean
  importBusy: boolean
  /** Most recent action outcome; rendered by `CollectionsIO.vue`. */
  lastResult: CollectionsIOResult | null
}

function describe(err: unknown): string {
  if (err instanceof Error) return err.message
  return String(err)
}

/** Match the timestamped filename the legacy variant used at
 *  `index.html:1776-1777`. */
function downloadTimestamp(): string {
  return new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
}

function formatReport(report: CollectionsImportReport): string {
  return (
    `Импорт завершён: ` +
    `создано ${report.created_collections}, ` +
    `обновлено ${report.updated_collections}, ` +
    `добавлено ${report.added_items}, ` +
    `не найдено ${report.missing_items}`
  )
}

/**
 * Triggers a browser download by spawning an anchor pointed at a
 * blob URL. Same approach as `admin.exportDatabase()` at
 * `frontend/src/stores/admin.ts:313-324`.
 */
function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
  } finally {
    URL.revokeObjectURL(url)
  }
}

export const useCollectionsIOStore = defineStore('collectionsIO', {
  state: (): CollectionsIOStoreState => ({
    exportBusy: false,
    importBusy: false,
    lastResult: null,
  }),

  actions: {
    clearResult(): void {
      this.lastResult = null
    },

    /**
     * Download the collections snapshot in the requested format.
     * On success a file is offered to the user; the store records a
     * success/error banner regardless.
     */
    async exportCollections(
      fmt: CollectionsExportFormat = 'json',
    ): Promise<CollectionsIOResult> {
      const session = useSessionStore()
      if (!session.canCallApi) {
        const result: CollectionsIOResult = {
          tone: 'error',
          message: 'Сессия не авторизована',
        }
        this.lastResult = result
        return result
      }
      this.exportBusy = true
      this.lastResult = null
      try {
        const res = await apiFetch(
          `/api/collections/export?fmt=${encodeURIComponent(fmt)}`,
        )
        if (!res.ok) {
          const result: CollectionsIOResult = {
            tone: 'error',
            message: `Экспорт не удался: HTTP ${res.status}`,
          }
          this.lastResult = result
          return result
        }
        const blob = await res.blob()
        triggerBlobDownload(blob, `collections-${downloadTimestamp()}.${fmt}`)
        const result: CollectionsIOResult = {
          tone: 'success',
          message: 'Экспорт начат — проверьте папку загрузок.',
        }
        this.lastResult = result
        return result
      } catch (err) {
        const result: CollectionsIOResult = {
          tone: 'error',
          message: `Ошибка экспорта: ${describe(err)}`,
        }
        this.lastResult = result
        return result
      } finally {
        this.exportBusy = false
      }
    },

    /**
     * Import a JSON collections file. Accepts either the export
     * envelope (`{collections: [...]}`) or a bare array, mirroring
     * legacy at `index.html:1818`. Pre-parses on the client so we can
     * surface "файл не валиден" without a server round-trip.
     */
    async importCollections(
      file: File,
      mode: CollectionsImportMode = 'merge',
    ): Promise<CollectionsIOResult> {
      const session = useSessionStore()
      if (!session.canCallApi) {
        const result: CollectionsIOResult = {
          tone: 'error',
          message: 'Сессия не авторизована',
        }
        this.lastResult = result
        return result
      }
      this.importBusy = true
      this.lastResult = null
      try {
        const text = await file.text()
        let parsed: unknown
        try {
          parsed = JSON.parse(text)
        } catch {
          const result: CollectionsIOResult = {
            tone: 'error',
            message: 'Файл не является валидным JSON',
          }
          this.lastResult = result
          return result
        }
        let collections: unknown
        if (Array.isArray(parsed)) {
          collections = parsed
        } else if (
          parsed !== null &&
          typeof parsed === 'object' &&
          'collections' in (parsed as Record<string, unknown>)
        ) {
          collections = (parsed as Record<string, unknown>).collections
        } else {
          collections = []
        }
        const res = await apiFetch('/api/collections/import', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            collections,
            replace: mode === 'replace',
          }),
        })
        if (!res.ok) {
          const result: CollectionsIOResult = {
            tone: 'error',
            message: `Импорт не удался: HTTP ${res.status}`,
          }
          this.lastResult = result
          return result
        }
        const report = (await res.json()) as CollectionsImportReport
        const result: CollectionsIOResult = {
          tone: 'success',
          message: formatReport(report),
          report,
        }
        this.lastResult = result
        return result
      } catch (err) {
        const result: CollectionsIOResult = {
          tone: 'error',
          message: `Ошибка импорта: ${describe(err)}`,
        }
        this.lastResult = result
        return result
      } finally {
        this.importBusy = false
      }
    },

    /**
     * Import a CSV with the same column layout as the export
     * (`collection_name, sort_order, kp_id, imdb_id, rezka_url,
     * title, original_title, year, added_at`). The raw bytes are
     * sent as the request body with `Content-Type: text/csv`, which
     * the backend reads via `request.body()` and decodes as utf-8 /
     * latin-1.
     */
    async importCollectionsCsv(
      file: File,
      mode: CollectionsImportMode = 'merge',
    ): Promise<CollectionsIOResult> {
      const session = useSessionStore()
      if (!session.canCallApi) {
        const result: CollectionsIOResult = {
          tone: 'error',
          message: 'Сессия не авторизована',
        }
        this.lastResult = result
        return result
      }
      this.importBusy = true
      this.lastResult = null
      try {
        const text = await file.text()
        const replace = mode === 'replace' ? 'true' : 'false'
        const res = await apiFetch(
          `/api/collections/import_csv?replace=${replace}`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'text/csv' },
            body: text,
          },
        )
        if (!res.ok) {
          const result: CollectionsIOResult = {
            tone: 'error',
            message: `Импорт не удался: HTTP ${res.status}`,
          }
          this.lastResult = result
          return result
        }
        const report = (await res.json()) as CollectionsImportReport
        const result: CollectionsIOResult = {
          tone: 'success',
          message: formatReport(report),
          report,
        }
        this.lastResult = result
        return result
      } catch (err) {
        const result: CollectionsIOResult = {
          tone: 'error',
          message: `Ошибка импорта: ${describe(err)}`,
        }
        this.lastResult = result
        return result
      } finally {
        this.importBusy = false
      }
    },
  },
})
