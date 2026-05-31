<script setup lang="ts">
// ROADMAP Stage 10.7f — Item card modal (legacy index.html:140-1020).
// Follow-up after 10.7z user feedback — replaced the tabbed layout
// (overview / ids / releases / actions) with a flat "everything on
// one canvas" layout that mirrors the legacy modal:
//
//   ┌──────────────────────────────────────────────────────────┐
//   │ [🔄 Обновить] [🗑️ Сброс]    Title (year)            [✕] │
//   ├──────────────────────────────────────────────────────────┤
//   │ Poster │ KP/IMDb · category · ignored?                   │
//   │        │ Description                                      │
//   │        │ [RUTOR] [КП] [REZKA] [IMDB]                     │
//   │        │ [▶ Смотреть]  [🎬 Трейлер]                       │
//   │        │ Releases (collapsible)                           │
//   │        │ [✎ Редактировать ID …]                           │
//   └──────────────────────────────────────────────────────────┘
//
// ✕ in the top-right is *both* ignore-toggle and modal-close, so it
// matches the legacy "swipe-to-dismiss" mental model. The reset
// dialog and edit-IDs panel are inline sub-views toggled via the
// header buttons / footer link respectively. State lives in
// `useItemsStore` (see `stores/items.ts`); this component is
// presentational and stateless across opens.

import { computed, ref, watch } from 'vue'

import {
  RESETTABLE_FIELDS,
  useItemsStore,
  type ResettableField,
} from '../stores/items'
import { useItemPlayerStore } from '../stores/player'
import {
  useKinopubStore,
  type KinopubSearchResult,
} from '../stores/kinopub'
import { useCollectionsStore } from '../stores/collections'
import { useSyncStore } from '../stores/sync'
import { useToastStore } from '../stores/toast'
import PlayerModal from './PlayerModal.vue'

const items = useItemsStore()
const player = useItemPlayerStore()
const collections = useCollectionsStore()
const sync = useSyncStore()
const kinopub = useKinopubStore()
const toast = useToastStore()

/** True whenever the items-store has an item set. The store is
 *  populated by `items.open(id, seed)` (called from
 *  `FeedItemCard`) and torn down by `items.close()` here. */
const open = computed(() => items.isOpen)

const showResetDialog = ref(false)
const showEditIds = ref(false)

const hoveredRating = ref<number | null>(null)
async function onRate(val: number | null): Promise<void> {
  await items.rate(val)
}
async function onToggleWatched(): Promise<void> {
  const cur = items.item
  if (!cur) return
  await items.setWatched(!(cur.is_watched === 1))
}

const collectionsMenuOpen = ref(false)
const memberCollections = computed(() => {
  if (!items.item) return []
  return collections.collectionsForItem(items.item.id)
})
const isBookmarked = computed(() => memberCollections.value.length > 0)

async function onToggleCollection(collectionId: number): Promise<void> {
  if (!items.item) return
  await collections.toggleItem(items.item.id, collectionId)
}

function isCollectionMember(collectionId: number): boolean {
  return memberCollections.value.includes(collectionId)
}

// Edit-IDs draft state. Re-seeded from store whenever the open item
// changes so navigating between items doesn't carry over half-edited
// values.
const draftKp = ref('')
const draftImdb = ref('')
const draftRezka = ref('')
const useFullRebind = ref(false)
const resetSelection = ref<Record<ResettableField, boolean>>(
  Object.fromEntries(
    RESETTABLE_FIELDS.map((f) => [f, false]),
  ) as Record<ResettableField, boolean>,
)

function seedDraftsFromItem(): void {
  const it = items.item
  draftKp.value = it?.kp_id ?? ''
  draftImdb.value = it?.imdb_id ?? ''
  draftRezka.value = it?.rezka_url ?? ''
  useFullRebind.value = false
  for (const field of RESETTABLE_FIELDS) {
    resetSelection.value[field] = false
  }
}

// Re-seed and collapse sub-dialogs on every new item. The reset
// dialog gets re-opened automatically when the feed-card hover
// shortcut sets `pendingResetDialog` before calling `open()` — we
// consume that flag here so the watcher stays the single source of
// truth for `showResetDialog` across the open lifetime.
watch(
  () => items.item?.id ?? null,
  (next, prev) => {
    if (next !== prev) {
      const honourPending = next !== null && items.pendingResetDialog
      showResetDialog.value = honourPending
      if (honourPending) items.pendingResetDialog = false
      showEditIds.value = false
      collectionsMenuOpen.value = false
      seedDraftsFromItem()
    }
  },
  { immediate: true },
)

const ratingKp = computed(() => items.item?.kp_rating ?? null)
const ratingImdb = computed(() => items.item?.imdb_rating ?? null)
const singleUpdateRunning = computed(
  () => sync.statuses.single_update === 'running',
)

// External-source links. Mirrors the legacy `index.html` chips:
//   RUTOR — latest direct release link when stored, otherwise title search,
//   КП    — kinopoisk.ru film page when `kp_id` is set,
//   REZKA — direct rezka_url passthrough,
//   IMDB  — imdb.com title page when `imdb_id` is set.
const rutorUrl = computed(() => {
  const it = items.item
  if (!it) return null
  const releases = [...items.releases].sort((a, b) => {
    return (b.date_added || '').localeCompare(a.date_added || '')
  })
  const latestWithUrl = releases.find((r) => r.link)
  if (latestWithUrl?.link) return latestWithUrl.link

  if (!it.title) return null
  const query = it.year ? `${it.title} ${it.year}` : it.title
  return `https://rutor.info/search/0/0/000/0/${encodeURIComponent(query)}`
})
const kpUrl = computed(() => {
  const id = items.item?.kp_id?.trim()
  return id ? `https://www.kinopoisk.ru/film/${encodeURIComponent(id)}/` : null
})
const rezkaUrl = computed(() => items.item?.rezka_url?.trim() || null)
const imdbUrl = computed(() => {
  const id = items.item?.imdb_id?.trim()
  return id ? `https://www.imdb.com/title/${encodeURIComponent(id)}/` : null
})

// PR 3 of the kino.pub stack — show an "Open on kino.pub" chip
// alongside RUTOR/КП/REZKA/IMDB once the row is bound. We prefer the
// cached `kinopub_url` (set by /bind) and fall back to the canonical
// "/item/<id>" shape if only the id is set (e.g. older rows from
// the sync_kinopub matcher that didn't capture the URL yet).
const kinopubUrl = computed(() => {
  const it = items.item
  if (!it) return null
  if (it.kinopub_url && it.kinopub_url.trim().length > 0) {
    return it.kinopub_url
  }
  if (it.kinopub_id) {
    return `https://kino.pub/item/view/${it.kinopub_id}`
  }
  return null
})

// PR 6 — Android intent:// deep-link. The official kino.pub app is
// shipped as `com.kinopub` (verified via 4PDA / Appteka). We use the
// standard Chrome intent URI with `S.browser_fallback_url` so on
// browsers that don't honour `intent://` (desktop Chrome, Firefox,
// Safari) clicking the link still lands on `https://kino.pub/item/…`
// instead of an error page.
//
// Spec: https://developer.chrome.com/docs/multidevice/android/intents
const kinopubAndroidIntentUrl = computed(() => {
  const fallback = kinopubUrl.value
  if (!fallback) return null
  const it = items.item
  if (!it?.kinopub_id) return null
  // `intent://` requires the path/authority of the data URI. We
  // mirror the canonical `kino.pub/item/<id>` so when the Android
  // app's intent-filter matches `https://kino.pub/...` it picks it
  // up; pass `scheme=https` to keep the data URI in step with the
  // app's manifest filter rather than relying on a custom scheme.
  const path = `kino.pub/item/${encodeURIComponent(String(it.kinopub_id))}`
  const encodedFallback = encodeURIComponent(fallback)
  return (
    `intent://${path}` +
    `#Intent;scheme=https;package=com.kinopub;` +
    `S.browser_fallback_url=${encodedFallback};end`
  )
})

// Edit-IDs panel — kino.pub bind state.
const draftKinopub = ref('')
const kinopubSearchQuery = ref('')
const kinopubSearchOpen = ref(false)

function seedKinopubDraft(): void {
  const id = items.item?.kinopub_id ?? null
  draftKinopub.value = id ? String(id) : ''
  kinopubSearchQuery.value = items.item?.title ?? ''
  kinopubSearchOpen.value = false
  kinopub.clearSearch()
}

watch(
  () => items.item?.id ?? null,
  () => {
    seedKinopubDraft()
  },
)

async function onKinopubBindManual(): Promise<void> {
  const cur = items.item
  if (!cur) return
  const id = Number(draftKinopub.value.trim())
  if (!Number.isFinite(id) || id <= 0) {
    toast.error('kino.pub ID должен быть положительным числом')
    return
  }
  const ok = await kinopub.bind(cur.id, { kinopub_id: id })
  if (ok) {
    toast.success(`Привязано к kino.pub #${id}`)
    // The items store doesn't know about kinopub_*; reload the
    // detail payload so the badge re-renders.
    await items.open(cur.id, cur)
    seedKinopubDraft()
  } else {
    toast.error(kinopub.bindError || 'Не удалось привязать')
  }
}

async function onKinopubUnbind(): Promise<void> {
  const cur = items.item
  if (!cur) return
  const ok = await kinopub.unbind(cur.id)
  if (ok) {
    toast.success('Отвязано от kino.pub')
    await items.open(cur.id, cur)
    seedKinopubDraft()
  } else {
    toast.error(kinopub.bindError || 'Не удалось отвязать')
  }
}

async function onKinopubSearch(): Promise<void> {
  const cur = items.item
  if (!cur) return
  const q = kinopubSearchQuery.value.trim()
  if (q.length === 0) return
  kinopubSearchOpen.value = true
  await kinopub.search(q, {
    year: cur.year ?? undefined,
    kp_id: cur.kp_id,
    imdb_id: cur.imdb_id,
  })
}

async function onKinopubPickResult(r: KinopubSearchResult): Promise<void> {
  const cur = items.item
  if (!cur) return
  const ok = await kinopub.bind(cur.id, {
    kinopub_id: r.id,
    kinopub_type: r.type,
    kinopub_url: r.url,
  })
  if (ok) {
    toast.success(`Привязано: ${r.title ?? r.id}`)
    kinopubSearchOpen.value = false
    kinopub.clearSearch()
    await items.open(cur.id, cur)
    seedKinopubDraft()
  } else {
    toast.error(kinopub.bindError || 'Не удалось привязать')
  }
}

function dirtyIds(): { kp_id?: string | null; imdb_id?: string | null } {
  const out: { kp_id?: string | null; imdb_id?: string | null } = {}
  const cur = items.item
  if (!cur) return out
  const trimmedKp = draftKp.value.trim()
  const trimmedImdb = draftImdb.value.trim()
  if (trimmedKp !== (cur.kp_id ?? '')) {
    out.kp_id = trimmedKp.length > 0 ? trimmedKp : null
  }
  if (trimmedImdb !== (cur.imdb_id ?? '')) {
    out.imdb_id = trimmedImdb.length > 0 ? trimmedImdb : null
  }
  return out
}

async function onSaveIds(): Promise<void> {
  const payload = dirtyIds()
  if (Object.keys(payload).length === 0) return
  await items.saveIds(payload)
  seedDraftsFromItem()
}

async function onRebind(): Promise<void> {
  const cur = items.item
  if (!cur) return
  const payload: {
    kp_id?: string | null
    imdb_id?: string | null
    rezka_url?: string | null
  } = {}
  const trimmedKp = draftKp.value.trim()
  const trimmedImdb = draftImdb.value.trim()
  const trimmedRezka = draftRezka.value.trim()
  if (trimmedKp !== (cur.kp_id ?? '')) {
    payload.kp_id = trimmedKp.length > 0 ? trimmedKp : null
  }
  if (trimmedImdb !== (cur.imdb_id ?? '')) {
    payload.imdb_id = trimmedImdb.length > 0 ? trimmedImdb : null
  }
  if (trimmedRezka !== (cur.rezka_url ?? '')) {
    payload.rezka_url = trimmedRezka.length > 0 ? trimmedRezka : null
  }
  if (Object.keys(payload).length === 0) return
  await items.rebind(payload)
  seedDraftsFromItem()
}

async function onResetSelected(): Promise<void> {
  const selected = RESETTABLE_FIELDS.filter(
    (f) => resetSelection.value[f],
  ) as ResettableField[]
  console.log('[DEBUG] Selected fields for reset:', selected)
  if (selected.length === 0) return
  const ok = await items.resetFields(selected)
  if (ok) {
    showResetDialog.value = false
    seedDraftsFromItem()
  }
}

async function onToggleIgnore(): Promise<void> {
  await items.toggleIgnore()
}

async function onReprocess(): Promise<void> {
  await items.reprocess()
}

// ── ✕ closes the modal AND flips the ignore flag so the item drops
// out of the feed. Matches the legacy "swipe-to-hide" gesture.
async function onIgnoreAndClose(): Promise<void> {
  // 10.7h — user feedback: ✕ should just close the modal, not toggle ignore.
  // The "one-click ignore" is on the feed card. In the modal,
  // explicit actions are better.
  items.close()
}

// ── 10.7g — open trailer / stream player on top of this modal ───
function onOpenTrailer(): void {
  const it = items.item
  if (!it) return
  player.openTrailer(it.id, it.title)
}

function onOpenStream(): void {
  const it = items.item
  if (!it) return
  player.openStream(it.id, it.title)
}

// PR 5 — open the kino.pub stream surface. Disabled until kino.pub
// auth is healthy AND the row is bound — we don't want users to land
// on a 401/409 from the modal.
function onOpenKinopubStream(): void {
  const it = items.item
  if (!it) return
  player.openKinopubStream(it.id, it.title)
}

const canOpenStream = computed(() => {
  const it = items.item
  if (!it) return false
  return Boolean(it.rezka_url || it.kp_id || it.imdb_id)
})

const canOpenKinopubStream = computed(() => {
  const it = items.item
  if (!it) return false
  // Require both binding and a healthy auth session. The store keeps
  // `isAuthenticated` truthy after a successful /v1/user fetch.
  return Boolean(it.kinopub_id) && kinopub.isAuthenticated
})

function formatReleaseDate(input: string | null | undefined): string {
  if (!input) return '—'
  const d = new Date(input)
  if (Number.isNaN(d.getTime())) return input
  return d.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function onClose(): void {
  items.close()
}

function onOpenResetDialog(): void {
  // Re-seed selection on each open so old ticks don't bleed through.
  for (const field of RESETTABLE_FIELDS) {
    resetSelection.value[field] = false
  }
  showResetDialog.value = true
}

function onCloseResetDialog(): void {
  showResetDialog.value = false
}

function onToggleEditIds(): void {
  if (!showEditIds.value) seedDraftsFromItem()
  showEditIds.value = !showEditIds.value
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
    data-testid="item-modal-backdrop"
    @click.self="onClose"
  >
    <div
      class="flex max-h-[90vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
      data-testid="item-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="item-modal-title"
    >
      <!-- ── Header: two action buttons (left) · title · ✕ (right) ── -->
      <header
        class="flex items-center gap-3 border-b border-slate-200 px-5 py-3"
        data-testid="item-modal-header"
      >
        <div class="flex flex-shrink-0 items-center gap-2">
          <!-- Collections dropdown menu -->
          <div class="relative">
            <button
              type="button"
              class="rounded-md px-2.5 py-1.5 text-xs font-medium border flex items-center gap-1.5 transition-colors"
              :class="isBookmarked ? 'bg-indigo-600 text-white border-indigo-600 hover:bg-indigo-700' : 'bg-slate-100 text-slate-700 border-slate-300 hover:bg-slate-200'"
              @click="collectionsMenuOpen = !collectionsMenuOpen"
            >
              <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
              </svg>
              {{ memberCollections.length }}
            </button>
            <div
              v-if="collectionsMenuOpen"
              class="absolute left-0 top-full mt-1 w-56 z-50 max-h-56 overflow-y-auto rounded-xl border border-slate-200 bg-white p-2 shadow-xl"
            >
              <div class="mb-2 flex items-center justify-between">
                <p class="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                  Закладки
                </p>
                <button
                  type="button"
                  class="text-xs text-slate-400 hover:text-slate-900"
                  @click="collectionsMenuOpen = false"
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
                    class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs hover:bg-slate-100 transition-colors"
                    @click="onToggleCollection(coll.id)"
                  >
                    <span
                      class="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border"
                      :class="isCollectionMember(coll.id) ? 'bg-indigo-600 border-indigo-600 text-white' : 'border-slate-300 text-transparent'"
                    >
                      ✓
                    </span>
                    <span class="truncate" :class="isCollectionMember(coll.id) ? 'text-indigo-700 font-semibold' : 'text-slate-700'">
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

          <button
            type="button"
            class="rounded-md bg-indigo-600 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            :disabled="items.loading || singleUpdateRunning"
            data-testid="item-modal-reprocess"
            :title="singleUpdateRunning ? 'Уже идёт перепроверка…' : 'Обновить метаданные'"
            @click="onReprocess"
          >
            <span v-if="singleUpdateRunning">🔄 …</span>
            <span v-else>🔄 Обновить</span>
          </button>
          <button
            type="button"
            class="rounded-md bg-rose-100 px-2.5 py-1.5 text-xs font-medium text-rose-800 hover:bg-rose-200 disabled:opacity-50"
            :disabled="items.loading"
            data-testid="item-modal-open-reset"
            title="Сброс данных (с галочками)"
            @click="onOpenResetDialog"
          >
            🗑️ Сброс
          </button>
        </div>
        <h2
          id="item-modal-title"
          class="flex-1 truncate text-center text-base font-semibold text-slate-900 sm:text-left"
          data-testid="item-modal-title"
        >
          {{ items.item?.title ?? '—' }}
          <span v-if="items.item?.year" class="font-normal text-slate-500">
            ({{ items.item.year }})
          </span>
        </h2>
        <button
          type="button"
          class="rounded-md p-1 text-slate-500 hover:bg-rose-100 hover:text-rose-700"
          :aria-label="items.isIgnored ? 'Восстановить и закрыть' : 'Скрыть и закрыть'"
          data-testid="item-modal-close"
          :title="items.isIgnored ? 'Уже скрыто — нажмите чтобы вернуть и закрыть' : 'Скрыть и закрыть'"
          @click="onIgnoreAndClose"
        >
          ✕
        </button>
      </header>

      <div class="flex-1 space-y-4 overflow-y-auto p-5 text-sm text-slate-700">
        <p
          v-if="items.loading"
          class="text-xs text-slate-500"
          data-testid="item-modal-loading"
        >
          Загрузка…
        </p>
        <p
          v-if="items.error"
          class="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800"
          data-testid="item-modal-error"
        >
          {{ items.error }}
        </p>
        <p
          v-if="items.actionError"
          class="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
          data-testid="item-modal-action-error"
        >
          {{ items.actionError }}
        </p>

        <section
          v-if="items.item"
          data-testid="item-modal-overview"
          class="flex flex-col gap-4 sm:flex-row"
        >
          <img
            v-if="items.item.poster_url"
            :src="items.item.poster_url"
            alt=""
            class="mx-auto h-48 w-32 flex-shrink-0 rounded object-cover sm:mx-0"
            data-testid="item-modal-poster"
          />
          <div class="flex-1 space-y-3">
            <p
              v-if="items.item.original_title && items.item.original_title !== items.item.title"
              class="text-xs italic text-slate-500"
              data-testid="item-modal-original-title"
            >
              {{ items.item.original_title }}
            </p>
            <div class="flex flex-wrap gap-2 text-[11px]">
              <span
                v-if="ratingKp !== null"
                class="rounded bg-orange-100 px-2 py-0.5 text-orange-700"
                data-testid="item-modal-kp"
              >
                KP {{ ratingKp }}
              </span>
              <span
                v-if="ratingImdb !== null"
                class="rounded bg-yellow-100 px-2 py-0.5 text-yellow-800"
                data-testid="item-modal-imdb"
              >
                IMDb {{ ratingImdb }}
              </span>
              <span
                v-if="items.isIgnored"
                class="rounded bg-slate-200 px-2 py-0.5 text-slate-700"
                data-testid="item-modal-ignored"
              >
                Скрыто
              </span>
            </div>

            <!-- User Rating Star selector -->
            <div class="flex items-center gap-1.5 py-1 text-xs" data-testid="item-modal-user-rating">
              <span class="font-medium text-slate-500">Ваша оценка:</span>
              <div class="flex items-center gap-0.5">
                <button
                  v-for="star in 10"
                  :key="star"
                  type="button"
                  class="text-base transition-all duration-150 focus:outline-none leading-none select-none"
                  :class="star <= (hoveredRating || items.item?.user_rating || 0) ? 'text-amber-400 scale-110' : 'text-slate-300 hover:text-amber-200'"
                  :title="`Оценить на ${star}/10`"
                  @mouseenter="hoveredRating = star"
                  @mouseleave="hoveredRating = null"
                  @click="onRate(star)"
                >
                  ★
                </button>
              </div>
              <button
                v-if="items.item?.user_rating"
                type="button"
                class="ml-1 text-[10px] text-red-500 hover:underline"
                @click="onRate(null)"
              >
                удалить
              </button>
            </div>

            <p
              v-if="items.item.description"
              class="whitespace-pre-line text-sm text-slate-600"
              data-testid="item-modal-description"
            >
              {{ items.item.description }}
            </p>
            <p
              v-else
              class="text-xs italic text-slate-400"
              data-testid="item-modal-no-description"
            >
              Описание не задано.
            </p>

            <!-- External-source link chips: RUTOR · КП · REZKA · IMDB -->
            <div
              class="flex flex-wrap gap-2"
              data-testid="item-modal-external-links"
            >
              <a
                v-if="rutorUrl"
                :href="rutorUrl"
                target="_blank"
                rel="noopener"
                class="rounded-md bg-slate-900 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-slate-700"
                data-testid="item-modal-link-rutor"
              >
                RUTOR
              </a>
              <a
                v-if="kpUrl"
                :href="kpUrl"
                target="_blank"
                rel="noopener"
                class="rounded-md bg-orange-600 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-orange-700"
                data-testid="item-modal-link-kp"
              >
                КП
              </a>
              <a
                v-if="rezkaUrl"
                :href="rezkaUrl"
                target="_blank"
                rel="noopener"
                class="rounded-md bg-emerald-700 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-emerald-800"
                data-testid="item-modal-link-rezka"
              >
                REZKA
              </a>
              <a
                v-if="imdbUrl"
                :href="imdbUrl"
                target="_blank"
                rel="noopener"
                class="rounded-md bg-yellow-500 px-2.5 py-1 text-[11px] font-medium text-slate-900 hover:bg-yellow-400"
                data-testid="item-modal-link-imdb"
              >
                IMDB
              </a>
              <a
                v-if="kinopubUrl"
                :href="kinopubUrl"
                target="_blank"
                rel="noopener"
                class="rounded-md bg-fuchsia-600 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-fuchsia-700"
                data-testid="item-modal-link-kinopub"
                :title="`kino.pub id: ${items.item?.kinopub_id ?? ''}`"
              >
                KINO.PUB
              </a>
            </div>

            <!-- 10.7g — primary playback actions, always visible -->
            <div
              class="grid grid-cols-2 sm:grid-cols-4 gap-2"
              data-testid="item-modal-play-actions"
            >
              <button
                type="button"
                class="rounded-md bg-emerald-600 px-3 py-2 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                :disabled="!canOpenStream"
                data-testid="item-modal-watch"
                @click="onOpenStream"
              >
                ▶ Смотреть
              </button>
              <button
                type="button"
                class="rounded-md bg-rose-600 px-3 py-2 text-xs font-medium text-white hover:bg-rose-700 disabled:opacity-50"
                :disabled="!items.item"
                data-testid="item-modal-trailer"
                @click="onOpenTrailer"
              >
                🎬 Трейлер
              </button>
              <button
                type="button"
                class="rounded-md px-3 py-2 text-xs font-medium border transition-colors"
                :class="items.item?.is_watched === 1
                  ? 'bg-emerald-50 text-emerald-700 border-emerald-300 hover:bg-emerald-100'
                  : 'bg-slate-100 text-slate-700 border-slate-300 hover:bg-slate-200'"
                data-testid="item-modal-watched-toggle"
                @click="onToggleWatched"
              >
                {{ items.item?.is_watched === 1 ? '✓ Просмотрено' : '👁 Просмотрено' }}
              </button>
              <button
                type="button"
                class="rounded-md px-3 py-2 text-xs font-medium border transition-colors"
                :class="items.item?.is_ignored === 1
                  ? 'bg-red-50 text-red-700 border-red-300 hover:bg-red-100'
                  : 'bg-slate-100 text-slate-700 border-slate-300 hover:bg-slate-200'"
                data-testid="item-modal-ignore-toggle"
                @click="onToggleIgnore"
              >
                {{ items.item?.is_ignored === 1 ? '↩ Вернуть' : '✕ В корзину' }}
              </button>
            </div>
            <p v-if="items.item?.is_watched === 1 && items.item?.watched_at" class="text-[10px] text-slate-400 italic mt-1">
              Просмотрено: {{ formatReleaseDate(items.item.watched_at) }}
            </p>
            <!-- PR 5: kino.pub playback. Only shown when the row is
                 bound + auth is healthy, so the button doesn't sit
                 there permanently disabled for non-kino.pub users. -->
            <button
              v-if="canOpenKinopubStream"
              type="button"
              class="rounded-md bg-fuchsia-600 px-3 py-2 text-xs font-medium text-white hover:bg-fuchsia-700"
              data-testid="item-modal-watch-kinopub"
              @click="onOpenKinopubStream"
            >
              ▶ Смотреть на kino.pub
            </button>
            <!-- PR 6: Android intent:// deep-link. Visible whenever
                 the row is bound — no auth check, the app handles
                 its own login. Falls back to the browser kino.pub
                 URL on non-Android browsers. -->
            <a
              v-if="kinopubAndroidIntentUrl"
              :href="kinopubAndroidIntentUrl"
              rel="noopener"
              class="block rounded-md bg-emerald-700 px-3 py-2 text-center text-xs font-medium text-white hover:bg-emerald-800"
              data-testid="item-modal-watch-kinopub-android"
            >
              📱 Открыть в Android-приложении kino.pub
            </a>
          </div>
        </section>

        <section
          v-if="items.releases.length > 0"
          data-testid="item-modal-releases"
          class="rounded-md border border-slate-200 p-3"
        >
          <p class="text-xs font-medium uppercase tracking-wide text-slate-500">
            📅 Релизов: {{ items.releases.length }}
          </p>
          <ul class="mt-2 divide-y divide-slate-200 text-xs">
            <li
              v-for="rel in items.releases"
              :key="rel.id"
              class="flex items-start gap-2 py-2"
            >
              <span class="flex-shrink-0 text-slate-400">
                {{ formatReleaseDate(rel.date_added) }}
              </span>
              <span class="flex-1 truncate text-slate-700">
                {{ rel.title ?? '—' }}
                <span v-if="rel.quality" class="ml-1 text-slate-400">
                  · {{ rel.quality }}
                </span>
                <span v-if="rel.size" class="ml-1 text-slate-400">
                  · {{ rel.size }}
                </span>
              </span>
            </li>
          </ul>
        </section>

        <!-- ── Footer: collapsible Edit-IDs panel ─────────────────── -->
        <section class="flex justify-end" data-testid="item-modal-edit-ids-row">
          <button
            type="button"
            class="text-xs font-medium text-indigo-700 hover:text-indigo-900"
            data-testid="item-modal-toggle-edit-ids"
            @click="onToggleEditIds"
          >
            {{ showEditIds ? '× Скрыть редактор ID' : '✎ Редактировать ID' }}
          </button>
        </section>

        <section
          v-if="showEditIds"
          data-testid="item-modal-ids"
          class="space-y-3 rounded-md border border-indigo-200 bg-indigo-50/40 p-3"
        >
          <label class="block">
            <span class="block text-xs font-medium text-slate-500">KP ID</span>
            <input
              v-model="draftKp"
              type="text"
              class="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-indigo-500 focus:outline-none"
              data-testid="item-modal-input-kp"
            />
          </label>
          <label class="block">
            <span class="block text-xs font-medium text-slate-500">IMDb ID</span>
            <input
              v-model="draftImdb"
              type="text"
              class="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-indigo-500 focus:outline-none"
              data-testid="item-modal-input-imdb"
            />
          </label>
          <label class="block">
            <span class="block text-xs font-medium text-slate-500">Rezka URL</span>
            <input
              v-model="draftRezka"
              type="text"
              class="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-indigo-500 focus:outline-none"
              data-testid="item-modal-input-rezka"
            />
          </label>

          <!-- kino.pub bind sub-panel (PR 3) ───────────────────────── -->
          <div
            class="space-y-2 rounded-md border border-fuchsia-200 bg-fuchsia-50/40 p-2"
            data-testid="item-modal-kinopub-panel"
          >
            <p class="text-xs font-medium uppercase tracking-wide text-fuchsia-700">
              kino.pub
              <span
                v-if="items.item?.kinopub_id"
                class="ml-1 rounded bg-fuchsia-200 px-1.5 py-0.5 text-[10px] text-fuchsia-900"
                data-testid="item-modal-kinopub-current-id"
              >
                привязано: #{{ items.item.kinopub_id }}
              </span>
              <span
                v-else
                class="ml-1 text-[10px] text-slate-500"
                data-testid="item-modal-kinopub-current-none"
              >
                не привязано
              </span>
            </p>
            <label class="block">
              <span class="block text-xs text-slate-500">ID на kino.pub</span>
              <div class="mt-1 flex gap-1">
                <input
                  v-model="draftKinopub"
                  type="number"
                  min="1"
                  class="flex-1 rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-fuchsia-500 focus:outline-none"
                  data-testid="item-modal-kinopub-input"
                />
                <button
                  type="button"
                  class="rounded-md bg-fuchsia-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-fuchsia-700 disabled:opacity-50"
                  :disabled="kinopub.bindBusy"
                  data-testid="item-modal-kinopub-bind"
                  @click="onKinopubBindManual"
                >
                  Привязать
                </button>
                <button
                  v-if="items.item?.kinopub_id"
                  type="button"
                  class="rounded-md bg-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-300 disabled:opacity-50"
                  :disabled="kinopub.bindBusy"
                  data-testid="item-modal-kinopub-unbind"
                  @click="onKinopubUnbind"
                >
                  Отвязать
                </button>
              </div>
            </label>

            <div class="space-y-1">
              <label class="block">
                <span class="block text-xs text-slate-500">Поиск по kino.pub</span>
                <div class="mt-1 flex gap-1">
                  <input
                    v-model="kinopubSearchQuery"
                    type="text"
                    class="flex-1 rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-fuchsia-500 focus:outline-none"
                    data-testid="item-modal-kinopub-search-input"
                    @keydown.enter.prevent="onKinopubSearch"
                  />
                  <button
                    type="button"
                    class="rounded-md bg-fuchsia-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-fuchsia-600 disabled:opacity-50"
                    :disabled="kinopub.searchBusy"
                    data-testid="item-modal-kinopub-search"
                    @click="onKinopubSearch"
                  >
                    Найти
                  </button>
                </div>
              </label>

              <p
                v-if="kinopub.searchError"
                class="text-[11px] text-rose-600"
                data-testid="item-modal-kinopub-search-error"
              >
                {{ kinopub.searchError }}
              </p>

              <ul
                v-if="kinopubSearchOpen && kinopub.searchResults.length > 0"
                class="max-h-48 divide-y divide-slate-200 overflow-y-auto rounded border border-slate-200 bg-white"
                data-testid="item-modal-kinopub-search-results"
              >
                <li
                  v-for="r in kinopub.searchResults"
                  :key="r.id"
                  class="flex items-center justify-between gap-2 px-2 py-1.5 text-xs hover:bg-fuchsia-50"
                >
                  <span class="flex-1 truncate text-slate-700">
                    <span class="font-medium">{{ r.title ?? '—' }}</span>
                    <span v-if="r.year" class="ml-1 text-slate-400">
                      ({{ r.year }})
                    </span>
                    <span v-if="r.type" class="ml-1 text-[10px] uppercase text-slate-400">
                      {{ r.type }}
                    </span>
                  </span>
                  <button
                    type="button"
                    class="rounded bg-fuchsia-600 px-2 py-0.5 text-[10px] font-medium text-white hover:bg-fuchsia-700"
                    data-testid="item-modal-kinopub-search-pick"
                    @click="onKinopubPickResult(r)"
                  >
                    Привязать
                  </button>
                </li>
              </ul>
              <p
                v-else-if="
                  kinopubSearchOpen &&
                  !kinopub.searchBusy &&
                  kinopub.searchResults.length === 0 &&
                  !kinopub.searchError
                "
                class="text-[11px] italic text-slate-400"
                data-testid="item-modal-kinopub-search-empty"
              >
                Ничего не найдено.
              </p>
            </div>
          </div>

          <label class="flex items-center gap-2 text-xs text-slate-600">
            <input
              v-model="useFullRebind"
              type="checkbox"
              data-testid="item-modal-rebind-checkbox"
            />
            Перепривязка (`rebind`) — сбросит флаги перепроверки.
          </label>
          <div class="flex flex-wrap gap-2 pt-1">
            <button
              v-if="!useFullRebind"
              type="button"
              class="rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              :disabled="items.loading"
              data-testid="item-modal-save-ids"
              @click="onSaveIds"
            >
              Сохранить ID
            </button>
            <button
              v-else
              type="button"
              class="rounded-md bg-rose-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-700 disabled:opacity-50"
              :disabled="items.loading"
              data-testid="item-modal-rebind"
              @click="onRebind"
            >
              Перепривязать
            </button>
          </div>
        </section>

        <!-- Restore-from-ignored shortcut — only useful while still
             on the card; otherwise the user just clicks "✕" to flip
             both ways. -->
        <section
          v-if="items.isIgnored"
          class="flex justify-end"
        >
          <button
            type="button"
            class="rounded-md bg-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-300 disabled:opacity-50"
            :disabled="items.loading"
            data-testid="item-modal-toggle-ignore"
            @click="onToggleIgnore"
          >
            Восстановить
          </button>
        </section>
      </div>
    </div>

    <!-- ── Reset dialog: sub-modal launched from the header ─────── -->
    <div
      v-if="showResetDialog"
      class="fixed inset-0 z-[55] flex items-center justify-center bg-slate-900/40 p-4"
      data-testid="item-modal-reset-dialog-backdrop"
      @click.self="onCloseResetDialog"
    >
      <div
        class="w-full max-w-md rounded-2xl bg-white p-5 shadow-2xl"
        data-testid="item-modal-reset-dialog"
      >
        <div class="flex items-center justify-between">
          <h3 class="text-sm font-semibold text-slate-900">🗑️ Сброс данных</h3>
          <button
            type="button"
            class="rounded-md p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-700"
            aria-label="Закрыть"
            data-testid="item-modal-reset-dialog-close"
            @click="onCloseResetDialog"
          >
            ×
          </button>
        </div>
        <p class="mt-1 text-[11px] text-slate-500">
          Выберите поля, значения которых нужно очистить.
        </p>
        <div class="mt-3 grid grid-cols-2 gap-1 text-xs">
          <label
            v-for="f in RESETTABLE_FIELDS"
            :key="f"
            class="flex items-center gap-2 text-slate-600"
          >
            <input
              v-model="resetSelection[f]"
              type="checkbox"
              :data-testid="`item-modal-reset-${f}`"
            />
            {{
              (
                {
                  poster_url: 'Постер',
                  description: 'Описание',
                  kp_id: 'Кинопоиск ID',
                  imdb_id: 'IMDb ID',
                  rezka_url: 'Ссылка Rezka',
                  kp_rating: 'Рейтинг КП',
                  imdb_rating: 'Рейтинг IMDb',
                  kinopub_id: 'ID Kino.pub',
                } as Record<string, string>
              )[f] || f
            }}
          </label>
        </div>
        <div class="mt-4 flex justify-end gap-2">
          <button
            type="button"
            class="rounded-md px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100"
            data-testid="item-modal-reset-dialog-cancel"
            @click="onCloseResetDialog"
          >
            Отмена
          </button>
          <button
            type="button"
            class="rounded-md bg-rose-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-700 disabled:opacity-50"
            :disabled="items.loading"
            data-testid="item-modal-reset-fields"
            @click="onResetSelected"
          >
            Сбросить выбранные
          </button>
        </div>
      </div>
    </div>

    <!--
      10.7g — modal-in-modal: the PlayerModal renders OUTSIDE the
      item-card scroll container but inside the same v-if gate, so
      it stays on top (z-[60]) and inherits the open lifetime of the
      card. Closing the card via items.close() will also unmount the
      PlayerModal because the parent v-if drops.
    -->
    <PlayerModal />
  </div>
</template>
