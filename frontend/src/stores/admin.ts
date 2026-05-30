// ROADMAP Stage 10.6 — admin actions store.
//
// Pinia replacement for the legacy `selfUpdate` / `resetDatabase` /
// `onDbImportFile` methods on the root Vue instance in `index.html`
// (~lines 1700-1759), plus the `/api/database_export` direct download.
// All four endpoints are auth-gated, so we route everything through
// `apiFetch` and surface the result on the store so the modal can
// render a status banner instead of relying on `alert()`.

import { defineStore } from 'pinia'
import { apiFetch, UnauthorizedError } from '../api/client'
import { useSessionStore } from './session'

/** Result reported by the panel after a long-running admin action. */
export interface AdminActionResult {
  tone: 'success' | 'error' | 'info'
  message: string
  /**
   * `true` when the backend reports it will restart. Callers can show
   * a "page will reload in 10s" hint; the actual reload is left to the
   * caller because it might want to gate it behind explicit consent.
   */
  willRestart: boolean
}

interface SelfUpdateResponse {
  status: 'updated' | 'up_to_date' | 'error'
  message?: string
}

interface ResetTokenResponse {
  token: string
}

interface DatabaseImportResponse {
  status: 'success' | 'error'
  message?: string
}

interface AdminStoreState {
  /**
   * Per-action "in flight" flags. The modal disables buttons while an
   * action is running.
   */
  selfUpdateBusy: boolean
  resetBusy: boolean
  importBusy: boolean
  exportBusy: boolean
  /** Backup-snapshot download (`/api/backup/download`). */
  backupBusy: boolean
  /** Items export to JSON/CSV (`/api/export`). */
  itemsExportBusy: boolean
  /** FTS index rebuild (`/api/rebuild_fts`). */
  rebuildFtsBusy: boolean
  /** Clear media data while keeping auth (`/api/database_clear`). */
  clearDatabaseBusy: boolean
  /** Full rebuild skip git pull (`/api/self_update?skip_pull=true`). */
  rebuildBusy: boolean
  /** Just restart (`/api/restart_server`). */
  restartBusy: boolean
  /** Most recent action outcome, rendered in the panel. */
  lastResult: AdminActionResult | null
}

function describe(err: unknown): string {
  if (err instanceof Error) return err.message
  return String(err)
}

export const useAdminStore = defineStore('admin', {
  state: (): AdminStoreState => ({
    selfUpdateBusy: false,
    resetBusy: false,
    importBusy: false,
    exportBusy: false,
    backupBusy: false,
    itemsExportBusy: false,
    rebuildFtsBusy: false,
    clearDatabaseBusy: false,
    rebuildBusy: false,
    restartBusy: false,
    lastResult: null,
  }),

  actions: {
    clearResult(): void {
      this.lastResult = null
    },

    /**
     * POST /api/self_update — runs `git pull` on the server. Three
     * branches in the response: `updated` (server restarts),
     * `up_to_date` (no-op), `error` (display message).
     */
    async selfUpdate(): Promise<AdminActionResult> {
      const session = useSessionStore()
      if (!session.canCallApi) {
        return {
          tone: 'error',
          message: 'Сессия не авторизована',
          willRestart: false,
        }
      }
      this.selfUpdateBusy = true
      this.lastResult = null
      try {
        const res = await apiFetch('/api/self_update', { method: 'POST' })
        if (!res.ok) {
          const result: AdminActionResult = {
            tone: 'error',
            message: `HTTP ${res.status}`,
            willRestart: false,
          }
          this.lastResult = result
          return result
        }
        const data: SelfUpdateResponse = await res.json()
        const result: AdminActionResult =
          data.status === 'updated'
            ? {
                tone: 'success',
                message:
                  data.message ?? 'Обновлено. Сервер перезапускается…',
                willRestart: true,
              }
            : data.status === 'up_to_date'
              ? {
                  tone: 'info',
                  message: data.message ?? 'Уже актуально — обновлений нет.',
                  willRestart: false,
                }
              : {
                  tone: 'error',
                  message: data.message ?? 'Неизвестная ошибка',
                  willRestart: false,
                }
        this.lastResult = result
        return result
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
        const result: AdminActionResult = {
          tone: 'error',
          message: `Сбой запроса: ${describe(err)}`,
          willRestart: false,
        }
        this.lastResult = result
        return result
      } finally {
        this.selfUpdateBusy = false
      }
    },

    /**
     * Drop and recreate the SQLite DB. The backend protects the
     * destructive endpoint with a short-lived token: GET the token,
     * then POST `?confirm=<token>` within 60 seconds.
     */
    async resetDatabase(): Promise<AdminActionResult> {
      const session = useSessionStore()
      if (!session.canCallApi) {
        return {
          tone: 'error',
          message: 'Сессия не авторизована',
          willRestart: false,
        }
      }
      this.resetBusy = true
      this.lastResult = null
      try {
        const tokenRes = await apiFetch('/api/reset_database/token')
        if (!tokenRes.ok) {
          const result: AdminActionResult = {
            tone: 'error',
            message: `HTTP ${tokenRes.status} при получении токена`,
            willRestart: false,
          }
          this.lastResult = result
          return result
        }
        const tokenData: ResetTokenResponse = await tokenRes.json()
        const res = await apiFetch(
          `/api/reset_database?confirm=${encodeURIComponent(tokenData.token)}`,
          { method: 'POST' },
        )
        if (!res.ok) {
          const result: AdminActionResult = {
            tone: 'error',
            message: `HTTP ${res.status}`,
            willRestart: false,
          }
          this.lastResult = result
          return result
        }
        const data: { status?: string; message?: string } = await res.json()
        const result: AdminActionResult =
          data.status === 'success'
            ? {
                tone: 'success',
                message:
                  data.message ??
                  'База удалена. Сервер перезапускается с пустой базой…',
                willRestart: true,
              }
            : {
                tone: 'error',
                message: data.message ?? 'Неизвестная ошибка',
                willRestart: false,
              }
        this.lastResult = result
        return result
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
        const result: AdminActionResult = {
          tone: 'error',
          message: `Сбой запроса: ${describe(err)}`,
          willRestart: false,
        }
        this.lastResult = result
        return result
      } finally {
        this.resetBusy = false
      }
    },

    /**
     * Upload a SQLite file to `/api/database_import`. The backend
     * validates the magic bytes, snapshots the current DB into
     * `backups/pre_import_*.db`, then atomically replaces and
     * restarts.
     */
    async importDatabase(file: File): Promise<AdminActionResult> {
      const session = useSessionStore()
      if (!session.canCallApi) {
        return {
          tone: 'error',
          message: 'Сессия не авторизована',
          willRestart: false,
        }
      }
      this.importBusy = true
      this.lastResult = null
      try {
        const form = new FormData()
        form.append('file', file)
        const res = await apiFetch('/api/database_import', {
          method: 'POST',
          body: form,
        })
        if (!res.ok) {
          // The endpoint surfaces JSON error bodies for known-bad files
          // (e.g. magic-byte mismatch); try to read it before bailing.
          let detail = `HTTP ${res.status}`
          try {
            const data: DatabaseImportResponse = await res.json()
            if (data.message) detail = data.message
          } catch {
            /* ignore — non-JSON body */
          }
          const result: AdminActionResult = {
            tone: 'error',
            message: detail,
            willRestart: false,
          }
          this.lastResult = result
          return result
        }
        const data: DatabaseImportResponse = await res.json()
        const result: AdminActionResult =
          data.status === 'success'
            ? {
                tone: 'success',
                message:
                  data.message ?? 'База импортирована. Сервер перезапускается…',
                willRestart: true,
              }
            : {
                tone: 'error',
                message: data.message ?? 'Неизвестная ошибка',
                willRestart: false,
              }
        this.lastResult = result
        return result
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
        const result: AdminActionResult = {
          tone: 'error',
          message: `Сбой запроса: ${describe(err)}`,
          willRestart: false,
        }
        this.lastResult = result
        return result
      } finally {
        this.importBusy = false
      }
    },

    /**
     * GET /api/database_export — download the SQLite file. We pull it
     * through `apiFetch` (so the bearer token is attached when auth
     * is on) and trigger a client-side download via a temp anchor —
     * same approach as the legacy `exportCollections` (~line 1765).
     */
    async exportDatabase(): Promise<AdminActionResult> {
      const session = useSessionStore()
      if (!session.canCallApi) {
        return {
          tone: 'error',
          message: 'Сессия не авторизована',
          willRestart: false,
        }
      }
      this.exportBusy = true
      this.lastResult = null
      try {
        const res = await apiFetch('/api/database_export')
        if (!res.ok) {
          const result: AdminActionResult = {
            tone: 'error',
            message: `HTTP ${res.status}`,
            willRestart: false,
          }
          this.lastResult = result
          return result
        }
        const blob = await res.blob()
        const url = URL.createObjectURL(blob)
        try {
          const a = document.createElement('a')
          a.href = url
          a.download = 'app_data.db'
          document.body.appendChild(a)
          a.click()
          a.remove()
        } finally {
          URL.revokeObjectURL(url)
        }
        const result: AdminActionResult = {
          tone: 'success',
          message: 'Экспорт начат — проверьте папку загрузок.',
          willRestart: false,
        }
        this.lastResult = result
        return result
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
        const result: AdminActionResult = {
          tone: 'error',
          message: `Сбой запроса: ${describe(err)}`,
          willRestart: false,
        }
        this.lastResult = result
        return result
      } finally {
        this.exportBusy = false
      }
    },

    /**
     * GET /api/backup/download — server snapshots `app_data.db` into
     * `backups/app_data-<ts>.db` and streams the resulting file. We
     * route through `apiFetch` for the bearer-token attachment, then
     * trigger a client-side download by anchoring the blob. Different
     * from `exportDatabase` because it writes a versioned snapshot
     * server-side first (useful as a manual checkpoint before a
     * destructive op).
     */
    async downloadBackup(): Promise<AdminActionResult> {
      const session = useSessionStore()
      if (!session.canCallApi) {
        return {
          tone: 'error',
          message: 'Сессия не авторизована',
          willRestart: false,
        }
      }
      this.backupBusy = true
      this.lastResult = null
      try {
        const res = await apiFetch('/api/backup/download')
        if (!res.ok) {
          const result: AdminActionResult = {
            tone: 'error',
            message: `HTTP ${res.status}`,
            willRestart: false,
          }
          this.lastResult = result
          return result
        }
        const blob = await res.blob()
        // Pull the filename out of `Content-Disposition` so we keep
        // the server-side timestamp; fall back to a generic name.
        const cd = res.headers.get('Content-Disposition') ?? ''
        const m = /filename="?([^";]+)"?/i.exec(cd)
        const filename = m?.[1]?.trim() || 'app_data-backup.db'
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
        const result: AdminActionResult = {
          tone: 'success',
          message: `Бэкап сохранён (${filename}).`,
          willRestart: false,
        }
        this.lastResult = result
        return result
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
        const result: AdminActionResult = {
          tone: 'error',
          message: `Сбой запроса: ${describe(err)}`,
          willRestart: false,
        }
        this.lastResult = result
        return result
      } finally {
        this.backupBusy = false
      }
    },

    /**
     * GET /api/export?fmt=&category_id= — dump the `items` table as
     * JSON or CSV, optionally narrowed to one category. `categoryId`
     * defaults to `-1` (all video) to match the backend's own default.
     */
    async exportItems(
      fmt: 'json' | 'csv',
      categoryId: number = -1,
    ): Promise<AdminActionResult> {
      const session = useSessionStore()
      if (!session.canCallApi) {
        return {
          tone: 'error',
          message: 'Сессия не авторизована',
          willRestart: false,
        }
      }
      this.itemsExportBusy = true
      this.lastResult = null
      try {
        const params = new URLSearchParams({
          fmt,
          category_id: String(categoryId),
        })
        const res = await apiFetch(`/api/export?${params.toString()}`)
        if (!res.ok) {
          const result: AdminActionResult = {
            tone: 'error',
            message: `HTTP ${res.status}`,
            willRestart: false,
          }
          this.lastResult = result
          return result
        }
        const blob = await res.blob()
        const cd = res.headers.get('Content-Disposition') ?? ''
        const m = /filename="?([^";]+)"?/i.exec(cd)
        const filename = m?.[1]?.trim() || `export.${fmt}`
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
        const result: AdminActionResult = {
          tone: 'success',
          message: `Экспорт готов (${filename}).`,
          willRestart: false,
        }
        this.lastResult = result
        return result
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
        const result: AdminActionResult = {
          tone: 'error',
          message: `Сбой запроса: ${describe(err)}`,
          willRestart: false,
        }
        this.lastResult = result
        return result
      } finally {
        this.itemsExportBusy = false
      }
    },

    /**
     * POST /api/rebuild_fts — rebuilds the SQLite full-text-search
     * index. Used after manual DB edits or schema repairs. Returns a
     * plain success / error banner.
     */
    async rebuildFts(): Promise<AdminActionResult> {
      const session = useSessionStore()
      if (!session.canCallApi) {
        return {
          tone: 'error',
          message: 'Сессия не авторизована',
          willRestart: false,
        }
      }
      this.rebuildFtsBusy = true
      this.lastResult = null
      try {
        const res = await apiFetch('/api/rebuild_fts', { method: 'POST' })
        if (!res.ok) {
          const result: AdminActionResult = {
            tone: 'error',
            message: `HTTP ${res.status}`,
            willRestart: false,
          }
          this.lastResult = result
          return result
        }
        const result: AdminActionResult = {
          tone: 'success',
          message: 'FTS-индекс перестроен.',
          willRestart: false,
        }
        this.lastResult = result
        return result
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
        const result: AdminActionResult = {
          tone: 'error',
          message: `Сбой запроса: ${describe(err)}`,
          willRestart: false,
        }
        this.lastResult = result
        return result
      } finally {
        this.rebuildFtsBusy = false
      }
    },
    /**
     * POST /api/database_clear — deletes all items/collections but
     * keeps auth and settings.
     */
    async clearDatabase(): Promise<AdminActionResult> {
      const session = useSessionStore()
      if (!session.canCallApi) {
        return {
          tone: 'error',
          message: 'Сессия не авторизована',
          willRestart: false,
        }
      }
      this.clearDatabaseBusy = true
      this.lastResult = null
      try {
        const res = await apiFetch('/api/database_clear', { method: 'POST' })
        if (!res.ok) {
          const result: AdminActionResult = {
            tone: 'error',
            message: `HTTP ${res.status}`,
            willRestart: false,
          }
          this.lastResult = result
          return result
        }
        const data: { status?: string; message?: string } = await res.json()
        const result: AdminActionResult =
          data.status === 'success'
            ? {
                tone: 'success',
                message: data.message ?? 'Медиа-данные очищены. Страница будет перезагружена…',
                willRestart: true,
              }
            : {
                tone: 'error',
                message: data.message ?? 'Неизвестная ошибка',
                willRestart: false,
              }
        this.lastResult = result
        return result
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
        const result: AdminActionResult = {
          tone: 'error',
          message: `Сбой запроса: ${describe(err)}`,
          willRestart: false,
        }
        this.lastResult = result
        return result
      } finally {
        this.clearDatabaseBusy = false
      }
    },

    /**
     * POST /api/self_update?skip_pull=true — Rebuilds (pip, npm) and restarts
     * without touching git.
     */
    async rebuildServer(): Promise<AdminActionResult> {
      const session = useSessionStore()
      if (!session.canCallApi) {
        return {
          tone: 'error',
          message: 'Сессия не авторизована',
          willRestart: false,
        }
      }
      this.rebuildBusy = true
      this.lastResult = null
      try {
        const res = await apiFetch('/api/self_update?skip_pull=true', {
          method: 'POST',
        })
        if (!res.ok) {
          const result: AdminActionResult = {
            tone: 'error',
            message: `HTTP ${res.status}`,
            willRestart: false,
          }
          this.lastResult = result
          return result
        }
        const data: { status?: string; message?: string } = await res.json()
        const result: AdminActionResult = {
          tone: 'success',
          message: data.message ?? 'Пересборка начата. Сервер перезапустится…',
          willRestart: true,
        }
        this.lastResult = result
        return result
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
        const result: AdminActionResult = {
          tone: 'error',
          message: `Сбой запроса: ${describe(err)}`,
          willRestart: false,
        }
        this.lastResult = result
        return result
      } finally {
        this.rebuildBusy = false
      }
    },

    /**
     * POST /api/restart_server — Triggers RESTART_COMMAND.
     */
    async restartServer(): Promise<AdminActionResult> {
      const session = useSessionStore()
      if (!session.canCallApi) {
        return {
          tone: 'error',
          message: 'Сессия не авторизована',
          willRestart: false,
        }
      }
      this.restartBusy = true
      this.lastResult = null
      try {
        const res = await apiFetch('/api/restart_server', { method: 'POST' })
        if (!res.ok) {
          const result: AdminActionResult = {
            tone: 'error',
            message: `HTTP ${res.status}`,
            willRestart: false,
          }
          this.lastResult = result
          return result
        }
        const data: { status?: string; message?: string } = await res.json()
        const result: AdminActionResult = {
          tone: 'success',
          message: data.message ?? 'Команда отправлена. Сервер перезагружается…',
          willRestart: true,
        }
        this.lastResult = result
        return result
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
        const result: AdminActionResult = {
          tone: 'error',
          message: `Сбой запроса: ${describe(err)}`,
          willRestart: false,
        }
        this.lastResult = result
        return result
      } finally {
        this.restartBusy = false
      }
    },

    async exportWatchedOrRatings(
      path: string,
      defaultFilename: string,
    ): Promise<AdminActionResult> {
      const session = useSessionStore()
      if (!session.canCallApi) {
        return {
          tone: 'error',
          message: 'Сессия не авторизована',
          willRestart: false,
        }
      }
      this.itemsExportBusy = true
      this.lastResult = null
      try {
        const res = await apiFetch(path)
        if (!res.ok) {
          const result: AdminActionResult = {
            tone: 'error',
            message: `HTTP ${res.status}`,
            willRestart: false,
          }
          this.lastResult = result
          return result
        }
        const blob = await res.blob()
        const cd = res.headers.get('Content-Disposition') ?? ''
        const m = /filename="?([^";]+)"?/i.exec(cd)
        const filename = m?.[1]?.trim() || defaultFilename
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
        const result: AdminActionResult = {
          tone: 'success',
          message: `Экспорт готов (${filename}).`,
          willRestart: false,
        }
        this.lastResult = result
        return result
      } catch (err) {
        if (err instanceof UnauthorizedError) {
          session.handleUnauthorized(err)
        }
        const result: AdminActionResult = {
          tone: 'error',
          message: `Сбой запроса: ${describe(err)}`,
          willRestart: false,
        }
        this.lastResult = result
        return result
      } finally {
        this.itemsExportBusy = false
      }
    },
  },
})
