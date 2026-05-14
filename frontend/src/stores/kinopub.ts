// kino.pub integration store (PR 2 + PR 3 of the kino.pub stack).
//
// Owns:
//   * Device-Flow UI state (status / start / poll / logout)
//   * Per-item bind/unbind (PR 3)
//   * On-demand catalog search + stream_info (PR 3)
//
// All HTTP goes through `apiFetch` so the bearer token is included
// and 401s surface to the session store.

import { defineStore } from 'pinia'
import { apiFetch } from '../api/client'

export interface KinopubStatus {
  enabled: boolean
  authenticated: boolean
  /** Absolute Unix-epoch seconds when the access_token expires, or null when not authed. */
  expiresAt: number | null
  /** Seconds until expiry; never negative. Null when not authed. */
  expiresIn: number | null
  clientId: string | null
}

interface DeviceFlow {
  deviceCode: string
  userCode: string
  verificationUri: string
  /** Seconds between poll calls, as recommended by the server. */
  interval: number
  /** Server-imposed device_code TTL in seconds. */
  expiresIn: number
  /** Absolute Unix-epoch ms when this device_code becomes invalid. */
  expiresAtMs: number
}

export interface KinopubSearchResult {
  id: number
  title: string | null
  year: number | null
  type: string | null
  url: string
  poster: string | null
}

export interface KinopubStreamFile {
  url: string
  quality: string | null
  codec: string | null
}

export interface KinopubStreamAudio {
  lang: string | null
  type: string | null
  author: string | null
}

export interface KinopubStreamSubtitle {
  url: string
  lang: string | null
  shift: number
  embed: boolean
}

export interface KinopubStreamVideo {
  number: number | null
  title: string | null
  duration: number | null
  files: KinopubStreamFile[]
  audios: KinopubStreamAudio[]
  subtitles: KinopubStreamSubtitle[]
}

export interface KinopubStreamSeason {
  number: number | null
  episodes: KinopubStreamVideo[]
}

export interface KinopubStreamInfo {
  id: number
  title: string | null
  year: number | null
  type: string | null
  url: string
  videos: KinopubStreamVideo[]
  seasons: KinopubStreamSeason[]
}

interface KinopubStoreState {
  status: KinopubStatus | null
  statusBusy: boolean
  statusError: string

  flow: DeviceFlow | null
  pollBusy: boolean
  pollError: string
  /** "pending" until user confirms; "confirmed" / "expired" at end. */
  pollState: 'idle' | 'pending' | 'confirmed' | 'expired'
  /** ID of the active `setInterval` poll, if any. */
  pollTimer: ReturnType<typeof setInterval> | null

  logoutBusy: boolean

  searchResults: KinopubSearchResult[]
  searchBusy: boolean
  searchError: string

  bindBusy: boolean
  bindError: string

  streamInfo: KinopubStreamInfo | null
  streamBusy: boolean
  streamError: string
}

function describeError(err: unknown): string {
  if (err instanceof Error) return err.message
  return String(err)
}

interface StatusResponse {
  enabled: boolean
  authenticated: boolean
  expires_at: number | null
  expires_in: number | null
  client_id: string | null
}

interface DeviceStartResponse {
  device_code: string
  user_code: string
  verification_uri: string
  interval: number
  expires_in: number
}

interface DevicePollResponse {
  state: 'pending' | 'confirmed' | 'expired'
}

export const useKinopubStore = defineStore('kinopub', {
  state: (): KinopubStoreState => ({
    status: null,
    statusBusy: false,
    statusError: '',
    flow: null,
    pollBusy: false,
    pollError: '',
    pollState: 'idle',
    pollTimer: null,
    logoutBusy: false,

    searchResults: [],
    searchBusy: false,
    searchError: '',

    bindBusy: false,
    bindError: '',

    streamInfo: null,
    streamBusy: false,
    streamError: '',
  }),

  getters: {
    isAuthenticated: (state): boolean =>
      state.status?.authenticated === true,
    isEnabled: (state): boolean => state.status?.enabled === true,
  },

  actions: {
    async fetchStatus(): Promise<void> {
      this.statusBusy = true
      this.statusError = ''
      try {
        const res = await apiFetch('/api/kinopub/status')
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const body = (await res.json()) as StatusResponse
        this.status = {
          enabled: body.enabled,
          authenticated: body.authenticated,
          expiresAt: body.expires_at,
          expiresIn: body.expires_in,
          clientId: body.client_id,
        }
      } catch (err) {
        this.statusError = describeError(err)
      } finally {
        this.statusBusy = false
      }
    },

    /** Start a Device Flow. Caller is expected to follow up with
     * `startPolling()` once the modal is visible. */
    async startDeviceFlow(): Promise<void> {
      this.stopPolling()
      this.flow = null
      this.pollError = ''
      this.pollState = 'idle'
      try {
        const res = await apiFetch('/api/kinopub/device/start', {
          method: 'POST',
        })
        if (!res.ok) {
          const detail = await safeReadDetail(res)
          throw new Error(detail || `HTTP ${res.status}`)
        }
        const body = (await res.json()) as DeviceStartResponse
        this.flow = {
          deviceCode: body.device_code,
          userCode: body.user_code,
          verificationUri: body.verification_uri,
          interval: Math.max(2, body.interval),
          expiresIn: body.expires_in,
          expiresAtMs: Date.now() + body.expires_in * 1000,
        }
        this.pollState = 'pending'
      } catch (err) {
        this.pollError = describeError(err)
        this.pollState = 'expired'
      }
    },

    /** Begin polling /api/kinopub/device/poll until confirmed/expired
     * or `stopPolling()` is called. Honors the server-recommended
     * `interval`. Auto-refreshes status on confirmation. */
    startPolling(): void {
      if (!this.flow || this.pollTimer) return
      const tick = async (): Promise<void> => {
        if (!this.flow) return
        if (Date.now() > this.flow.expiresAtMs) {
          this.pollState = 'expired'
          this.stopPolling()
          return
        }
        this.pollBusy = true
        try {
          const res = await apiFetch('/api/kinopub/device/poll', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_code: this.flow.deviceCode }),
          })
          if (!res.ok) throw new Error(`HTTP ${res.status}`)
          const body = (await res.json()) as DevicePollResponse
          this.pollState = body.state
          if (body.state === 'confirmed') {
            this.stopPolling()
            await this.fetchStatus()
          } else if (body.state === 'expired') {
            this.stopPolling()
          }
        } catch (err) {
          this.pollError = describeError(err)
        } finally {
          this.pollBusy = false
        }
      }
      this.pollTimer = setInterval(tick, this.flow.interval * 1000)
      // Fire one immediate tick so the user doesn't wait `interval`
      // seconds for the first poll after a fast confirmation.
      void tick()
    },

    stopPolling(): void {
      if (this.pollTimer) {
        clearInterval(this.pollTimer)
        this.pollTimer = null
      }
    },

    /** Drop the in-progress flow without logging out. */
    cancelDeviceFlow(): void {
      this.stopPolling()
      this.flow = null
      this.pollState = 'idle'
      this.pollError = ''
    },

    async logout(): Promise<void> {
      this.logoutBusy = true
      try {
        await apiFetch('/api/kinopub/logout', { method: 'POST' })
        await this.fetchStatus()
        this.cancelDeviceFlow()
      } catch (err) {
        this.statusError = describeError(err)
      } finally {
        this.logoutBusy = false
      }
    },

    /** Search kino.pub by title with optional year/type filters.
     * Populates `searchResults`; consumers (ItemCardModal) render
     * them as picker rows. */
    async search(
      title: string,
      opts: { year?: number | null; type?: string | null; limit?: number } = {},
    ): Promise<void> {
      const q = title.trim()
      if (q.length === 0) {
        this.searchResults = []
        this.searchError = ''
        return
      }
      this.searchBusy = true
      this.searchError = ''
      try {
        const params = new URLSearchParams({ title: q })
        if (opts.year) params.set('year', String(opts.year))
        if (opts.type) params.set('type', opts.type)
        if (opts.limit) params.set('limit', String(opts.limit))
        const res = await apiFetch(`/api/kinopub/search?${params.toString()}`)
        if (!res.ok) {
          const detail = await safeReadDetail(res)
          throw new Error(detail || `HTTP ${res.status}`)
        }
        const body = (await res.json()) as { results: KinopubSearchResult[] }
        this.searchResults = body.results
      } catch (err) {
        this.searchResults = []
        this.searchError = describeError(err)
      } finally {
        this.searchBusy = false
      }
    },

    clearSearch(): void {
      this.searchResults = []
      this.searchError = ''
    },

    /** Attach a kinopub_id to a par2 item. Returns true on success
     * so the caller can show a toast. The items store is the source
     * of truth for the row itself — callers should refresh it after. */
    async bind(
      itemId: number,
      payload: {
        kinopub_id: number
        kinopub_type?: string | null
        kinopub_url?: string | null
      },
    ): Promise<boolean> {
      this.bindBusy = true
      this.bindError = ''
      try {
        const res = await apiFetch(`/api/kinopub/bind/${itemId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })
        if (!res.ok) {
          const detail = await safeReadDetail(res)
          throw new Error(detail || `HTTP ${res.status}`)
        }
        return true
      } catch (err) {
        this.bindError = describeError(err)
        return false
      } finally {
        this.bindBusy = false
      }
    },

    /** Detach the kinopub_id from a par2 item. Same conventions as `bind`. */
    async unbind(itemId: number): Promise<boolean> {
      this.bindBusy = true
      this.bindError = ''
      try {
        const res = await apiFetch(`/api/kinopub/unbind/${itemId}`, {
          method: 'POST',
        })
        if (!res.ok) {
          const detail = await safeReadDetail(res)
          throw new Error(detail || `HTTP ${res.status}`)
        }
        return true
      } catch (err) {
        this.bindError = describeError(err)
        return false
      } finally {
        this.bindBusy = false
      }
    },

    /** Fetch stream metadata (qualities/audios/subtitles/seasons) for
     * a bound par2 item. The PlayerModal in PR 4 will consume this. */
    async fetchStreamInfo(itemId: number): Promise<void> {
      this.streamBusy = true
      this.streamError = ''
      this.streamInfo = null
      try {
        const res = await apiFetch(`/api/kinopub/stream_info/${itemId}`)
        if (!res.ok) {
          const detail = await safeReadDetail(res)
          throw new Error(detail || `HTTP ${res.status}`)
        }
        this.streamInfo = (await res.json()) as KinopubStreamInfo
      } catch (err) {
        this.streamError = describeError(err)
      } finally {
        this.streamBusy = false
      }
    },
  },
})

async function safeReadDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string }
    if (body && typeof body.detail === 'string') return body.detail
  } catch {
    // ignore — fall through to status code
  }
  return ''
}
