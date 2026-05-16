<script setup lang="ts">
// ROADMAP Stage 10.4 — feed grid page.
// ROADMAP Stage 10.5 — composes the categories + collections sidebar
// alongside the grid and triggers `refresh()` on the sidebar stores
// whenever the session becomes API-callable.

import { onMounted, onUnmounted, watch, ref } from 'vue'
import FeedFilters from './FeedFilters.vue'
import FeedItemCard from './FeedItemCard.vue'
import Sidebar from './Sidebar.vue'
import { useFeedStore } from '../../stores/feed'
import { useCategoriesStore } from '../../stores/categories'
import { useCollectionsStore } from '../../stores/collections'
import { useSessionStore } from '../../stores/session'
import { useVisitStore } from '../../stores/visits'
import { attachFilterPersistence } from '../../composables/useFilterPersistence'

const feed = useFeedStore()
const categories = useCategoriesStore()
const collections = useCollectionsStore()
const session = useSessionStore()
const visits = useVisitStore()
const showMobileSidebar = ref(window.sessionStorage.getItem('f_showMobileSidebar') === 'true')

watch(showMobileSidebar, (val) => {
  if (val) {
    window.sessionStorage.setItem('f_showMobileSidebar', 'true')
  } else {
    window.sessionStorage.removeItem('f_showMobileSidebar')
  }
})
let detachPersistence: (() => void) | null = null

async function refreshAll(): Promise<void> {
  await Promise.all([
    categories.refresh(feed.filters.hideRated, feed.filters.hideCollected),
    collections.refresh(),
    visits.refresh(),
    feed.fetchFeed(),
  ])
}

// Stage 10.7b — record a visit when the tab is being closed (or
// hidden, since modern browsers no longer fire `beforeunload`
// reliably). `pagehide` works in every modern browser and survives
// bfcache transitions.
function onPageHide(): void {
  void visits.markVisited()
}

onMounted(() => {
  // Stage 10.7a — hydrate the feed store from sessionStorage and
  // start the watchers that mirror future mutations back into it.
  // Done before the first fetch so persisted filters drive the URL.
  detachPersistence = attachFilterPersistence()
  window.addEventListener('pagehide', onPageHide)
  if (session.canCallApi) void refreshAll()
})

onUnmounted(() => {
  detachPersistence?.()
  detachPersistence = null
  window.removeEventListener('pagehide', onPageHide)
})

// Auto-fetch as soon as the session moves into an API-callable
// state (e.g. after the user logs in via the modal).
watch(
  () => session.canCallApi,
  (next, prev) => {
    if (next && !prev) void refreshAll()
  },
)

function retry(): void {
  void feed.fetchFeed()
}
</script>

<template>
  <section class="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8" data-testid="feed-view">
    <header class="mb-4 sm:mb-6 flex flex-col gap-2">
      <div class="flex items-center justify-between gap-3">
        <h1 class="text-2xl font-bold text-slate-900">Лента релизов</h1>
        <button
          type="button"
          class="lg:hidden rounded-full border border-slate-200/60 bg-white/50 backdrop-blur-sm px-4 py-2 text-xs font-semibold text-slate-700 shadow-sm hover:bg-white transition-colors shrink-0"
          @click="showMobileSidebar = !showMobileSidebar"
        >
          {{ showMobileSidebar ? 'Скрыть фильтры' : 'Фильтры' }}
        </button>
      </div>
      <p class="text-sm text-slate-600">
        Список новых и обновлённых раздач, собранных с Rutor и обогащённых
        метаданными.
      </p>
    </header>

    <div class="flex flex-col gap-6 lg:flex-row lg:items-start">
      <div :class="showMobileSidebar ? 'block mb-6 lg:mb-0' : 'hidden lg:block'" class="w-full lg:w-auto shrink-0">
        <Sidebar />
      </div>
      <div class="flex-1 min-w-0">
        <FeedFilters class="mb-6" />

        <div
          v-if="feed.connectionError"
          class="mb-4 flex items-center justify-between rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-red-700"
          data-testid="feed-connection-error"
        >
          <span class="text-sm font-medium">
            Сервер недоступен. Проверьте подключение.
          </span>
          <button
            class="text-sm font-semibold underline hover:text-red-900"
            data-testid="feed-retry"
            @click="retry"
          >
            Повторить
          </button>
        </div>

        <div
          v-if="feed.loading"
          class="rounded-xl border border-slate-200 bg-white py-16 text-center"
          data-testid="feed-loading"
        >
          <p class="text-sm font-medium text-slate-500">Загрузка ленты…</p>
        </div>

        <div
          v-else-if="!feed.hasItems"
          class="rounded-xl border border-slate-200 bg-white py-16 text-center"
          data-testid="feed-empty"
        >
          <p class="text-sm font-medium text-slate-500">
            В этой категории пока ничего нет или всё отфильтровано.
          </p>
        </div>

        <div
          v-else
          class="grid grid-cols-1 gap-4 sm:grid-cols-2 sm:gap-6 md:grid-cols-3 lg:grid-cols-3 xl:grid-cols-4"
          data-testid="feed-grid"
        >
          <FeedItemCard v-for="item in feed.items" :key="item.id" :item="item" />
        </div>

        <nav
          v-if="feed.hasItems && feed.totalPages > 1"
          class="mt-6 flex items-center justify-center gap-3"
          data-testid="feed-pagination"
        >
          <button
            type="button"
            class="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!feed.canPrevPage || feed.loading"
            data-testid="feed-prev"
            @click="feed.prevPage()"
          >
            ← Назад
          </button>
          <span class="text-sm text-slate-600" data-testid="feed-page-indicator">
            Страница {{ feed.page }} из {{ feed.totalPages }}
          </span>
          <button
            type="button"
            class="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="!feed.canNextPage || feed.loading"
            data-testid="feed-next"
            @click="feed.nextPage()"
          >
            Вперёд →
          </button>
        </nav>
      </div>
    </div>
  </section>
</template>
