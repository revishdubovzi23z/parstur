<script setup lang="ts">
// ROADMAP Stage 10.7d — sync controls panel.
//
// Replaces the cluster of action buttons in the legacy sidebar
// (`index.html:340-525`): start full pipeline / sync video / sync
// other / reprocess / fix / poiskkino / rezka / rezka folders /
// cleanup / user CSV import, plus the per-button "stop" buttons.
//
// The modal is mounted from `AppShell`. Open it from the "⚡
// Синхронизация" button. The sync store owns the `/ws` lifecycle —
// we just bind to `statuses` / `progress` and call actions.
//
// Follow-up to 10.7z: the panel now seeds `minYear` / `maxYear` with
// sensible defaults (2023 .. current year + 1) like the legacy
// `index.html` did at boot, and emits a `started` event after every
// successful `start*` action so the shell can pop the logs overlay.
//
// Follow-up to the backend-gap audit: the panel exposes the previously
// unreachable `/api/sync_user` (CSV import worker) and lets the user
// flip `force=true` on `/api/start_reprocess` via a checkbox.

import { reactive, ref, watch } from 'vue'

import { useSyncStore, type ProcessKey, type SyncFilters } from '../stores/sync'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{
  (e: 'close'): void
  /** Fires after a successful `/api/start_*` call so the parent
   *  shell can auto-open the live log overlay for the right key. */
  (e: 'started', key: ProcessKey): void
}>()

const sync = useSyncStore()

// Legacy `syncFilters` default at `index.html` boot was 2023 ..
// (current year + 1) so a fresh "Sync · Video" or "Sync · Other"
// only picked up the modern slice of Rutor unless the user widened
// the range manually. Mirror that here so the SPA isn't surprising
// on first interaction.
const CURRENT_YEAR = new Date().getFullYear()
const DEFAULT_MIN_YEAR = 2023
const DEFAULT_MAX_YEAR = CURRENT_YEAR + 1
const filters = reactive<{
  minYear: string | number
  maxYear: string | number
  minDate: string
}>({
  minYear: DEFAULT_MIN_YEAR,
  maxYear: DEFAULT_MAX_YEAR,
  minDate: '',
})

async function runStart(
  key: ProcessKey,
  start: () => Promise<boolean>,
): Promise<void> {
  const ok = await start()
  if (ok) emit('started', key)
}

function parseInt(raw: string | number): number | null {
  if (raw === '' || raw === null || raw === undefined) return null
  const n = typeof raw === 'number' ? raw : Number(String(raw).trim())
  return Number.isFinite(n) && n > 0 ? n : null
}

function parsedFilters(): Partial<SyncFilters> {
  const minDate = String(filters.minDate ?? '').trim()
  return {
    minYear: parseInt(filters.minYear),
    maxYear: parseInt(filters.maxYear),
    minDate: minDate || null,
  }
}

interface ControlRow {
  key: ProcessKey
  label: string
  description: string
  start: () => Promise<boolean>
  startLabel: string
}

const CONTROLS: ControlRow[] = [
  {
    key: 'full_pipeline',
    label: 'Полный цикл',
    description: 'Запускает все sync-этапы по порядку.',
    start: () => sync.startFullPipeline(),
    startLabel: 'Запустить',
  },
  {
    key: 'sync_video',
    label: 'Sync · Видео',
    description: 'Парсинг новых видеораздач с Rutor.',
    start: () => sync.startSyncVideo(parsedFilters()),
    startLabel: 'Старт',
  },
  {
    key: 'sync_other',
    label: 'Sync · Игры и софт',
    description: 'Парсинг игровых и софтовых раздач.',
    start: () => sync.startSyncOther(parsedFilters()),
    startLabel: 'Старт',
  },
  {
    key: 'reprocess',
    label: 'Полное обновление базы',
    description:
      'Пересобирает метаданные для всех записей. С «Принудительно» — не пропускает уже обработанные.',
    start: () => sync.startReprocess(reprocessForce.value),
    startLabel: 'Старт',
  },
  {
    key: 'poiskkino',
    label: 'Поиск (PoiskKino)',
    description: 'Восстановление пропавших постеров/ID.',
    start: () => sync.startFixPoisk(),
    startLabel: 'Старт',
  },
  {
    key: 'fix',
    label: 'Поиск (Legacy API)',
    description: 'Fallback на старый поиск.',
    start: () => sync.startFix(),
    startLabel: 'Старт',
  },
  {
    key: 'rezka',
    label: 'Rezka · ссылки',
    description: 'Связывание раздач со страницами Rezka.',
    start: () => sync.startSyncRezka(),
    startLabel: 'Старт',
  },
  {
    key: 'rezka_collections',
    label: 'Rezka · папки',
    description: 'Синхронизация коллекций с папками Rezka.',
    start: () => sync.startRezkaCollections(),
    startLabel: 'Старт',
  },
  {
    key: 'kinopub',
    label: 'kino.pub · матчер',
    description:
      'Сопоставляет раздачи с каталогом kino.pub: пишет kinopub_id / kinopub_url. Требует включённой OAuth-авторизации (Admin → kino.pub).',
    start: () => sync.startSyncKinopub(),
    startLabel: 'Старт',
  },
  {
    key: 'cleanup',
    label: 'Очистка дубликатов',
    description: 'Сворачивает повторы из items / releases.',
    start: () => sync.startCleanup(),
    startLabel: 'Старт',
  },
  {
    key: 'user',
    label: 'CSV / Пользовательский импорт',
    description:
      'Запускает user_sync.py — импорт списка пользователя с диска. Логи пойдут в user_sync_log.txt.',
    start: () => sync.startSyncUser(),
    startLabel: 'Старт',
  },
]

// `force=true` checkbox for the `reprocess` row. We bind it via a
// ref so the `start` closure in CONTROLS picks the current value at
// click-time (not at panel-mount time).
const reprocessForce = ref<boolean>(false)

const STATUS_LABELS: Record<string, string> = {
  idle: 'Не запущен',
  queued: 'В очереди',
  running: 'Идёт',
  completed: 'Готово',
  stopped: 'Остановлен',
  error: 'Ошибка',
}

const STATUS_TONES: Record<string, string> = {
  idle: 'bg-slate-100 text-slate-500',
  queued: 'bg-amber-100 text-amber-700',
  running: 'bg-emerald-100 text-emerald-700',
  completed: 'bg-sky-100 text-sky-700',
  stopped: 'bg-slate-200 text-slate-600',
  error: 'bg-red-100 text-red-700',
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status
}

function statusTone(status: string): string {
  return STATUS_TONES[status] ?? STATUS_TONES.idle
}

function progressFor(key: ProcessKey): string {
  const p = sync.progress[key]
  if (!p || p.total <= 0) return ''
  return `${p.current} / ${p.total}`
}

function progressPct(key: ProcessKey): number {
  const p = sync.progress[key]
  if (!p || p.total <= 0) return 0
  return Math.min(100, Math.round((p.current / p.total) * 100))
}

const REZKA_LABELS: Record<string, string> = {
  up: 'Rezka: онлайн',
  connecting: 'Rezka: подключение',
  down: 'Rezka: offline',
}

const REZKA_TONES: Record<string, string> = {
  up: 'bg-emerald-50 text-emerald-700',
  connecting: 'bg-amber-50 text-amber-700',
  down: 'bg-slate-100 text-slate-500',
}

function onClose(): void {
  emit('close')
}

watch(
  () => props.open,
  (isOpen, wasOpen) => {
    if (isOpen && !wasOpen) {
      // Pull a fresh snapshot on open in case the WS is mid-reconnect.
      void sync.fetchStatus()
    }
  },
)
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
    data-testid="sync-panel-backdrop"
    @click.self="onClose"
  >
    <div
      class="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
      data-testid="sync-panel"
      role="dialog"
      aria-modal="true"
      aria-labelledby="sync-panel-title"
    >
      <header class="flex items-center justify-between border-b border-slate-200 px-5 py-3">
        <div>
          <h2
            id="sync-panel-title"
            class="text-base font-semibold text-slate-900"
          >
            Синхронизация и фоновые задачи
          </h2>
          <p class="text-xs text-slate-500">
            Управление парсингом, обогащением и обслуживанием базы.
          </p>
        </div>
        <div class="flex items-center gap-2">
          <span
            class="rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider"
            :class="REZKA_TONES[sync.rezkaSession]"
            data-testid="sync-panel-rezka-badge"
          >
            {{ REZKA_LABELS[sync.rezkaSession] }}
          </span>
          <span
            class="rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider"
            :class="sync.wsConnected ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'"
            data-testid="sync-panel-ws-badge"
          >
            {{ sync.wsConnected ? 'WS: live' : 'WS: polling' }}
          </span>
          <button
            type="button"
            class="rounded-md p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-700"
            aria-label="Закрыть"
            data-testid="sync-panel-close"
            @click="onClose"
          >
            ×
          </button>
        </div>
      </header>

      <div class="flex-1 space-y-3 overflow-y-auto p-5 text-sm text-slate-700">
        <section
          class="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3"
          data-testid="sync-filters"
        >
          <h3 class="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Фильтры для Sync · Видео / Игры
          </h3>
          <p class="mt-1 text-[11px] text-slate-500">
            Не обязательны. Проходят как query string в
            <code class="font-mono">/api/start_sync_video</code> /
            <code class="font-mono">/api/start_sync_other</code>.
          </p>
          <div class="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
            <label class="flex flex-col text-[11px] font-semibold text-slate-600">
              Мин. год
              <input
                v-model="filters.minYear"
                type="number"
                inputmode="numeric"
                placeholder="2010"
                class="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                data-testid="sync-filter-min-year"
              />
            </label>
            <label class="flex flex-col text-[11px] font-semibold text-slate-600">
              Макс. год
              <input
                v-model="filters.maxYear"
                type="number"
                inputmode="numeric"
                placeholder="2024"
                class="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                data-testid="sync-filter-max-year"
              />
            </label>
            <label class="flex flex-col text-[11px] font-semibold text-slate-600">
              Релизы после
              <input
                v-model="filters.minDate"
                type="date"
                class="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                data-testid="sync-filter-min-date"
              />
            </label>
          </div>
        </section>

        <article
          v-for="control in CONTROLS"
          :key="control.key"
          class="rounded-xl border border-slate-200 p-4"
          :data-testid="`sync-row-${control.key}`"
        >
          <div class="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <h3 class="text-sm font-semibold text-slate-900">{{ control.label }}</h3>
                <span
                  :class="['rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider', statusTone(sync.statuses[control.key])]"
                  :data-testid="`sync-status-${control.key}`"
                >
                  {{ statusLabel(sync.statuses[control.key]) }}
                </span>
              </div>
              <p class="mt-1 text-xs text-slate-500">{{ control.description }}</p>
              <label
                v-if="control.key === 'reprocess'"
                class="mt-2 inline-flex items-center gap-1.5 text-xs text-slate-600"
                data-testid="sync-reprocess-force-label"
              >
                <input
                  v-model="reprocessForce"
                  type="checkbox"
                  class="h-3 w-3"
                  data-testid="sync-reprocess-force"
                />
                Принудительно (не пропускать уже обработанные)
              </label>
              <div
                v-if="progressFor(control.key)"
                class="mt-2 flex items-center gap-2"
                :data-testid="`sync-progress-${control.key}`"
              >
                <div class="h-1.5 w-32 overflow-hidden rounded-full bg-slate-200">
                  <div
                    class="h-full bg-emerald-500 transition-all"
                    :style="{ width: `${progressPct(control.key)}%` }"
                  ></div>
                </div>
                <span class="text-[11px] font-medium text-slate-500">
                  {{ progressFor(control.key) }}
                </span>
              </div>
            </div>
            <div class="flex items-center gap-2 self-start">
              <button
                v-if="sync.statuses[control.key] === 'running'"
                type="button"
                class="rounded-md border border-red-200 bg-red-50 px-3 py-1 text-xs font-semibold text-red-700 hover:bg-red-100"
                :data-testid="`sync-stop-${control.key}`"
                @click="sync.stop(control.key)"
              >
                Стоп
              </button>
              <button
                v-else
                type="button"
                class="rounded-md border border-slate-200 bg-slate-900 px-3 py-1 text-xs font-semibold text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="sync.anyBusy"
                :data-testid="`sync-start-${control.key}`"
                @click="runStart(control.key, control.start)"
              >
                {{ control.startLabel }}
              </button>
            </div>
          </div>
        </article>

        <p
          v-if="sync.lastError"
          class="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800"
          data-testid="sync-panel-error"
        >
          {{ sync.lastError }}
        </p>
      </div>
    </div>
  </div>
</template>
