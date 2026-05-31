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

import { onBeforeUnmount, reactive, watch, ref } from 'vue'
import { useFeedStore } from '../../stores/feed'
import { useCategoriesStore } from '../../stores/categories'

const showAdvancedFilters = ref(false)

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

// Toggles and rating sliders fire without debounce so the user sees
// filtered results immediately. Text/date/year inputs keep the
// debounced path so typing doesn't fan out requests.
watch(
  () => [form.hideRated, form.hideCollected],
  () => scheduleApply(true),
)
watch(
  () => [
    form.minKp,
    form.maxKp,
    form.minImdb,
    form.maxImdb,
  ],
  () => scheduleApply(true),
)
watch(
  () => [
    form.search,
    form.minYear,
    form.maxYear,
    form.minDate,
    form.maxDate,
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
    class="rounded-2xl border border-slate-200/60 bg-white/50 backdrop-blur-sm p-4 shadow-sm"
    data-testid="feed-filters"
    @submit.prevent="apply"
  >
    <!-- Add CSS fix for range sliders so they only respond to thumb drag, not track clicks -->
    <component is="style">
      .drag-only-slider {
        pointer-events: none;
      }
      .drag-only-slider::-webkit-slider-thumb {
        pointer-events: auto;
      }
      .drag-only-slider::-moz-range-thumb {
        pointer-events: auto;
      }
    </component>

    <div class="flex flex-wrap items-end gap-3">
      <label class="flex-1 min-w-[180px]">
        <span class="block text-xs font-medium uppercase tracking-wide text-slate-500">
          Поиск
        </span>
        <input
          v-model="form.search"
          type="search"
          placeholder="Название, оригинал, актёр…"
          class="mt-1 w-full rounded-xl border border-slate-200/80 bg-white/80 px-3 py-2 text-[13px] shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 transition-all"
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
          class="mt-1 w-full rounded-xl border border-slate-200/80 bg-white/80 px-3 py-2 text-[13px] shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 transition-all"
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
          class="mt-1 w-full rounded-xl border border-slate-200/80 bg-white/80 px-3 py-2 text-[13px] shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 transition-all"
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
          class="rounded-xl border border-slate-200/80 bg-white px-4 py-2 text-[13px] font-semibold text-slate-700 hover:bg-slate-50 hover:text-slate-900 transition-colors shadow-sm"
          data-testid="feed-filters-reset"
          @click="reset"
        >
          Сбросить
        </button>
      </div>
    </div>

    <!-- Mobile collapsible toggle -->
    <div class="mt-3 flex sm:hidden border-t border-slate-100 pt-3">
      <button
        type="button"
        class="flex w-full items-center justify-between text-[13px] font-semibold text-slate-600 hover:text-slate-900 focus:outline-none"
        @click="showAdvancedFilters = !showAdvancedFilters"
      >
        <span>Настройки фильтров</span>
        <svg class="w-4 h-4 transition-transform duration-200" :class="showAdvancedFilters ? 'rotate-180' : ''" viewBox="0 0 20 20" fill="currentColor">
          <path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd" />
        </svg>
      </button>
    </div>

    <div
      class="flex-wrap items-end gap-3 border-t border-slate-100 pt-3"
      :class="showAdvancedFilters ? 'flex mt-3 sm:border-t-0 sm:pt-0' : 'hidden sm:flex mt-3'"
    >
      <fieldset class="flex min-w-[220px] flex-1 flex-col gap-2">
        <legend class="flex items-center justify-between gap-3 text-xs font-medium uppercase tracking-wide text-slate-500">
          <span>Рейтинг КП</span>
          <span class="font-mono text-[11px] text-slate-700">
            {{ form.minKp.toFixed(1) }} — {{ form.maxKp.toFixed(1) }}
          </span>
        </legend>
        <div class="flex flex-col gap-1.5">
          <input
            v-model.number="form.minKp"
            type="range"
            min="0"
            max="10"
            step="0.1"
            class="w-full accent-slate-900 drag-only-slider"
            data-testid="feed-filters-min-kp"
            aria-label="Минимальный рейтинг КП"
          />
          <input
            v-model.number="form.maxKp"
            type="range"
            min="0"
            max="10"
            step="0.1"
            class="w-full accent-slate-900 drag-only-slider"
            data-testid="feed-filters-max-kp"
            aria-label="Максимальный рейтинг КП"
          />
        </div>
      </fieldset>
      <fieldset class="flex min-w-[220px] flex-1 flex-col gap-2">
        <legend class="flex items-center justify-between gap-3 text-xs font-medium uppercase tracking-wide text-slate-500">
          <span>Рейтинг IMDB</span>
          <span class="font-mono text-[11px] text-slate-700">
            {{ form.minImdb.toFixed(1) }} — {{ form.maxImdb.toFixed(1) }}
          </span>
        </legend>
        <div class="flex flex-col gap-1.5">
          <input
            v-model.number="form.minImdb"
            type="range"
            min="0"
            max="10"
            step="0.1"
            class="w-full accent-slate-900 drag-only-slider"
            data-testid="feed-filters-min-imdb"
            aria-label="Минимальный рейтинг IMDB"
          />
          <input
            v-model.number="form.maxImdb"
            type="range"
            min="0"
            max="10"
            step="0.1"
            class="w-full accent-slate-900 drag-only-slider"
            data-testid="feed-filters-max-imdb"
            aria-label="Максимальный рейтинг IMDB"
          />
        </div>
      </fieldset>
      <label class="flex items-center gap-2 text-[13px] font-medium text-slate-700 bg-white/60 px-3 py-1.5 rounded-lg border border-slate-200/60 hover:bg-white transition-colors cursor-pointer">
        <input
          v-model="form.hideRated"
          type="checkbox"
          class="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
          data-testid="feed-filters-hide-rated"
        />
        Скрыть просмотренные
      </label>
      <label class="flex items-center gap-2 text-[13px] font-medium text-slate-700 bg-white/60 px-3 py-1.5 rounded-lg border border-slate-200/60 hover:bg-white transition-colors cursor-pointer">
        <input
          v-model="form.hideCollected"
          type="checkbox"
          class="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
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
            class="rounded-xl border border-slate-200/80 bg-white/80 px-2 py-1.5 text-[13px] shadow-sm focus:border-indigo-500 focus:outline-none transition-all"
            data-testid="feed-filters-min-date"
          />
          <span class="text-xs text-slate-400">—</span>
          <input
            v-model="form.maxDate"
            type="date"
            class="rounded-xl border border-slate-200/80 bg-white/80 px-2 py-1.5 text-[13px] shadow-sm focus:border-indigo-500 focus:outline-none transition-all"
            data-testid="feed-filters-max-date"
          />
        </div>
      </fieldset>
    </div>
  </form>
</template>
