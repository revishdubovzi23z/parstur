// ROADMAP Stage 10.2 — high-level API composable.
//
// Thin wrapper around `apiFetch` that automatically routes
// UnauthorizedError into the session store. Components and stores
// that need to call the backend should use this rather than the
// raw `apiFetch` from `../api/client` so the 401-handling flow is
// uniform.

import { apiFetch, UnauthorizedError, type ApiFetchOptions } from '../api/client'
import { useSessionStore } from '../stores/session'

export interface UseApi {
  /** Raw response — caller decides on `.json()` / `.text()`. */
  request: (path: string, options?: ApiFetchOptions) => Promise<Response>
  /** Parsed JSON. Throws on non-2xx. Use `request` for fine control. */
  json: <T = unknown>(path: string, options?: ApiFetchOptions) => Promise<T>
}

export function useApi(): UseApi {
  const session = useSessionStore()

  async function request(path: string, options?: ApiFetchOptions): Promise<Response> {
    try {
      return await apiFetch(path, options)
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        session.handleUnauthorized(err)
      }
      throw err
    }
  }

  async function json<T = unknown>(path: string, options?: ApiFetchOptions): Promise<T> {
    const res = await request(path, options)
    if (!res.ok) {
      const body = await res.text().catch(() => '')
      throw new Error(`HTTP ${res.status} ${res.statusText} for ${path}: ${body}`)
    }
    return (await res.json()) as T
  }

  return { request, json }
}
