<script setup lang="ts">
// ROADMAP Stage 10.7c — Stats + Job History dashboard modal.
//
// Replaces the legacy "📊 СТАТИСТИКА" button + `showDashboard` modal
// in `index.html` (~lines 497, 1049-1145). Renders:
//   - four aggregate tiles (total_video / no_poster / no_ratings /
//     no_rezka),
//   - a "нужно обогащение" banner when any backlog counter is > 0
//     (legacy also wires a "🚀 ЗАПУСТИТЬ ОБОГАЩЕНИЕ" button here, but
//     that hooks into the sync-controls UI being ported in 10.7d —
//     we leave only the informational banner for now),
//   - the last N job_history rows.
//
// Behaviour: opens when the `open` prop flips to `true`, which
// triggers a `stats.refresh()`. Closing emits `close` and resets the
// store so the next open starts from a clean state instead of
// flashing stale numbers.

import { computed, watch } from 'vue'

import { useStatsStore, type JobHistoryEntry } from '../stores/stats'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'run-full-pipeline'): void
}>()

const stats = useStatsStore()

/**
 * Legacy `logTypes` map from `index.html:1171`. Keys are stored in the
 * DB as either bare ids (`rezka`, `user`, ...) or `sync_*` aliases —
 * the legacy code does `job.job_type.replace('sync_', '')` before
 * looking up. We replicate that lookup so the rendered labels match.
 */
const JOB_TYPE_LABELS: Record<string, string> = {
  reprocess: 'Обновление',
  video: 'Видео',
  other: 'Остальное',
  fix: 'Поиск',
  fix_poiskkino: 'PoiskKino',
  rezka: 'Rezka',
  rezka_collections: 'Rezka Папки',
  user: 'CSV',
  cleanup: 'Чистка',
  single_update: 'Карточка',
}

const STATUS_LABELS: Record<string, string> = {
  completed: 'Успешно',
  error: 'Ошибка',
  stopped: 'Стоп',
}

const STATUS_TONES: Record<string, string> = {
  completed: 'bg-emerald-100 text-emerald-700',
  error: 'bg-red-100 text-red-700',
  stopped: 'bg-slate-100 text-slate-600',
}

function jobLabel(jobType: string): string {
  const key = jobType.replace(/^sync_/, '')
  return JOB_TYPE_LABELS[key] ?? jobType
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? STATUS_LABELS.stopped
}

function statusTone(status: string): string {
  return STATUS_TONES[status] ?? STATUS_TONES.stopped
}

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '—'
  const d = new Date(dateStr)
  if (Number.isNaN(d.getTime())) return dateStr
  return d.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '—'
  if (seconds < 60) return `${Math.round(seconds)}с`
  const min = Math.floor(seconds / 60)
  const sec = Math.round(seconds % 60)
  return `${min}м ${sec}с`
}

function trackJob(_idx: number, job: JobHistoryEntry): number | string {
  return job.id ?? job.start_time
}

watch(
  () => props.open,
  (isOpen, wasOpen) => {
    if (isOpen && !wasOpen) {
      void stats.refresh(10)
    }
    if (!isOpen && wasOpen) {
      stats.reset()
    }
  },
)

function onClose(): void {
  emit('close')
}

function onRunFullPipeline(): void {
  emit('run-full-pipeline')
}

const hasBacklog = computed(() => stats.hasEnrichmentBacklog)
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
    data-testid="stats-panel-backdrop"
    @click.self="onClose"
  >
    <div
      class="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
      data-testid="stats-panel"
      role="dialog"
      aria-modal="true"
      aria-labelledby="stats-panel-title"
    >
      <header class="flex items-center justify-between border-b border-slate-200 px-5 py-3">
        <div>
          <h2
            id="stats-panel-title"
            class="text-base font-semibold text-slate-900"
          >
            Статистика и история запусков
          </h2>
          <p class="text-xs text-slate-500">
            Сводка по базе и последние задачи синхронизации.
          </p>
        </div>
        <button
          type="button"
          class="rounded-md p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-700"
          aria-label="Закрыть"
          data-testid="stats-panel-close"
          @click="onClose"
        >
          ×
        </button>
      </header>

      <div class="flex-1 space-y-5 overflow-y-auto p-5 text-sm text-slate-700">
        <p
          v-if="stats.loading"
          class="text-xs text-slate-500"
          data-testid="stats-panel-loading"
        >
          Загрузка…
        </p>
        <p
          v-if="stats.error"
          class="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800"
          data-testid="stats-panel-error"
        >
          {{ stats.error }}
        </p>

        <section
          class="grid grid-cols-2 gap-3 md:grid-cols-4"
          data-testid="stats-panel-tiles"
        >
          <div
            class="rounded-xl border border-slate-200 bg-slate-900 p-4 text-white"
            data-testid="stats-tile-total"
          >
            <p class="text-[10px] font-semibold uppercase tracking-wider text-white/60">
              Всего видео
            </p>
            <p class="mt-1 text-2xl font-bold">{{ stats.stats.total_video }}</p>
          </div>
          <div
            class="rounded-xl border border-indigo-100 bg-indigo-50 p-4"
            data-testid="stats-tile-no-poster"
          >
            <p class="text-[10px] font-semibold uppercase tracking-wider text-indigo-500">
              Без постеров
            </p>
            <p class="mt-1 text-2xl font-bold text-indigo-700">
              {{ stats.stats.no_poster }}
            </p>
          </div>
          <div
            class="rounded-xl border border-orange-100 bg-orange-50 p-4"
            data-testid="stats-tile-no-ratings"
          >
            <p class="text-[10px] font-semibold uppercase tracking-wider text-orange-500">
              Без рейтингов
            </p>
            <p class="mt-1 text-2xl font-bold text-orange-700">
              {{ stats.stats.no_ratings }}
            </p>
          </div>
          <div
            class="rounded-xl border border-purple-100 bg-purple-50 p-4"
            data-testid="stats-tile-no-rezka"
          >
            <p class="text-[10px] font-semibold uppercase tracking-wider text-purple-500">
              Без Rezka
            </p>
            <p class="mt-1 text-2xl font-bold text-purple-700">
              {{ stats.stats.no_rezka }}
            </p>
          </div>
        </section>

        <section
          v-if="hasBacklog"
          class="flex flex-col gap-3 rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
          data-testid="stats-panel-backlog"
        >
          <div>
            <h3 class="text-sm font-semibold text-indigo-900">
              Нужно обогащение данных
            </h3>
            <p class="mt-1 text-xs text-indigo-900/80">
              Без постеров: {{ stats.stats.no_poster }} ·
              без рейтингов: {{ stats.stats.no_ratings }} ·
              без Rezka: {{ stats.stats.no_rezka }}.
            </p>
          </div>
          <button
            type="button"
            class="shrink-0 rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500"
            data-testid="stats-panel-run-full-pipeline"
            @click="onRunFullPipeline"
          >
            Запустить полный пайплайн
          </button>
        </section>

        <section>
          <div class="mb-2 flex items-center justify-between">
            <h3 class="text-sm font-semibold text-slate-900">
              История запусков
            </h3>
            <span class="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              Последние 10 задач
            </span>
          </div>
          <div class="overflow-hidden rounded-xl border border-slate-200">
            <table
              class="w-full border-collapse text-left"
              data-testid="stats-panel-history"
            >
              <thead>
                <tr class="border-b border-slate-100 bg-slate-50 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                  <th class="px-3 py-2">Процесс</th>
                  <th class="px-3 py-2">Старт</th>
                  <th class="px-3 py-2">Время</th>
                  <th class="px-3 py-2 text-center">Элементов</th>
                  <th class="px-3 py-2 text-right">Статус</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-100">
                <tr
                  v-for="(job, idx) in stats.jobHistory"
                  :key="trackJob(idx, job)"
                  class="hover:bg-slate-50"
                  data-testid="stats-panel-history-row"
                >
                  <td class="px-3 py-2 text-xs font-semibold uppercase text-slate-900">
                    {{ jobLabel(job.job_type) }}
                  </td>
                  <td class="px-3 py-2 text-xs text-slate-500">
                    {{ formatDate(job.start_time) }}
                  </td>
                  <td class="px-3 py-2 text-xs text-slate-500">
                    {{ formatDuration(job.duration) }}
                  </td>
                  <td class="px-3 py-2 text-center text-xs text-slate-700">
                    {{ job.items_processed }}
                    <span
                      v-if="job.total_items > 0"
                      class="text-[10px] text-slate-400"
                    >
                      / {{ job.total_items }}
                    </span>
                  </td>
                  <td class="px-3 py-2 text-right">
                    <span
                      :class="[
                        'rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider',
                        statusTone(job.status),
                      ]"
                    >
                      {{ statusLabel(job.status) }}
                    </span>
                  </td>
                </tr>
                <tr v-if="stats.jobHistory.length === 0">
                  <td
                    colspan="5"
                    class="px-3 py-6 text-center text-xs italic text-slate-400"
                    data-testid="stats-panel-history-empty"
                  >
                    История пуста
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>
