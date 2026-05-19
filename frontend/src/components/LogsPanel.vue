<script setup lang="ts">
// ROADMAP Stage 10.7e — log viewer modal.
//
// Visual continuity with the legacy bottom-docked log panel at
// `index.html:99-134`: dark slate header with tab pills, monospace
// "terminal" body in green-on-black, and the same action buttons
// (refresh / stop / clear / download / close). The legacy variant
// was a slide-up dock that took 70vh; we keep the same form factor.

import { nextTick, ref, watch } from 'vue'

import {
  LOG_TYPE_LABELS,
  type LogType,
  useLogsStore,
} from '../stores/logs'
import { useSyncStore } from '../stores/sync'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const logs = useLogsStore()
const sync = useSyncStore()

const bodyEl = ref<HTMLDivElement | null>(null)

const TAB_ORDER: LogType[] = [
  'full_pipeline',
  'reprocess',
  'video',
  'other',
  'fix',
  'fix_poiskkino',
  'rezka',
  'rezka_collections',
  'tmdb',
  'kinopub',
  'kinopub_collections',
  'user',
  'cleanup',
  'single_update',
]

function onClose(): void {
  emit('close')
}

function onSelectTab(type: LogType): void {
  void logs.selectType(type)
}

function onRefresh(): void {
  void logs.refresh()
}

function onClear(): void {
  void logs.clear()
}

function onDownload(): void {
  void logs.download()
}

function onStop(): void {
  void sync.stop(logs.currentProcessKey)
}

watch(
  () => props.open,
  (isOpen, wasOpen) => {
    if (isOpen && !wasOpen) logs.open()
    if (!isOpen && wasOpen) logs.close()
  },
)

// Auto-scroll to the bottom whenever the content changes, but only
// if the user wasn't actively scrolled away. Mirrors the legacy
// `wasAtBottom` heuristic at `index.html:1914-1922`.
watch(
  () => logs.content,
  async () => {
    const el = bodyEl.value
    if (!el) return
    const wasAtBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 50
    const isSelecting =
      typeof window !== 'undefined' &&
      window.getSelection
        ? (window.getSelection()?.toString().length ?? 0) > 0
        : false
    await nextTick()
    if (wasAtBottom && !isSelecting) {
      el.scrollTop = el.scrollHeight
    }
  },
)
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-x-0 bottom-0 z-50 flex flex-col"
    style="max-height: 70vh"
    data-testid="logs-panel"
  >
    <div
      class="flex flex-1 min-h-0 flex-col rounded-t-2xl border-t border-slate-700 bg-slate-900 shadow-2xl"
    >
      <div
        class="flex shrink-0 items-center justify-between gap-3 border-b border-slate-700 px-4 py-2.5"
      >
        <div class="flex items-center gap-3 overflow-x-auto">
          <div class="flex rounded-lg bg-slate-800 p-0.5">
            <button
              v-for="type in TAB_ORDER"
              :key="type"
              type="button"
              :class="[
                'whitespace-nowrap rounded-md px-2.5 py-1 text-[10px] font-bold transition-all',
                logs.selectedType === type
                  ? 'bg-white text-black shadow-sm'
                  : 'text-slate-400 hover:text-white',
              ]"
              :data-testid="`logs-tab-${type}`"
              @click="onSelectTab(type)"
            >
              {{ LOG_TYPE_LABELS[type] }}
            </button>
          </div>
          <span
            class="shrink-0 font-mono text-[9px] text-slate-500"
            data-testid="logs-filename"
          >
            {{ logs.currentFilename }}
          </span>
        </div>
        <div class="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            class="rounded bg-slate-700 px-2 py-1 text-[10px] font-bold text-slate-300 hover:bg-slate-600"
            data-testid="logs-refresh"
            aria-label="Обновить лог"
            @click="onRefresh"
          >
            ↻
          </button>
          <button
            v-if="sync.statuses[logs.currentProcessKey] === 'running'"
            type="button"
            class="rounded bg-red-600 px-2 py-1 text-[10px] font-bold text-white hover:bg-red-700"
            data-testid="logs-stop"
            aria-label="Остановить активный процесс"
            @click="onStop"
          >
            ⏹
          </button>
          <button
            type="button"
            class="rounded bg-slate-700 px-2 py-1 text-[10px] font-bold text-slate-300 hover:bg-slate-600"
            data-testid="logs-clear"
            aria-label="Очистить файл лога"
            @click="onClear"
          >
            ✕ Очистить
          </button>
          <button
            type="button"
            class="rounded bg-indigo-600 px-2 py-1 text-[10px] font-bold text-white hover:bg-indigo-700"
            data-testid="logs-download"
            aria-label="Скачать лог"
            @click="onDownload"
          >
            ↓
          </button>
          <button
            type="button"
            class="ml-1 text-lg text-slate-400 hover:text-white"
            data-testid="logs-close"
            aria-label="Закрыть"
            @click="onClose"
          >
            ✕
          </button>
        </div>
      </div>
      <div
        ref="bodyEl"
        class="min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap bg-black p-3 font-mono text-[11px] text-green-400"
        data-testid="logs-body"
      >
        <div
          v-if="logs.error"
          class="mb-2 rounded bg-red-950/40 border border-red-800/40 px-2.5 py-1.5 text-xs text-red-400 flex items-center gap-1.5 shrink-0"
          data-testid="logs-error"
        >
          <span>⚠️ Ошибка получения логов: {{ logs.error }} (отображаются кэшированные данные)</span>
        </div>
        <template v-if="logs.content">{{ logs.content }}</template>
        <span v-else class="text-slate-500 italic">Пусто</span>
      </div>
    </div>
  </div>
</template>
