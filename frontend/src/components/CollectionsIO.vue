<script setup lang="ts">
// ROADMAP Stage 10.7i — collections export / import section.
//
// Visual continuity with the «База данных» section of `AdminPanel`:
// stacked rounded-border card with title + description + a row of
// action buttons. Adds a Merge / Replace toggle that drives the
// `mode` argument of both import paths, plus separate hidden file
// inputs for JSON and CSV so the on-change handler can pick the
// right endpoint without sniffing extension twice.

import { computed, ref } from 'vue'

import {
  type CollectionsImportMode,
  useCollectionsIOStore,
} from '../stores/collectionsIO'
import { useCollectionsStore } from '../stores/collections'
import { useFeedStore } from '../stores/feed'

const emit = defineEmits<{ (e: 'imported'): void }>()

const io = useCollectionsIOStore()
const collections = useCollectionsStore()
const feed = useFeedStore()

const mode = ref<CollectionsImportMode>('merge')

const jsonInputRef = ref<HTMLInputElement | null>(null)
const csvInputRef = ref<HTMLInputElement | null>(null)

const anyBusy = computed(() => io.exportBusy || io.importBusy)

const toneClass = computed(() => {
  switch (io.lastResult?.tone) {
    case 'success':
      return 'border-emerald-200 bg-emerald-50 text-emerald-900'
    case 'error':
      return 'border-red-200 bg-red-50 text-red-900'
    case 'info':
    default:
      return 'border-slate-200 bg-slate-50 text-slate-800'
  }
})

async function onExport(fmt: 'json' | 'csv'): Promise<void> {
  await io.exportCollections(fmt)
}

function onImportJsonClick(): void {
  jsonInputRef.value?.click()
}

function onImportCsvClick(): void {
  csvInputRef.value?.click()
}

async function onImportJsonChange(ev: Event): Promise<void> {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  // Reset so picking the same file twice re-fires `change`.
  input.value = ''
  if (!file) return
  const result = await io.importCollections(file, mode.value)
  if (result.tone === 'success') {
    void collections.refresh()
    void feed.fetchFeed()
    emit('imported')
  }
}

async function onImportCsvChange(ev: Event): Promise<void> {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  input.value = ''
  if (!file) return
  const result = await io.importCollectionsCsv(file, mode.value)
  if (result.tone === 'success') {
    void collections.refresh()
    void feed.fetchFeed()
    emit('imported')
  }
}
</script>

<template>
  <section
    class="rounded-lg border border-slate-200 p-4"
    data-testid="collections-io"
  >
    <h3 class="text-sm font-semibold text-slate-900">Коллекции</h3>
    <p class="mt-1 text-xs text-slate-500">
      Экспортируйте все коллекции в один файл или загрузите снимок
      обратно. Импорт принимает JSON-снимок этого же формата либо
      CSV с колонками <code>collection_name, sort_order, kp_id,
      imdb_id, rezka_url, title, original_title, year, added_at</code>.
    </p>

    <p
      v-if="io.lastResult"
      :class="['mt-3 rounded-md border px-3 py-2 text-xs', toneClass]"
      data-testid="collections-io-result"
    >
      {{ io.lastResult.message }}
    </p>

    <div class="mt-3 flex flex-wrap items-center gap-2">
      <button
        type="button"
        class="inline-flex items-center justify-center rounded-md bg-slate-800 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-900 disabled:opacity-50"
        data-testid="collections-export-json"
        :disabled="anyBusy"
        @click="onExport('json')"
      >
        {{ io.exportBusy ? 'Экспорт…' : '⬇ JSON' }}
      </button>
      <button
        type="button"
        class="inline-flex items-center justify-center rounded-md bg-slate-800 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-900 disabled:opacity-50"
        data-testid="collections-export-csv"
        :disabled="anyBusy"
        @click="onExport('csv')"
      >
        ⬇ CSV
      </button>
      <span class="mx-1 h-5 border-l border-slate-200" />
      <fieldset class="inline-flex items-center gap-2 text-xs">
        <legend class="sr-only">Режим импорта</legend>
        <label class="flex items-center gap-1">
          <input
            v-model="mode"
            type="radio"
            value="merge"
            class="cursor-pointer"
            data-testid="collections-mode-merge"
          />
          Merge
        </label>
        <label class="flex items-center gap-1">
          <input
            v-model="mode"
            type="radio"
            value="replace"
            class="cursor-pointer"
            data-testid="collections-mode-replace"
          />
          Replace
        </label>
      </fieldset>
      <button
        type="button"
        class="inline-flex items-center justify-center rounded-md bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-800 hover:bg-slate-200 disabled:opacity-50"
        data-testid="collections-import-json"
        :disabled="anyBusy"
        @click="onImportJsonClick"
      >
        {{ io.importBusy ? 'Импорт…' : '⬆ Импорт JSON' }}
      </button>
      <button
        type="button"
        class="inline-flex items-center justify-center rounded-md bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-800 hover:bg-slate-200 disabled:opacity-50"
        data-testid="collections-import-csv"
        :disabled="anyBusy"
        @click="onImportCsvClick"
      >
        ⬆ Импорт CSV
      </button>
      <input
        ref="jsonInputRef"
        type="file"
        accept=".json,application/json"
        class="hidden"
        data-testid="collections-import-json-input"
        @change="onImportJsonChange"
      />
      <input
        ref="csvInputRef"
        type="file"
        accept=".csv,text/csv"
        class="hidden"
        data-testid="collections-import-csv-input"
        @change="onImportCsvChange"
      />
    </div>
  </section>
</template>
