// ROADMAP Stage 10.7h — audit log + undo state store.
//
// Pinia replacement for the legacy audit-log modal at
// `index.html:1023-1047` + the action handlers at
// `index.html:2483-2510`. Backend at `main.py:1274-1315`:
//   - `GET  /api/audit_log?limit=…&item_id=…`     → tail of rows
//   - `POST /api/audit_log/{audit_id}/undo`        → rollback (rebind
//     only, per current backend; 400 for unsupported actions).
//
// A row looks like:
//   { id, action, item_id, field, old_value, new_value, created_at,
//     undone }
//
// `groupedByAction` slices the list by `action` (e.g. rebind, category,
// ...) preserving DESC-by-id order inside each bucket, mirroring the
// visual grouping the legacy panel used at `index.html:1115`.

import { defineStore } from 'pinia'

import { apiFetch } from '../api/client'
import { useSessionStore } from './session'
import { useToastStore } from './toast'

export interface AuditEntry {
  id: number
  action: string
  item_id: number | null
  field: string | null
  old_value: string | null
  new_value: string | null
  created_at: string | null
  undone: 0 | 1 | boolean
}

interface AuditStoreState {
  entries: AuditEntry[]
  loading: boolean
  error: string | null
  /** Cap on rows fetched; the backend clamps to 500. */
  limit: number
}

interface ApiError {
  error?: string
}

async function extractError(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as ApiError
    if (data?.error) return data.error
  } catch {
    // body was not JSON — fall through
  }
  return `HTTP ${res.status}`
}

/** Coerce the `undone` field — SQLite returns it as 0/1, but tests
 *  and hand-crafted fixtures sometimes use booleans. */
function isUndone(entry: AuditEntry): boolean {
  return entry.undone === 1 || entry.undone === true
}

/** Currently the backend only supports undo for `rebind` rows (see
 *  `main.py:1292-1313`). The audit panel surfaces this via a
 *  per-row check so the undo button can be hidden for unsupported
 *  actions. Keeping the allow-list here makes it easy to extend
 *  later without touching the component. */
const UNDOABLE_ACTIONS = new Set(['rebind'])

export function isUndoable(entry: AuditEntry): boolean {
  if (isUndone(entry)) return false
  return UNDOABLE_ACTIONS.has(entry.action)
}

export const useAuditStore = defineStore('audit', {
  state: (): AuditStoreState => ({
    entries: [],
    loading: false,
    error: null,
    limit: 50,
  }),

  getters: {
    /** Map of `action` → entries (newest first), used by the panel to
     *  render grouped sections. Order of keys matches first-occurrence
     *  in the list so the most-recent action label appears at top. */
    groupedByAction(state): Record<string, AuditEntry[]> {
      const groups: Record<string, AuditEntry[]> = {}
      for (const entry of state.entries) {
        const bucket = groups[entry.action]
        if (bucket) {
          bucket.push(entry)
        } else {
          groups[entry.action] = [entry]
        }
      }
      return groups
    },

    /** Count of rows that still have an undo affordance. */
    undoableCount(state): number {
      return state.entries.filter((e) => isUndoable(e)).length
    },
  },

  actions: {
    /** Reload the tail of the audit log. Optional `limit` overrides
     *  the stored value for this call (and persists for the next). */
    async refresh(limit?: number): Promise<void> {
      const session = useSessionStore()
      if (!session.canCallApi) return
      if (typeof limit === 'number' && limit > 0) {
        this.limit = limit
      }
      this.loading = true
      this.error = null
      try {
        const res = await apiFetch(
          `/api/audit_log?limit=${encodeURIComponent(this.limit)}`,
        )
        if (!res.ok) {
          this.error = await extractError(res)
          return
        }
        const data = (await res.json()) as AuditEntry[]
        this.entries = Array.isArray(data) ? data : []
      } catch (err) {
        this.error = err instanceof Error ? err.message : String(err)
      } finally {
        this.loading = false
      }
    },

    /**
     * Roll back the given audit row via `POST .../undo`. On success
     * the row is marked `undone` locally (the backend also persists
     * the flag) and a toast is shown. The store doesn't auto-refresh
     * the feed — callers (or the component, via emit) coordinate
     * that, mirroring legacy `undoAudit()` which called
     * `fetchFeed()` after a successful undo.
     */
    async undo(auditId: number): Promise<boolean> {
      const session = useSessionStore()
      if (!session.canCallApi) return false
      const toast = useToastStore()
      this.error = null
      try {
        const res = await apiFetch(
          `/api/audit_log/${auditId}/undo`,
          { method: 'POST' },
        )
        if (!res.ok) {
          const msg = await extractError(res)
          this.error = msg
          toast.error(`Откат: ${msg}`)
          return false
        }
        // Mark locally so the UI updates without a full refresh.
        const target = this.entries.find((e) => e.id === auditId)
        if (target) target.undone = 1
        toast.success('Откачено')
        return true
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        this.error = msg
        toast.error(`Откат: ${msg}`)
        return false
      }
    },
  },
})
