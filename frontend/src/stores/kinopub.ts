// kino.pub integration store (PR 2 of the kino.pub stack).
//
// Owns the Device-Flow UI state: pinging `/api/kinopub/status`,
// starting a device flow, polling for confirmation, and logging out.
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
