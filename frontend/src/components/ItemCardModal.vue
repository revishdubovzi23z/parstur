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
import { useSyncStore } from '../stores/sync'
import PlayerModal from './PlayerModal.vue'

const items = useItemsStore()
const player = useItemPlayerStore()
const sync = useSyncStore()

/** True whenever the items-store has an item set. The store is
 *  populated by `items.open(id, seed)` (called from
 *  `FeedItemCard`) and torn down by `items.close()` here. */
const open = computed(() => items.isOpen)

const showResetDialog = ref(false)
const showEditIds = ref(false)

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
//   RUTOR — search-by-title link (we don't store a per-item rutor URL),
//   КП    — kinopoisk.ru film page when `kp_id` is set,
//   REZKA — direct rezka_url passthrough,
//   IMDB  — imdb.com title page when `imdb_id` is set.
const rutorUrl = computed(() => {
  const it = items.item
  if (!it) return null
  // Try to find the latest release with a URL (most likely a rutor link)
  const releases = [...(it.releases || [])].sort((a, b) => {
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

const canOpenStream = computed(() => {
  const it = items.item
  if (!it) return false
  return Boolean(it.rezka_url || it.kp_id || it.imdb_id)
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
            </div>

            <!-- 10.7g — primary playback actions, always visible -->
            <div
              class="grid grid-cols-2 gap-2"
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
            </div>
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
