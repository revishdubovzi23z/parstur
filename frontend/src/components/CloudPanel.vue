<script setup lang="ts">
// Stage 13 — ☁️ Cloud sync (Turso / libSQL) panel.
//
// Mirrors LampaPanel: credentials are stored via /api/settings/credentials
// (CLOUD_PROVIDER / CLOUD_TURSO_URL / CLOUD_TURSO_TOKEN), while live
// status + manual push/pull go through /api/cloud/*.
//
// A manual push/pull can take a while (a remote-only libSQL connection
// does a network round-trip per statement), so while one runs we poll
// /api/cloud/progress once a second to show a progress bar and offer a
// Stop button that hits /api/cloud/cancel.

import { onMounted, onUnmounted, reactive, ref, watch } from 'vue'

import { apiFetch, UnauthorizedError } from '../api/client'
import { useSessionStore } from '../stores/session'

const props = defineProps<{ visible: boolean }>()

const session = useSessionStore()
const loading = ref(false)
const saving = ref(false)
const syncing = ref(false)
const cancelling = ref(false)
const error = ref('')
const success = ref('')
const loaded = ref(false)

const draft = reactive({
  CLOUD_PROVIDER: 'none',
  CLOUD_TURSO_URL: '',
  CLOUD_TURSO_TOKEN: '',
})

const status = reactive<Record<string, any>>({
  enabled: false,
  last_push: null,
  last_pull: null,
})

const progress = reactive<Record<string, any>>({
  running: false,
  direction: '',
  phase: 'idle',
  current_table: '',
  tables_total: 0,
  tables_done: 0,
  rows_total: 0,
  rows_done: 0,
  percent: 0,
  detail: '',
  elapsed_seconds: 0,
  cancel_requested: false,
})

let progressTimer: number | null = null

const PHASE_LABELS: Record<string, string> = {
  idle: 'Ожидание',
  starting: 'Запуск…',
  schema: 'Перенос схемы…',
  data: 'Копирование данных…',
  finalizing: 'Завершение…',
  done: 'Готово',
  error: 'Ошибка',
  cancelled: 'Остановлено',
}

function phaseLabel(phase: string): string {
  return PHASE_LABELS[phase] || phase
}

function directionLabel(direction: string): string {
  if (direction === 'push') return '⇑ Push'
  if (direction === 'pull') return '⇓ Pull'
  return ''
}

function progressLine(): string {
  const dir = directionLabel(progress.direction)
  const phase = phaseLabel(progress.phase)
  const table = progress.current_table ? ' · ' + progress.current_table : ''
  return (dir ? dir + ' — ' : '') + phase + table
}

function progressCounts(): string {
  const rows = progress.rows_done + ' / ' + (progress.rows_total || '?') + ' строк'
  const tables = ' · таблиц ' + progress.tables_done + '/' + progress.tables_total
  return rows + tables
}

function applyCredentials(data: any): void {
  const c = data?.credentials
  if (!c) return
  if (c.CLOUD_PROVIDER) draft.CLOUD_PROVIDER = c.CLOUD_PROVIDER.value || 'none'
  if (c.CLOUD_TURSO_URL) draft.CLOUD_TURSO_URL = c.CLOUD_TURSO_URL.value || ''
  if (c.CLOUD_TURSO_TOKEN) draft.CLOUD_TURSO_TOKEN = c.CLOUD_TURSO_TOKEN.value || ''
}

async function loadStatus(): Promise<void> {
  try {
    const res = await apiFetch('/api/cloud/status')
    if (res.ok) Object.assign(status, await res.json())
  } catch (err) {
    if (err instanceof UnauthorizedError) session.handleUnauthorized(err)
  }
}

async function pollProgress(): Promise<void> {
  try {
    const res = await apiFetch('/api/cloud/progress')
    if (res.ok) Object.assign(progress, await res.json())
  } catch (err) {
    if (err instanceof UnauthorizedError) session.handleUnauthorized(err)
  }
}

function stopProgressPolling(): void {
  if (progressTimer !== null) {
    window.clearInterval(progressTimer)
    progressTimer = null
  }
}

function startProgressPolling(): void {
  stopProgressPolling()
  void pollProgress()
  progressTimer = window.setInterval(() => {
    void pollProgress()
  }, 1000)
}

async function loadSettings(force = false): Promise<void> {
  if (!props.visible || !session.canCallApi || (loaded.value && !force)) return
  loading.value = true
  error.value = ''
  try {
    const res = await apiFetch('/api/settings/credentials')
    if (!res.ok) {
      error.value = `HTTP ${res.status}`
      return
    }
    applyCredentials(await res.json())
    loaded.value = true
    await loadStatus()
    // If a sync is already in flight (e.g. started before the panel was
    // opened), surface its progress immediately.
    await pollProgress()
    if (progress.running) startProgressPolling()
  } catch (err) {
    if (err instanceof UnauthorizedError) session.handleUnauthorized(err)
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function saveSettings(): Promise<void> {
  if (!session.canCallApi) {
    error.value = 'Сессия не авторизована'
    return
  }
  const values: Record<string, string> = {
    CLOUD_PROVIDER: draft.CLOUD_PROVIDER,
    CLOUD_TURSO_URL: draft.CLOUD_TURSO_URL.trim(),
    CLOUD_TURSO_TOKEN: draft.CLOUD_TURSO_TOKEN.trim(),
  }
  saving.value = true
  error.value = ''
  success.value = ''
  try {
    const res = await apiFetch('/api/settings/credentials', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values }),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      error.value = String(data.error ?? `HTTP ${res.status}`)
      return
    }
    applyCredentials(await res.json())
    success.value = 'Настройки облака сохранены'
    await loadStatus()
  } catch (err) {
    if (err instanceof UnauthorizedError) session.handleUnauthorized(err)
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    saving.value = false
  }
}

async function runSync(direction: 'push' | 'pull'): Promise<void> {
  if (!session.canCallApi) return
  if (
    direction === 'pull' &&
    !window.confirm('Заменить локальную базу данными из облака? Текущие данные будут перезаписаны.')
  )
    return
  syncing.value = true
  cancelling.value = false
  error.value = ''
  success.value = ''
  startProgressPolling()
  try {
    const res = await apiFetch(`/api/cloud/${direction}`, { method: 'POST' })
    const data = await res.json().catch(() => ({}))
    if (!res.ok || data.status === 'error') {
      error.value = String(data.detail ?? `HTTP ${res.status}`)
    } else if (data.status === 'disabled') {
      error.value = 'Облачная синхронизация не настроена'
    } else if (data.status === 'busy') {
      error.value = String(data.detail ?? 'Синхронизация уже выполняется')
    } else if (data.status === 'cancelled') {
      success.value = String(data.detail ?? 'Остановлено')
    } else if (data.status === 'skipped') {
      success.value = String(data.detail ?? 'Пропущено')
    } else {
      success.value = String(data.detail ?? 'Готово')
    }
  } catch (err) {
    if (err instanceof UnauthorizedError) session.handleUnauthorized(err)
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    stopProgressPolling()
    await pollProgress()
    await loadStatus()
    syncing.value = false
    cancelling.value = false
  }
}

async function cancelSync(): Promise<void> {
  if (!session.canCallApi) return
  cancelling.value = true
  try {
    const res = await apiFetch('/api/cloud/cancel', { method: 'POST' })
    await res.json().catch(() => ({}))
    await pollProgress()
  } catch (err) {
    if (err instanceof UnauthorizedError) session.handleUnauthorized(err)
  }
}

watch(
  () => props.visible,
  (visible) => {
    if (visible) void loadSettings()
  },
)

onMounted(() => {
  void loadSettings()
})

onUnmounted(() => {
  stopProgressPolling()
})
</script>

<template>
  <section class="rounded-lg border border-slate-200 p-4" data-testid="cloud-panel">
    <div class="flex items-start justify-between gap-3">
      <div>
        <h3 class="text-sm font-semibold text-slate-900">☁️ Облачная синхронизация (Turso)</h3>
        <p class="mt-1 text-xs text-slate-500">
          Зеркалирует локальную базу <code>app_data.db</code> в удалённую базу Turso / libSQL и обратно.
        </p>
      </div>
      <span
        :class="[
          'rounded-full px-2 py-1 text-[10px] font-semibold',
          status.enabled ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500',
        ]"
        v-text="status.enabled ? 'Активно' : 'Выключено'"
      ></span>
    </div>

    <div class="mt-4 grid gap-3 sm:grid-cols-2">
      <label class="flex flex-col gap-1 text-xs text-slate-600">
        <span class="font-semibold">Провайдер</span>
        <select
          v-model="draft.CLOUD_PROVIDER"
          class="rounded-md border border-slate-300 px-2 py-1.5 text-xs bg-white"
          data-testid="cloud-provider-select"
        >
          <option value="none">Отключено</option>
          <option value="turso">Turso / libSQL</option>
        </select>
      </label>

      <label class="flex flex-col gap-1 text-xs text-slate-600">
        <span class="font-semibold">URL базы (libsql://...)</span>
        <input
          v-model="draft.CLOUD_TURSO_URL"
          class="rounded-md border border-slate-300 px-2 py-1.5 text-xs"
          placeholder="libsql://my-db.turso.io"
          data-testid="cloud-url-input"
        />
      </label>

      <label class="flex flex-col gap-1 text-xs text-slate-600 sm:col-span-2">
        <span class="font-semibold">Auth-токен</span>
        <input
          v-model="draft.CLOUD_TURSO_TOKEN"
          type="password"
          class="rounded-md border border-slate-300 px-2 py-1.5 text-xs"
          placeholder="Токен доступа Turso"
          data-testid="cloud-token-input"
        />
      </label>
    </div>

    <div
      v-if="syncing || progress.running"
      class="mt-4 rounded-md border border-teal-100 bg-teal-50/50 px-3 py-2"
      data-testid="cloud-progress"
    >
      <div class="flex items-center justify-between text-[11px] font-semibold text-teal-800">
        <span v-text="progressLine()"></span>
        <span v-text="progress.percent + '%'"></span>
      </div>
      <div class="mt-1 h-2 w-full overflow-hidden rounded-full bg-teal-100">
        <div
          class="h-full rounded-full bg-teal-500 transition-all duration-500"
          :style="{ width: (progress.percent || 0) + '%' }"
        ></div>
      </div>
      <div class="mt-1 flex items-center justify-between text-[10px] text-slate-500">
        <span v-text="progressCounts()"></span>
        <span v-text="progress.elapsed_seconds + ' с'"></span>
      </div>
    </div>

    <div
      v-if="status.last_push || status.last_pull"
      class="mt-4 grid gap-2 sm:grid-cols-2 text-[11px] text-slate-500"
    >
      <div class="rounded-md bg-slate-50 border border-slate-150 px-2 py-1.5">
        <span class="font-semibold">Последний push:</span>
        <span v-if="status.last_push" v-text="' ' + status.last_push.status + ' — ' + status.last_push.detail"></span>
        <span v-else> —</span>
      </div>
      <div class="rounded-md bg-slate-50 border border-slate-150 px-2 py-1.5">
        <span class="font-semibold">Последний pull:</span>
        <span v-if="status.last_pull" v-text="' ' + status.last_pull.status + ' — ' + status.last_pull.detail"></span>
        <span v-else> —</span>
      </div>
    </div>

    <div class="mt-4 flex flex-wrap items-center gap-2">
      <button
        type="button"
        class="inline-flex items-center justify-center rounded-md bg-slate-800 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-900 disabled:opacity-50"
        :disabled="loading || saving || syncing"
        @click="saveSettings"
      >
        Сохранить
      </button>
      <button
        type="button"
        class="inline-flex items-center justify-center rounded-md bg-teal-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-teal-700 disabled:opacity-50"
        :disabled="loading || saving || syncing || !status.enabled"
        data-testid="cloud-push"
        @click="runSync('push')"
      >
        ⇑ Push в облако
      </button>
      <button
        type="button"
        class="inline-flex items-center justify-center rounded-md bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-700 disabled:opacity-50"
        :disabled="loading || saving || syncing || !status.enabled"
        data-testid="cloud-pull"
        @click="runSync('pull')"
      >
        ⇓ Pull из облака
      </button>
      <button
        v-if="syncing || progress.running"
        type="button"
        class="inline-flex items-center justify-center rounded-md bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-50"
        :disabled="cancelling || progress.cancel_requested"
        data-testid="cloud-cancel"
        @click="cancelSync"
        v-text="(cancelling || progress.cancel_requested) ? 'Останавливаю…' : '■ Остановить'"
      ></button>
      <span v-if="success" class="text-xs font-semibold text-emerald-700" v-text="success"></span>
      <span v-if="error" class="text-xs font-semibold text-red-700" v-text="error"></span>
    </div>
  </section>
</template>
