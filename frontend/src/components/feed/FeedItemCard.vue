<script setup lang="ts">
// ROADMAP Stage 10.4 — single feed card.
// ROADMAP Stage 10.5 — add the bookmark menu (toggle collections per
// item). Other interactions (ignore, rebind, rezka playback) remain
// out of scope; they land in Stage 10.6 (admin / item modals).

import { computed, ref } from 'vue'
import type { FeedItem } from '../../types/feed'
import { useCollectionsStore } from '../../stores/collections'
import { useItemsStore } from '../../stores/items'
import { useSyncStore } from '../../stores/sync'
import { useVisitStore } from '../../stores/visits'

const props = defineProps<{ item: FeedItem }>()

const collections = useCollectionsStore()
const items = useItemsStore()
const sync = useSyncStore()
const visits = useVisitStore()
const menuOpen = ref(false)
const singleUpdateRunning = computed(
  () => sync.statuses.single_update === 'running',
)

const isNew = computed(() => visits.isNewRelease(props.item.latest_release))

const ratings = computed(() => {
  const list: Array<{ key: string; label: string }> = []
  const kp = props.item.kp_rating ?? 0
  const imdb = props.item.imdb_rating ?? 0
  const user = props.item.user_rating ?? 0
  if (user > 0) list.push({ key: 'user', label: `★ ${user}` })
  if (kp > 0) list.push({ key: 'kp', label: `KP ${kp.toFixed(1)}` })
  if (imdb > 0) list.push({ key: 'imdb', label: `IMDB ${imdb.toFixed(1)}` })
  return list
})

const yearLabel = computed(() => {
  return props.item.year ? String(props.item.year) : ''
})

const titleLabel = computed(() => props.item.title || 'Без названия')

const memberCollections = computed(() =>
  collections.collectionsForItem(props.item.id),
)
const isBookmarked = computed(() => memberCollections.value.length > 0)

async function onToggle(collectionId: number): Promise<void> {
  await collections.toggleItem(props.item.id, collectionId)
}

function isMember(collectionId: number): boolean {
  return memberCollections.value.includes(collectionId)
}

function onOpen(): void {
  void items.open(props.item.id, props.item)
}

async function onHoverReprocess(): Promise<void> {
  // Fire single_update without opening the modal — the user will
  // see a toast and the card will refresh on `single_update`
  // completion via the WS handler.
  await items.reprocessById(props.item.id)
}

function onHoverReset(): void {
  // Open the modal with the reset dialog pre-popped so the user
  // immediately sees the field-selection checkboxes.
  void items.openWithResetDialog(props.item.id, props.item)
}
</script>

<template>
  <article
    class="group bg-white rounded-2xl shadow-sm hover:shadow-xl hover:-translate-y-1 border border-slate-200/60 overflow-hidden flex flex-col transition-all duration-300"
    data-testid="feed-item-card"
    :data-item-id="item.id"
  >
    <div class="relative w-full shrink-0 aspect-[2/3] bg-gray-100 overflow-hidden">
      <img
        v-if="item.poster_url"
        :src="item.poster_url"
        :alt="titleLabel"
        loading="lazy"
        class="w-full h-full object-cover transition-transform duration-500 group-hover:scale-105"
        data-testid="feed-item-poster"
      />
      <div
        v-else
        class="absolute inset-0 flex items-center justify-center text-slate-400"
        data-testid="feed-item-poster-placeholder"
      >
        <svg class="w-10 h-10 opacity-60" viewBox="0 0 24 24" fill="none" stroke="currentColor">
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
          />
        </svg>
      </div>
      <div class="absolute top-2 left-2 flex flex-col gap-1 z-10">
        <span
          v-for="r in ratings"
          :key="r.key"
          class="bg-white/90 px-2 py-0.5 rounded text-[10px] font-bold border border-slate-200"
          :class="r.key === 'user' ? 'text-amber-600 bg-amber-50 border-amber-200 shadow-sm' : 'text-slate-800'"
          :data-testid="`feed-item-rating-${r.key}`"
        >
          {{ r.label }}
        </span>
      </div>
      <div
        v-if="isNew"
        class="absolute top-2 right-2 z-20"
        data-testid="feed-item-new-badge"
      >
        <span class="bg-gradient-to-r from-orange-400 to-rose-500 text-white text-[9px] tracking-wider font-black px-2 py-0.5 rounded-full shadow-md animate-pulse">
          NEW
        </span>
      </div>
      <!-- Hover-only action menu: groups all operations into a single
           expandable dial to keep the UI clean. -->
      <div
        class="absolute bottom-2 left-2 z-10 flex flex-col-reverse items-center gap-1.5 group/actions"
        data-testid="feed-item-hover-actions-menu"
      >
        <button
          type="button"
          class="flex items-center justify-center w-8 h-8 rounded-full bg-white/90 text-slate-700 shadow-md hover:bg-white opacity-0 translate-y-2 transition-all duration-300 group-hover:opacity-100 group-hover:translate-y-0 group-focus-within:opacity-100 group-focus-within:translate-y-0"
          title="Действия"
          aria-label="Действия"
          @click.stop
        >
          <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor">
            <circle cx="12" cy="12" r="1"></circle>
            <circle cx="12" cy="5" r="1"></circle>
            <circle cx="12" cy="19" r="1"></circle>
          </svg>
        </button>

        <div class="flex flex-col gap-1 opacity-0 translate-y-4 pointer-events-none transition-all duration-300 group-hover/actions:opacity-100 group-hover/actions:translate-y-0 group-hover/actions:pointer-events-auto mb-0.5">
          <button
            type="button"
            class="flex items-center justify-center w-7 h-7 rounded-full bg-white/95 text-xs font-bold shadow hover:bg-white transition-colors"
            :class="item.is_watched === 1 ? 'text-emerald-600' : 'text-slate-400'"
            :title="item.is_watched === 1 ? 'Отметить как непросмотренное' : 'Отметить как просмотренное'"
            :aria-label="item.is_watched === 1 ? 'Не просмотрено' : 'Просмотрено'"
            data-testid="feed-item-hover-watched"
            @click.stop="items.toggleWatchedById(item.id)"
          >
            ✓
          </button>
          <button
            type="button"
            class="flex items-center justify-center w-7 h-7 rounded-full bg-white/95 text-xs font-bold shadow hover:bg-white transition-colors disabled:opacity-50"
            :class="item.is_ignored === 1 ? 'text-emerald-600' : 'text-red-600'"
            :title="item.is_ignored === 1 ? 'Восстановить из корзины' : 'Скрыть (в корзину)'"
            :aria-label="item.is_ignored === 1 ? 'Восстановить' : 'Скрыть'"
            data-testid="feed-item-hover-ignore"
            @click.stop="items.toggleIgnoreById(item.id)"
          >
            {{ item.is_ignored === 1 ? '↩' : '✕' }}
          </button>
          <button
            type="button"
            class="flex items-center justify-center w-7 h-7 rounded-full bg-white/95 text-xs font-semibold text-slate-700 shadow hover:bg-white transition-colors disabled:opacity-50"
            :disabled="singleUpdateRunning"
            :title="singleUpdateRunning ? 'Уже идёт перепроверка' : 'Обновить метаданные'"
            aria-label="Обновить метаданные"
            data-testid="feed-item-hover-reprocess"
            @click.stop="onHoverReprocess"
          >
            🔄
          </button>
          <button
            type="button"
            class="flex items-center justify-center w-7 h-7 rounded-full bg-white/95 text-[10px] font-semibold text-slate-400 shadow hover:bg-white transition-colors"
            title="Сбросить данные…"
            aria-label="Сбросить данные"
            data-testid="feed-item-hover-reset"
            @click.stop="onHoverReset"
          >
            🗑️
          </button>
        </div>
      </div>
      <button
        type="button"
        class="absolute bottom-2 right-2 z-10 flex items-center gap-1.5 rounded-full px-2.5 py-1.5 text-xs font-semibold shadow-md backdrop-blur-sm transition-all"
        :class="
          isBookmarked
            ? 'bg-indigo-600 text-white shadow-indigo-600/30'
            : 'bg-white/90 text-slate-700 hover:bg-white'
        "
        data-testid="feed-item-bookmark-toggle"
        :aria-expanded="menuOpen"
        @click.stop="menuOpen = !menuOpen"
      >
        <svg
          class="h-3.5 w-3.5"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z"
          />
        </svg>
        <span data-testid="feed-item-bookmark-count">
          {{ memberCollections.length }}
        </span>
      </button>
      <div
        v-if="menuOpen"
        class="absolute inset-x-2 bottom-12 z-20 max-h-44 overflow-y-auto rounded-xl border border-slate-200 bg-white p-2 shadow-xl"
        data-testid="feed-item-bookmark-menu"
        @click.stop
      >
        <div class="mb-2 flex items-center justify-between">
          <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
            Закладки
          </p>
          <button
            type="button"
            class="text-xs text-slate-400 hover:text-slate-900"
            data-testid="feed-item-bookmark-close"
            @click="menuOpen = false"
          >
            ✕
          </button>
        </div>
        <ul v-if="collections.items.length" class="flex flex-col gap-0.5">
          <li
            v-for="coll in collections.items"
            :key="coll.id"
          >
            <button
              type="button"
              class="flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-xs hover:bg-slate-100"
              :data-testid="`feed-item-bookmark-option-${coll.id}`"
              @click="onToggle(coll.id)"
            >
              <span
                class="inline-flex h-3.5 w-3.5 items-center justify-center rounded border"
                :class="
                  isMember(coll.id)
                    ? 'bg-indigo-600 border-indigo-600 text-white'
                    : 'border-slate-300 text-transparent'
                "
              >
                ✓
              </span>
              <span
                class="truncate"
                :class="isMember(coll.id) ? 'text-indigo-700 font-semibold' : 'text-slate-700'"
              >
                {{ coll.name }}
              </span>
            </button>
          </li>
        </ul>
        <p v-else class="text-xs text-slate-400">
          Создайте коллекцию в боковой панели.
        </p>
      </div>
    </div>
    <div class="p-2 sm:p-4 flex flex-col gap-1 bg-white relative z-10 flex-1 min-w-0 justify-start">
      <button
        type="button"
        class="text-left text-[13px] sm:text-[15px] font-bold text-slate-800 leading-snug tracking-tight line-clamp-2 hover:text-indigo-600 focus:outline-none focus:text-indigo-600 transition-colors"
        data-testid="feed-item-title"
        :title="titleLabel"
        @click="onOpen"
      >
        {{ titleLabel }}
      </button>
      <p
        v-if="yearLabel"
        class="text-xs text-slate-500"
        data-testid="feed-item-year"
      >
        {{ yearLabel }}
      </p>
    </div>
  </article>
</template>

<style scoped>
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
