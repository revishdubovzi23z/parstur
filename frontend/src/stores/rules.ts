// ROADMAP Stage 10.7h — filter rules manager state store.
//
// Pinia replacement for the legacy filter-rules modal at
// `index.html:980-1021` + the action handlers at
// `index.html:2422-2481`. The backend endpoint shape is defined at
// `main.py:1188-1248`:
//   - `GET    /api/filter_rules`            → list
//   - `POST   /api/filter_rules`            → create
//   - `PUT    /api/filter_rules/{rule_id}`  → update (partial)
//   - `DELETE /api/filter_rules/{rule_id}`  → delete
//
// A rule has the shape `{ id, name, field, pattern, action, enabled }`.
// `field` is one of `title | original_title | description` and `action`
// is one of `hide | highlight`. The pattern is a Python `re` regex —
// validation happens server-side and 400s come back as `{error: …}`.

import { defineStore } from 'pinia'

import { apiFetch } from '../api/client'
import { useSessionStore } from './session'
import { useToastStore } from './toast'

export type FilterRuleField = 'title' | 'original_title' | 'description'
export type FilterRuleAction = 'hide' | 'highlight'

export interface FilterRule {
  id: number
  name: string
  field: FilterRuleField
  pattern: string
  action: FilterRuleAction
  enabled: boolean
}

export interface FilterRuleCreate {
  name: string
  field: FilterRuleField
  pattern: string
  action: FilterRuleAction
  enabled?: boolean
}

export type FilterRuleUpdate = Partial<{
  name: string
  field: FilterRuleField
  pattern: string
  action: FilterRuleAction
  enabled: boolean
}>

interface RulesStoreState {
  rules: FilterRule[]
  loading: boolean
  error: string | null
}

interface ApiError {
  error?: string
}

/** Read the `error` field from a non-2xx JSON body and fall back to a
 *  generic "HTTP <status>" string. Mirrors the legacy pattern at
 *  `index.html:2448-2450`. */
async function extractError(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as ApiError
    if (data?.error) return data.error
  } catch {
    // body was not JSON — fall through
  }
  return `HTTP ${res.status}`
}

export const useRulesStore = defineStore('rules', {
  state: (): RulesStoreState => ({
    rules: [],
    loading: false,
    error: null,
  }),

  getters: {
    /** Count of currently active rules, used by panel headers. */
    enabledCount(state): number {
      return state.rules.filter((r) => r.enabled).length
    },
  },

  actions: {
    /** Reload the full rule list from `GET /api/filter_rules`. */
    async refresh(): Promise<void> {
      const session = useSessionStore()
      if (!session.canCallApi) return
      this.loading = true
      this.error = null
      try {
        const res = await apiFetch('/api/filter_rules')
        if (!res.ok) {
          this.error = await extractError(res)
          return
        }
        const data = (await res.json()) as FilterRule[]
        this.rules = Array.isArray(data) ? data : []
      } catch (err) {
        this.error = err instanceof Error ? err.message : String(err)
      } finally {
        this.loading = false
      }
    },

    /**
     * Create a new rule. On success, the freshly created rule is
     * appended to local state via `refresh()`. Returns the new id, or
     * `null` if the server rejected the payload.
     */
    async create(data: FilterRuleCreate): Promise<number | null> {
      const session = useSessionStore()
      if (!session.canCallApi) return null
      const toast = useToastStore()
      this.error = null
      try {
        const res = await apiFetch('/api/filter_rules', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data),
        })
        if (!res.ok) {
          const msg = await extractError(res)
          this.error = msg
          toast.error(`Создание правила: ${msg}`)
          return null
        }
        const body = (await res.json()) as { id?: number }
        await this.refresh()
        toast.success('Правило создано')
        return body.id ?? null
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        this.error = msg
        toast.error(`Создание правила: ${msg}`)
        return null
      }
    },

    /** Patch an existing rule with the provided partial. */
    async update(
      ruleId: number,
      patch: FilterRuleUpdate,
    ): Promise<boolean> {
      const session = useSessionStore()
      if (!session.canCallApi) return false
      const toast = useToastStore()
      this.error = null
      try {
        const res = await apiFetch(`/api/filter_rules/${ruleId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(patch),
        })
        if (!res.ok) {
          const msg = await extractError(res)
          this.error = msg
          toast.error(`Обновление правила: ${msg}`)
          return false
        }
        await this.refresh()
        return true
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        this.error = msg
        toast.error(`Обновление правила: ${msg}`)
        return false
      }
    },

    /** Shortcut around `update()` that flips just the `enabled` flag.
     *  Mirrors legacy `toggleRule()` at `index.html:2459-2470`. */
    async toggle(rule: FilterRule): Promise<boolean> {
      return this.update(rule.id, { enabled: !rule.enabled })
    },

    /** Delete a rule. */
    async remove(ruleId: number): Promise<boolean> {
      const session = useSessionStore()
      if (!session.canCallApi) return false
      const toast = useToastStore()
      this.error = null
      try {
        const res = await apiFetch(`/api/filter_rules/${ruleId}`, {
          method: 'DELETE',
        })
        if (!res.ok) {
          const msg = await extractError(res)
          this.error = msg
          toast.error(`Удаление правила: ${msg}`)
          return false
        }
        await this.refresh()
        toast.success('Правило удалено')
        return true
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        this.error = msg
        toast.error(`Удаление правила: ${msg}`)
        return false
      }
    },
  },
})
