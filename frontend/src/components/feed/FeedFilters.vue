<script setup lang="ts">
// ROADMAP Stage 10.4 — minimal feed filters (search + year range).
// ROADMAP Stage 10.5 — add KP/IMDB number inputs + hide-rated /
// hide-collected checkboxes.
// Follow-up to 10.7z — filters now auto-apply on change with a small
// debounce so the user gets results without hunting for an "Применить"
// button. Toggles (hide-rated / hide-collected) fire immediately;
// text and number inputs wait ~350 ms so typing doesn't fan out a
// fetch per keystroke. The "Применить" button stays as a no-op
// fallback for Enter on number/date fields in browsers that don't
// emit change events until blur.

import { onBeforeUnmount, reactive, watch } from 'vue'
import { useFeedStore } from '../../stores/feed'
import { useCategoriesStore } from '../../stores/categories'

const feed = useFeedStore()
const categories = useCategoriesStore()

const form = reactive({
  search: feed.filters.search,
  minYear: feed.filters.minYear,
  maxYear: feed.filters.maxYear,
  minDate: feed.filters.minDate,
  maxDate: feed.filters.maxDate,
  minKp: feed.filters.minKp,
  maxKp: feed.filters.maxKp,
  minImdb: feed.filters.minImdb,
  maxImdb: feed.filters.maxImdb,
  hideRated: feed.filters.hideRated,
  hideCollected: feed.filters.hideCollected,
})

let syncingFromStore = false
watch(
  () => ({ ...feed.filters }),
  (next) => {
    syncingFromStore = true
    form.search = next.search
    form.minYear = next.minYear
    form.maxYear = next.maxYear
    form.minDate = next.minDate
    form.maxDate = next.maxDate
    form.minKp = next.minKp
    form.maxKp = next.maxKp
    form.minImdb = next.minImdb
    form.maxImdb = next.maxImdb
    form.hideRated = next.hideRated
    form.hideCollected = next.hideCollected
    // Defer the unset so the form-watcher below sees `syncingFromStore`
    // = true and skips the auto-apply call we'd otherwise loop on.
    queueMicrotask(() => {
      syncingFromStore = false
    })
  },
)

function clamp(v: number | null | undefined, lo: number, hi: number): number {
  const n = typeof v === 'number' && Number.isFinite(v) ? v : lo
  return Math.min(hi, Math.max(lo, n))
}

function normaliseDate(value: string | null): string | null {
  if (!value) return null
  // <input type="date"> already gives YYYY-MM-DD, but guard against
  // someone typing nonsense in jsdom-only fallbacks.
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : null
}

async function apply(): Promise<void> {
  feed.setFilters({
    search: form.search,
    minYear: form.minYear,
    maxYear: form.maxYear,
    minDate: normaliseDate(form.minDate),
    maxDate: normaliseDate(form.maxDate),
    minKp: clamp(form.minKp, 0, 10),
    maxKp: clamp(form.maxKp, 0, 10),
    minImdb: clamp(form.minImdb, 0, 10),
    maxImdb: clamp(form.maxImdb, 0, 10),
    hideRated: form.hideRated,
    hideCollected: form.hideCollected,
  })
  await feed.fetchFeed()
  // Re-pull category counts so they reflect the new hide flags.
  await categories.refresh(form.hideRated, form.hideCollected)
}

async function reset(): Promise<void> {
  feed.resetFilters()
  await feed.fetchFeed()
  await categories.refresh(false, false)
}

const DEBOUNCE_MS = 350
let debounceTimer: ReturnType<typeof setTimeout> | null = null

function scheduleApply(immediate: boolean): void {
  if (syncingFromStore) return
  if (debounceTimer) {
    clearTimeout(debounceTimer)
    debounceTimer = null
  }
  if (immediate) {
    void apply()
    return
  }
  debounceTimer = setTimeout(() => {
    debounceTimer = null
    void apply()
  }, DEBOUNCE_MS)
}

// Toggles flip without debounce so the user sees results the moment
// they tick a checkbox — same UX as the sidebar's "Только новое"
// switch. Numeric and text inputs get the debounced path.
watch(
  () => [form.hideRated, form.hideCollected],
  () => scheduleApply(true),
)
watch(
  () => [
    form.search,
    form.minYear,
    form.maxYear,
    form.minDate,
    form.maxDate,
    form.minKp,
    form.maxKp,
    form.minImdb,
    form.maxImdb,
  ],
  () => scheduleApply(false),
)

onBeforeUnmount(() => {
  if (debounceTimer) {
    clearTimeout(debounceTimer)
    debounceTimer = null
  }
})
</script>

<template>
  <form
    class="rounded-xl border border-slate-200 bg-white p-3"
    data-testid="feed-filters"
    @submit.prevent="apply"
  >
    <div class="flex flex-wrap items-end gap-3">
      <label class="flex-1 min-w-[180px]">
        <span class="block text-xs font-medium uppercase tracking-wide text-slate-500">
          Поиск
        </span>
        <input
          v-model="form.search"
          type="search"
          placeholder="Название, оригинал, актёр…"
          class="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm focus:border-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-900/10"
          data-testid="feed-filters-search"
        />
      </label>
      <label class="w-24">
        <span class="block text-xs font-medium uppercase tracking-wide text-slate-500">
          Год от
        </span>
        <input
          v-model.number="form.minYear"
          type="number"
          min="1900"
          max="2100"
          placeholder="1990"
          class="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm focus:border-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-900/10"
          data-testid="feed-filters-min-year"
        />
      </label>
      <label class="w-24">
        <span class="block text-xs font-medium uppercase tracking-wide text-slate-500">
          Год до
        </span>
        <input
          v-model.number="form.maxYear"
          type="number"
          min="1900"
          max="2100"
          placeholder="2030"
          class="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm focus:border-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-900/10"
          data-testid="feed-filters-max-year"
        />
      </label>
      <div class="flex items-center gap-2">
        <!-- Hidden submit so pressing Enter on a number/date input
             commits the value immediately rather than waiting for
             the 350 ms debounce. -->
        <button
          type="submit"
          class="sr-only"
          tabindex="-1"
          aria-hidden="true"
          data-testid="feed-filters-apply"
        >
          Применить
        </button>
        <button
          type="button"
          class="rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          data-testid="feed-filters-reset"
          @click="reset"
        >
          Сбросить
        </button>
      </div>
    </div>

    <div class="mt-3 flex flex-wrap items-end gap-3 border-t border-slate-100 pt-3">
      <fieldset class="flex flex-col gap-1">
        <legend class="text-xs font-medium uppercase tracking-wide text-slate-500">
          Рейтинг КП
        </legend>
        <div class="flex items-center gap-2">
          <input
            v-model.number="form.minKp"
            type="number"
            min="0"
            max="10"
            step="0.1"
            class="w-20 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm shadow-sm focus:border-slate-900 focus:outline-none"
            data-testid="feed-filters-min-kp"
          />
          <span class="text-xs text-slate-400">—</span>
          <input
            v-model.number="form.maxKp"
            type="number"
            min="0"
            max="10"
            step="0.1"
            class="w-20 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm shadow-sm focus:border-slate-900 focus:outline-none"
            data-testid="feed-filters-max-kp"
          />
        </div>
      </fieldset>
      <fieldset class="flex flex-col gap-1">
        <legend class="text-xs font-medium uppercase tracking-wide text-slate-500">
          Рейтинг IMDB
        </legend>
        <div class="flex items-center gap-2">
          <input
            v-model.number="form.minImdb"
            type="number"
            min="0"
            max="10"
            step="0.1"
            class="w-20 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm shadow-sm focus:border-slate-900 focus:outline-none"
            data-testid="feed-filters-min-imdb"
          />
          <span class="text-xs text-slate-400">—</span>
          <input
            v-model.number="form.maxImdb"
            type="number"
            min="0"
            max="10"
            step="0.1"
            class="w-20 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm shadow-sm focus:border-slate-900 focus:outline-none"
            data-testid="feed-filters-max-imdb"
          />
        </div>
      </fieldset>
      <label class="flex items-center gap-2 text-sm text-slate-700">
        <input
          v-model="form.hideRated"
          type="checkbox"
          class="h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-900"
          data-testid="feed-filters-hide-rated"
        />
        Скрыть просмотренные
      </label>
      <label class="flex items-center gap-2 text-sm text-slate-700">
        <input
          v-model="form.hideCollected"
          type="checkbox"
          class="h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-900"
          data-testid="feed-filters-hide-collected"
        />
        Скрыть в коллекциях
      </label>
      <fieldset class="flex flex-col gap-1">
        <legend class="text-xs font-medium uppercase tracking-wide text-slate-500">
          Дата добавления
        </legend>
        <div class="flex items-center gap-2">
          <input
            v-model="form.minDate"
            type="date"
            class="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm shadow-sm focus:border-slate-900 focus:outline-none"
            data-testid="feed-filters-min-date"
          />
          <span class="text-xs text-slate-400">—</span>
          <input
            v-model="form.maxDate"
            type="date"
            class="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm shadow-sm focus:border-slate-900 focus:outline-none"
            data-testid="feed-filters-max-date"
          />
        </div>
      </fieldset>
    </div>
  </form>
</template>
