// ROADMAP Stage 10.7e — log viewer state store.
//
// Pinia replacement for the legacy log panel state in `index.html`:
//   - `selectedLogType` / `syncLogs` (current buffer),
//   - `userSelectedLog` flag (`index.html:1180`) that prevents the
//     auto-status fan-out from clobbering a manual tab selection,
//   - `fetchLogs()` (`index.html:1911-1923`) — pulls the tail of the
//     selected log from `GET /api/sync_log?log_type=...`,
//   - `startLogPolling()` (`index.html:1924-1928`) — 2 s poll while
//     the panel is open and WS is down,
//   - WS `log` message fan-in — the legacy handler appended every
//     `{type:'log', key, data}` chunk to `syncLogs` when its `key`
//     matched the currently selected tab.
//
// The store also exposes `clear()` / `download()` shortcuts mapped
// onto `/api/clear_log` and `/api/download_log` (proxied through
// `apiFetch` so the bearer token rides along; for the download we
// open a one-off request and stream the response into a blob URL
// because the legacy `<a href download>` cannot carry auth headers).

import { defineStore } from 'pinia'

import { apiFetch } from '../api/client'
import { useSessionStore } from './session'
import { useToastStore } from './toast'
import type { ProcessKey } from './sync'

/** Tab id → human label. Mirrors `logTypes` at `index.html:1171`. */
export const LOG_TYPE_LABELS: Record<string, string> = {
  full_pipeline: 'Полный цикл',
  reprocess: 'Обновление',
  video: 'Видео',
  other: 'Остальное',
  fix: 'Поиск',
  fix_poiskkino: 'PoiskKino',
  rezka: 'Rezka',
  rezka_collections: 'Rezka Папки',
  kinopub: 'kino.pub',
  kinopub_collections: 'kino.pub Папки',
  user: 'CSV',
  cleanup: 'Чистка',
  single_update: 'Карточка',
  tmdb: 'TMDB Папки',
}

export type LogType = keyof typeof LOG_TYPE_LABELS

/** Tab id → on-disk filename. Mirrors `currentLogFilename` at
 *  `index.html:1251-1264`. Exposed mostly so the panel can render
 *  the file path next to the tabs (visual continuity with legacy). */
export const LOG_TYPE_FILENAMES: Record<LogType, string> = {
  full_pipeline: 'full_pipeline_log.txt',
  reprocess: 'reprocess_log.txt',
  video: 'sync_video_log.txt',
  other: 'sync_other_log.txt',
  fix: 'fix_tech_log.txt',
  fix_poiskkino: 'fix_poiskkino_log.txt',
  rezka: 'sync_rezka_log.txt',
  rezka_collections: 'rezka_collections_log.txt',
  kinopub: 'sync_kinopub_log.txt',
  kinopub_collections: 'kinopub_collections_log.txt',
  user: 'user_sync_log.txt',
  cleanup: 'cleanup_log.txt',
  single_update: 'single_update_log.txt',
  tmdb: 'sync_tmdb_log.txt',
}

/** Process key (as it appears in `process_status`) → log tab id.
 *  Mirrors the inline map at `index.html:1939-1949`. Some legacy
 *  process keys differ from log type ids (e.g. `sync_video` vs
 *  `video`). */
export const PROCESS_TO_LOG: Partial<Record<ProcessKey, LogType>> = {
  full_pipeline: 'full_pipeline',
  sync_video: 'video',
  sync_other: 'other',
  reprocess: 'reprocess',
  fix: 'fix',
  poiskkino: 'fix_poiskkino',
  rezka: 'rezka',
  rezka_collections: 'rezka_collections',
  kinopub: 'kinopub',
  kinopub_collections: 'kinopub_collections',
  user: 'user',
  cleanup: 'cleanup',
  single_update: 'single_update',
  tmdb: 'tmdb',
}

/** Inverse mapping log type → ProcessKey for the panel header's
 *  "currently active stop" button (`currentStatusKey` in
 *  `index.html:1255-1268`). */
export const LOG_TO_PROCESS: Record<LogType, ProcessKey> = {
  full_pipeline: 'full_pipeline',
  video: 'sync_video',
  other: 'sync_other',
  reprocess: 'reprocess',
  fix: 'fix',
  fix_poiskkino: 'poiskkino',
  rezka: 'rezka',
  rezka_collections: 'rezka_collections',
  kinopub: 'kinopub',
  kinopub_collections: 'kinopub_collections',
  user: 'user',
  cleanup: 'cleanup',
  single_update: 'single_update',
  tmdb: 'tmdb',
}

interface LogsStoreState {
  selectedType: LogType
  /** Plain-text tail of the currently selected log. */
  content: string
  loading: boolean
  error: string | null
  /** `true` after the user explicitly clicked a tab. Disables the
   *  auto fan-out that would otherwise switch the panel to whichever
   *  process is currently running. Legacy `userSelectedLog`. */
  userSelected: boolean
  panelOpen: boolean
}

const POLL_INTERVAL_MS = 2000

// Module-scoped — these aren't reactive and shouldn't be serialized.
let pollTimer: ReturnType<typeof setInterval> | null = null

function isLogType(value: string): value is LogType {
  return Object.prototype.hasOwnProperty.call(LOG_TYPE_LABELS, value)
}

export const useLogsStore = defineStore('logs', {
  state: (): LogsStoreState => ({
    selectedType: 'reprocess',
    content: '',
    loading: false,
    error: null,
    userSelected: false,
    panelOpen: false,
  }),

  getters: {
    currentFilename(state): string {
      return LOG_TYPE_FILENAMES[state.selectedType]
    },
    currentProcessKey(state): ProcessKey {
      return LOG_TO_PROCESS[state.selectedType]
    },
  },

  actions: {
    /**
     * Fetch the tail of `selectedType` and overwrite `content`. The
     * cache-buster `?t=…` in legacy was redundant because the route
     * itself sets `Cache-Control: no-store`, so we drop it.
     */
    async refresh(): Promise<void> {
      const session = useSessionStore()
      if (!session.canCallApi) return
      this.loading = true
      this.error = null
      try {
        const res = await apiFetch(
          `/api/sync_log?log_type=${encodeURIComponent(this.selectedType)}`,
        )
        if (!res.ok) {
          this.error = `Сервер ответил ${res.status}`
          return
        }
        const data = (await res.json()) as { log?: string; filename?: string }
        this.content = data.log ?? ''
      } catch (err) {
        this.error = err instanceof Error ? err.message : String(err)
      } finally {
        this.loading = false
      }
    },

    /** Switch the active tab. `userInitiated=true` flips the
     *  `userSelected` lock so subsequent WS process changes don't
     *  swap the tab out from under the user. */
    async selectType(type: LogType, userInitiated = true): Promise<void> {
      if (this.selectedType === type) {
        if (this.panelOpen) await this.refresh()
        return
      }
      this.selectedType = type
      if (userInitiated) this.userSelected = true
      if (this.panelOpen) await this.refresh()
    },

    /** Forwarded from the sync store's WS dispatcher: a `{type:'log',
     *  key, data}` message. Append the chunk if it targets the
     *  currently visible tab. */
    appendChunk(key: ProcessKey, chunk: string): void {
      const logType = PROCESS_TO_LOG[key]
      if (!logType) return
      if (logType !== this.selectedType) return
      this.content = `${this.content}${chunk}`
    },

    /** Called from `sync.fetchStatus` / WS snapshot. Sets the tab to
     *  whatever process is currently running, but only if the user
     *  hasn't pinned a specific tab. */
    autoFollow(runningKeys: ProcessKey[]): void {
      if (this.userSelected) return
      for (const key of runningKeys) {
        const logType = PROCESS_TO_LOG[key]
        if (!logType) continue
        if (this.selectedType !== logType) {
          this.selectedType = logType
          if (this.panelOpen) void this.refresh()
        }
        return
      }
    },

    async clear(): Promise<boolean> {
      const session = useSessionStore()
      if (!session.canCallApi) return false
      const toast = useToastStore()
      try {
        const res = await apiFetch(
          `/api/clear_log?log_type=${encodeURIComponent(this.selectedType)}`,
          { method: 'POST' },
        )
        if (!res.ok) {
          toast.error(`Очистка лога: ошибка ${res.status}`)
          return false
        }
        this.content = ''
        await this.refresh()
        return true
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Очистка лога не удалась')
        return false
      }
    },

    /**
     * Stream the current log from `/api/download_log` and trigger a
     * browser-side save. Goes through `apiFetch` so the bearer
     * token is included — legacy used a bare `<a download>` link
     * which silently broke when auth was enabled.
     */
    async download(): Promise<void> {
      const session = useSessionStore()
      if (!session.canCallApi) return
      const toast = useToastStore()
      try {
        const res = await apiFetch(
          `/api/download_log?log_type=${encodeURIComponent(this.selectedType)}`,
        )
        if (!res.ok) {
          toast.error(`Скачивание лога: ошибка ${res.status}`)
          return
        }
        const blob = await res.blob()
        const url = window.URL.createObjectURL(blob)
        try {
          const link = window.document.createElement('a')
          link.href = url
          link.download = LOG_TYPE_FILENAMES[this.selectedType]
          window.document.body.appendChild(link)
          link.click()
          window.document.body.removeChild(link)
        } finally {
          window.URL.revokeObjectURL(url)
        }
      } catch (err) {
        toast.error(
          err instanceof Error ? err.message : 'Скачивание лога не удалось',
        )
      }
    },

    open(): void {
      this.panelOpen = true
      void this.refresh()
      this.startPolling()
    },

    close(): void {
      this.panelOpen = false
      this.stopPolling()
    },

    startPolling(): void {
      if (pollTimer) return
      pollTimer = setInterval(() => {
        if (this.panelOpen) void this.refresh()
      }, POLL_INTERVAL_MS)
    },

    stopPolling(): void {
      if (pollTimer) {
        clearInterval(pollTimer)
        pollTimer = null
      }
    },

    /** Drop pinned-tab state. Used when the user explicitly cycles
     *  back to the "auto" mode (UI is just the close button for
     *  now — see `LogsPanel.vue`). */
    resetUserSelection(): void {
      this.userSelected = false
    },
  },
})

export { isLogType }
