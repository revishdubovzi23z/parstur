// ROADMAP Stage 10.7d — sync/cleanup/full-pipeline controls + WS.
//
// Pinia replacement for three intertwined pieces of legacy state on
// the root Vue instance in `index.html`:
//
//  - `statuses` / `progress` / `processStartTimes` / `wsConnected` —
//    populated from the `/ws` stream (see `connectWebSocket()` in
//    `index.html:1369-1462`),
//  - the `startSync` / `startFix` / `startFullPipeline` /
//    `startCleanup` / `startRezkaSync` / `startRezkaCollections` /
//    `stopProcess` methods (`index.html:1987-2154`),
//  - the polling fallback (`startStatusPolling` /
//    `fetchStatus` at `index.html:1929-1967`) used when the WS
//    cannot connect.
//
// The store owns the WS lifecycle: ticket negotiation, reconnect
// with backoff, fan-out of `status` / `progress` / `rezka_session`
// messages, and a polling fallback. Components subscribe to
// `statuses` / `progress` and trigger `start*` / `stop` actions.
//
// `log` messages and `rezka_sync_error` toasts are passed through to
// the existing `toast` store but the log buffer / log viewer is
// owned by the upcoming 10.7e store and is not implemented here.

import { defineStore } from 'pinia'

import { apiFetch, getStoredToken } from '../api/client'
import { useFeedStore } from './feed'
import { useItemsStore } from './items'
import { useLogsStore } from './logs'
import { useSessionStore } from './session'
import { useToastStore } from './toast'

/** All keys the backend's `process_status` dict can carry. Must stay
 *  in sync with `PROCESS_KEYS` in `main.py`. */
export const PROCESS_KEYS = [
  'sync_video',
  'sync_other',
  'fix',
  'poiskkino',
  'reprocess',
  'user',
  'cleanup',
  'rezka',
  'rezka_collections',
  'kinopub',
  'kinopub_collections',
  'tmdb',
  'trakt_collections',
  'full_pipeline',
  'single_update',
] as const

export type ProcessKey = (typeof PROCESS_KEYS)[number]

export type ProcessStatus =
  | 'idle'
  | 'queued'
  | 'running'
  | 'completed'
  | 'stopped'
  | 'error'

export interface ProgressEntry {
  current: number
  total: number
}

export type RezkaSessionState = 'down' | 'connecting' | 'up'

interface StatusMessage {
  type: 'status'
  /** Per-key update. */
  key?: ProcessKey
  value?: ProcessStatus
  /** Full snapshot. */
  statuses?: Record<string, ProcessStatus>
  progress?: Record<string, ProgressEntry>
  checkpoints?: Record<string, boolean>
  rezka_session?: RezkaSessionState
}

interface ProgressMessage {
  type: 'progress'
  key?: ProcessKey
  current?: number
  total?: number
  progress?: Record<string, ProgressEntry>
}

interface LogMessage {
  type: 'log'
  key?: ProcessKey
  data?: string
}

interface RezkaSyncErrorMessage {
  type: 'rezka_sync_error'
  message?: string
}

interface RezkaSessionMessage {
  type: 'rezka_session'
  state?: RezkaSessionState
}

interface ItemUpdatedMessage {
  type: 'item_updated'
  item_id: number
}

type WsMessage =
  | StatusMessage
  | ProgressMessage
  | LogMessage
  | RezkaSyncErrorMessage
  | RezkaSessionMessage
  | ItemUpdatedMessage

export interface SyncFilters {
  minYear: number | null
  maxYear: number | null
  minDate: string | null
}

interface SyncStoreState {
  statuses: Record<ProcessKey, ProcessStatus>
  progress: Record<ProcessKey, ProgressEntry>
  checkpoints: Record<ProcessKey, boolean>
  rezkaSession: RezkaSessionState
  wsConnected: boolean
  /** Last UI-visible error from an action (start_x / stop). */
  lastError: string | null
}

const RECONNECT_DELAY_MS = 3000
const POLL_INTERVAL_MS = 5000

function emptyProgress(): Record<ProcessKey, ProgressEntry> {
  const acc = {} as Record<ProcessKey, ProgressEntry>
  for (const key of PROCESS_KEYS) acc[key] = { current: 0, total: 0 }
  return acc
}

function emptyCheckpoints(): Record<ProcessKey, boolean> {
  const acc = {} as Record<ProcessKey, boolean>
  for (const key of PROCESS_KEYS) acc[key] = false
  return acc
}

function idleStatuses(): Record<ProcessKey, ProcessStatus> {
  const acc = {} as Record<ProcessKey, ProcessStatus>
  for (const key of PROCESS_KEYS) acc[key] = 'idle'
  return acc
}

function describe(err: unknown): string {
  if (err instanceof Error) return err.message
  return String(err)
}

function isProcessKey(value: string): value is ProcessKey {
  return (PROCESS_KEYS as readonly string[]).includes(value)
}

// Module-scoped handles for the WS / polling lifecycle. They live
// outside the Pinia state because they are not reactive and must not
// be serialised. `setActivePinia` in tests resets state but not
// these — the WS hooks below always re-check `currentSocket`.
let currentSocket: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
/** Set true by `disconnect()` so the `onclose` handler doesn't try
 *  to reconnect when we deliberately tore the socket down. */
let intentionallyClosed = false

/** Resolve the `ws:` / `wss:` URL for the `/ws` endpoint, taking
 *  HTTPS into account. Mirrors `index.html:1391-1392`. */
function buildWsUrl(query: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/ws${query}`
}

export const useSyncStore = defineStore('sync', {
  state: (): SyncStoreState => ({
    statuses: idleStatuses(),
    progress: emptyProgress(),
    checkpoints: emptyCheckpoints(),
    rezkaSession: 'down',
    wsConnected: false,
    lastError: null,
  }),

  getters: {
    /** Map of `process_key → boolean` for "currently running". */
    isRunning(state): Record<ProcessKey, boolean> {
      const acc = {} as Record<ProcessKey, boolean>
      for (const key of PROCESS_KEYS) {
        acc[key] = state.statuses[key] === 'running'
      }
      return acc
    },
    /** `true` if any tracked job is `running` or `queued`. Used to
     *  disable mutually-exclusive Start buttons. */
    anyBusy(state): boolean {
      for (const key of PROCESS_KEYS) {
        const s = state.statuses[key]
        if (s === 'running' || s === 'queued') return true
      }
      return false
    },
  },

  actions: {
    /**
     * Apply a single status update or a full snapshot. Splitting it
     * out makes the WS `onmessage` handler easier to test in
     * isolation.
     */
    applyStatusMessage(msg: StatusMessage): void {
      // Track single_update transitions — we want to nudge the item
      // detail store to refresh whenever the backend reports a
      // terminal state for that key (legacy `index.html` re-fetched
      // the card manually after the user clicked "reprocess").
      const prevSingleUpdate = this.statuses.single_update
      if (msg.statuses) {
        for (const key of Object.keys(msg.statuses)) {
          if (isProcessKey(key)) {
            this.statuses[key] = msg.statuses[key]
          }
        }
      } else if (msg.key && msg.value && isProcessKey(msg.key)) {
        this.statuses[msg.key] = msg.value
      }
      const nextSingleUpdate = this.statuses.single_update
      if (
        prevSingleUpdate === 'running' &&
        (nextSingleUpdate === 'completed' || nextSingleUpdate === 'stopped')
      ) {
        useItemsStore().onSingleUpdateCompleted()
      }
      if (msg.progress) {
        for (const key of Object.keys(msg.progress)) {
          if (isProcessKey(key)) {
            this.progress[key] = msg.progress[key]
          }
        }
      }
      if (msg.checkpoints) {
        for (const key of Object.keys(msg.checkpoints)) {
          if (isProcessKey(key)) {
            this.checkpoints[key] = msg.checkpoints[key]
          }
        }
      }
      if (msg.rezka_session) {
        this.rezkaSession = msg.rezka_session
      }
      // Hand off the "who is running now" hint to the logs store so
      // the log panel can auto-follow the active process (legacy
      // `index.html:1936-1957`).
      const running: ProcessKey[] = []
      for (const key of PROCESS_KEYS) {
        if (this.statuses[key] === 'running') running.push(key)
      }
      if (running.length > 0) {
        useLogsStore().autoFollow(running)
      }
    },

    applyProgressMessage(msg: ProgressMessage): void {
      if (msg.key && isProcessKey(msg.key)) {
        this.progress[msg.key] = {
          current: msg.current ?? 0,
          total: msg.total ?? 0,
        }
      } else if (msg.progress) {
        for (const key of Object.keys(msg.progress)) {
          if (isProcessKey(key)) {
            this.progress[key] = msg.progress[key]
          }
        }
      }
    },

    /** Dispatch a single raw WS payload. Exposed for tests. */
    handleMessage(raw: string): void {
      let msg: WsMessage
      try {
        msg = JSON.parse(raw) as WsMessage
      } catch {
        return
      }
      if (msg.type === 'status') {
        this.applyStatusMessage(msg)
      } else if (msg.type === 'progress') {
        this.applyProgressMessage(msg)
      } else if (msg.type === 'rezka_session') {
        if (msg.state) this.rezkaSession = msg.state
      } else if (msg.type === 'rezka_sync_error') {
        const toast = useToastStore()
        toast.error(msg.message ?? 'Rezka sync error')
      } else if (msg.type === 'log') {
        if (msg.key && msg.data && isProcessKey(msg.key)) {
          useLogsStore().appendChunk(msg.key, msg.data)
        }
      } else if (msg.type === 'item_updated') {
        // Broad refresh for the feed card
        void useFeedStore().updateItemById(msg.item_id)
        // If the same item is currently open in a modal, refresh the modal too
        const itemsStore = useItemsStore()
        if (itemsStore.item?.id === msg.item_id) {
          void itemsStore.refresh()
        }
      }
    },

    /**
     * Open the `/ws` connection. Idempotent — calling twice reuses
     * the existing socket. The function negotiates a one-time
     * ticket from `/api/ws/ticket` so the long-lived bearer token
     * never appears in a query string (which would leak into proxy
     * access logs). Falls back to `?token=` for backward compat,
     * matching legacy behaviour in `index.html:1376-1389`.
     */
    async connect(): Promise<void> {
      const session = useSessionStore()
      if (!session.canCallApi) return
      if (currentSocket && currentSocket.readyState <= WebSocket.OPEN) return

      intentionallyClosed = false
      this.cancelReconnect()

      let query = ''
      if (session.status === 'authenticated') {
        const token = getStoredToken()
        try {
          const res = await apiFetch('/api/ws/ticket', { method: 'POST' })
          if (res.ok) {
            const { ticket } = (await res.json()) as { ticket?: string }
            if (ticket) {
              query = `?ticket=${encodeURIComponent(ticket)}`
            } else if (token) {
              query = `?token=${encodeURIComponent(token)}`
            }
          } else if (token) {
            query = `?token=${encodeURIComponent(token)}`
          }
        } catch {
          if (token) query = `?token=${encodeURIComponent(token)}`
        }
      }

      const socket = new WebSocket(buildWsUrl(query))
      currentSocket = socket

      socket.onopen = () => {
        this.wsConnected = true
        this.stopPolling()
      }
      socket.onclose = () => {
        this.wsConnected = false
        if (currentSocket === socket) currentSocket = null
        if (!intentionallyClosed) {
          this.startPolling()
          this.scheduleReconnect()
        }
      }
      socket.onerror = () => {
        try {
          socket.close()
        } catch {
          /* noop */
        }
      }
      socket.onmessage = (ev: MessageEvent) => {
        if (typeof ev.data === 'string') this.handleMessage(ev.data)
      }
    },

    /** Tear down the WS connection without scheduling a reconnect.
     *  Used on logout / app teardown. */
    disconnect(): void {
      intentionallyClosed = true
      this.cancelReconnect()
      this.stopPolling()
      if (currentSocket) {
        try {
          currentSocket.close()
        } catch {
          /* noop */
        }
        currentSocket = null
      }
      this.wsConnected = false
    },

    scheduleReconnect(): void {
      if (reconnectTimer) return
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null
        void this.connect()
      }, RECONNECT_DELAY_MS)
    },

    cancelReconnect(): void {
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
    },

    /**
     * Pull `/api/process_status` once and merge into state. Used by
     * the polling fallback and on demand right after firing a
     * mutating action (legacy did `setTimeout(fetchStatus, 500)`
     * after `stopProcess` — we keep the same).
     */
    async fetchStatus(): Promise<void> {
      const session = useSessionStore()
      if (!session.canCallApi) return
      try {
        const res = await apiFetch('/api/process_status')
        if (!res.ok) return
        const data = (await res.json()) as {
          statuses?: Record<string, ProcessStatus>
          progress?: Record<string, ProgressEntry>
          checkpoints?: Record<string, boolean>
        }
        if (data.statuses) {
          for (const key of Object.keys(data.statuses)) {
            if (isProcessKey(key)) this.statuses[key] = data.statuses[key]
          }
        }
        if (data.progress) {
          for (const key of Object.keys(data.progress)) {
            if (isProcessKey(key)) this.progress[key] = data.progress[key]
          }
        }
        if (data.checkpoints) {
          for (const key of Object.keys(data.checkpoints)) {
            if (isProcessKey(key)) this.checkpoints[key] = data.checkpoints[key]
          }
        }
        const running: ProcessKey[] = []
        for (const key of PROCESS_KEYS) {
          if (this.statuses[key] === 'running') running.push(key)
        }
        if (running.length > 0) useLogsStore().autoFollow(running)
      } catch {
        /* polling failures are non-fatal */
      }
    },

    startPolling(): void {
      if (pollTimer) return
      void this.fetchStatus()
      pollTimer = setInterval(() => {
        if (!this.wsConnected) void this.fetchStatus()
      }, POLL_INTERVAL_MS)
    },

    stopPolling(): void {
      if (pollTimer) {
        clearInterval(pollTimer)
        pollTimer = null
      }
    },

    // ----- Mutating actions ------------------------------------------------
    //
    // All start_* / stop endpoints return either:
    //   * 200 { status: 'started' | 'stopped' | 'not_running' } — happy path
    //   * 400 { detail: '...' } — `check_any_running` rejected because
    //     another job is busy. We surface `detail` as a toast and
    //     return `false` so the caller can leave its modal open.
    //   * 401 — handled by apiFetch (throws UnauthorizedError).
    //   * Other non-2xx — surface as a generic error toast.

    async _post(
      path: string,
      label: string,
    ): Promise<boolean> {
      this.lastError = null
      const toast = useToastStore()
      try {
        const res = await apiFetch(path, { method: 'POST' })
        if (res.status === 400) {
          const data = (await res.json().catch(() => ({}))) as {
            detail?: string
          }
          const detail = data.detail ?? 'Другой процесс уже запущен'
          this.lastError = detail
          toast.error(detail)
          return false
        }
        if (!res.ok) {
          this.lastError = `HTTP ${res.status}`
          toast.error(`${label}: ошибка ${res.status}`)
          return false
        }
        // After a successful start/stop the server's WS will broadcast
        // the new status; for the polling fallback we trigger an
        // explicit refresh so the UI updates within ~half a second
        // instead of waiting for the next 5 s tick.
        if (!this.wsConnected) {
          window.setTimeout(() => void this.fetchStatus(), 500)
        }
        return true
      } catch (err) {
        const description = describe(err)
        if (description === 'Unauthorized' || description.startsWith('Unauthorized:')) {
          // session store will flip on its own via apiFetch.
          return false
        }
        this.lastError = description
        toast.error(`${label}: сбой запроса`)
        return false
      }
    },

    startFullPipeline(filters: Partial<SyncFilters> = {}): Promise<boolean> {
      const qs = buildSyncQuery(filters)
      return this._post(`/api/start_full_pipeline${qs}`, 'Полный цикл')
    },

    startSyncVideo(filters: Partial<SyncFilters> = {}): Promise<boolean> {
      const qs = buildSyncQuery(filters)
      return this._post(`/api/start_sync_video${qs}`, 'Синхронизация Video')
    },

    startSyncOther(filters: Partial<SyncFilters> = {}): Promise<boolean> {
      const qs = buildSyncQuery(filters)
      return this._post(`/api/start_sync_other${qs}`, 'Синхронизация Other')
    },

    startSyncRezka(): Promise<boolean> {
      return this._post('/api/start_sync_rezka', 'Синхронизация Rezka')
    },

    startRezkaCollections(): Promise<boolean> {
      return this._post('/api/start_rezka_collections', 'Папки Rezka')
    },

    startSyncKinopub(): Promise<boolean> {
      return this._post('/api/start_sync_kinopub', 'Синхронизация kino.pub')
    },

    startKinopubCollections(): Promise<boolean> {
      return this._post('/api/start_kinopub_collections', 'Папки kino.pub')
    },

    startCleanup(): Promise<boolean> {
      return this._post('/api/start_cleanup', 'Очистка дубликатов')
    },

    startFix(): Promise<boolean> {
      return this._post('/api/start_fix', 'Поиск (legacy)')
    },

    startFixPoisk(): Promise<boolean> {
      return this._post('/api/start_fix_poisk', 'Поиск (PoiskKino)')
    },

    startReprocess(force = false): Promise<boolean> {
      const path = force ? '/api/start_reprocess?force=true' : '/api/start_reprocess'
      return this._post(path, 'Полное обновление базы')
    },

    startSyncUser(): Promise<boolean> {
      return this._post('/api/sync_user', 'Импорт CSV')
    },

    startSyncTmdb(): Promise<boolean> {
      return this._post('/api/start_sync_tmdb', 'Синхронизация TMDB')
    },

    startTraktCollections(): Promise<boolean> {
      return this._post('/api/start_trakt_collections', 'Синхронизация Trakt')
    },

    stop(key: ProcessKey): Promise<boolean> {
      return this._post(`/api/stop/${key}`, `Остановка ${key}`)
    },

    async clearCheckpoint(key: ProcessKey): Promise<boolean> {
      this.lastError = null
      const toast = useToastStore()
      try {
        const res = await apiFetch(`/api/clear_checkpoint/${key}`, { method: 'POST' })
        if (!res.ok) {
          this.lastError = `HTTP ${res.status}`
          toast.error(`Не удалось сбросить поиск: ошибка ${res.status}`)
          return false
        }
        this.checkpoints[key] = false
        this.progress[key] = { current: 0, total: 0 }
        toast.success('Поиск сброшен, можно начать заново!')
        return true
      } catch (err) {
        const description = describe(err)
        this.lastError = description
        toast.error('Не удалось сбросить поиск: сбой запроса')
        return false
      }
    },

    /** Forget state and tear down sockets/timers. Idempotent. */
    reset(): void {
      this.disconnect()
      this.statuses = idleStatuses()
      this.progress = emptyProgress()
      this.rezkaSession = 'down'
      this.wsConnected = false
      this.lastError = null
    },
  },
})

/** Build a `?min_year=…&max_year=…&min_date=…` query string out of a
 *  filters object. Empty / null fields are skipped, matching legacy
 *  `index.html:1989-1993`. Exported for tests. */
export function buildSyncQuery(filters: Partial<SyncFilters>): string {
  const params: string[] = []
  if (filters.minYear) params.push(`min_year=${filters.minYear}`)
  if (filters.maxYear) params.push(`max_year=${filters.maxYear}`)
  if (filters.minDate) params.push(`min_date=${encodeURIComponent(filters.minDate)}`)
  return params.length ? `?${params.join('&')}` : ''
}
