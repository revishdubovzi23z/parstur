<script setup lang="ts">
// ROADMAP Stage 10.5 — categories + collections sidebar.
//
// Mirrors the legacy sidebar from `index.html` (~lines 152-320): a
// category list at the top, a collections list below with inline
// create / rename / delete buttons. Selecting a category or
// collection patches the feed filters and triggers a refetch.

import { computed, ref, watch } from 'vue'
import { useCategoriesStore } from '../../stores/categories'
import { useCollectionsStore } from '../../stores/collections'
import { useFeedStore } from '../../stores/feed'
import { useSyncStore } from '../../stores/sync'
import { useVisitStore } from '../../stores/visits'

const categories = useCategoriesStore()
const collections = useCollectionsStore()
const feed = useFeedStore()
const sync = useSyncStore()
const visits = useVisitStore()

// "Lazy collections": a fresh DB no longer ships with 11 seeded
// rows, so we offer the user a one-click HDRezka pull right where
// the empty list lives. The sync store exposes a `statuses` map
// keyed by process_key — reading `rezka_collections` mirrors the
// running-flag the existing SyncPanel already binds to.
const collectionsSyncing = computed(
  () => sync.statuses?.rezka_collections === 'running',
)

async function syncCollectionsNow(): Promise<void> {
  // The store handles the POST + toast + status polling. We just
  // refresh the local collections list afterwards so the banner
  // disappears as soon as the sync produced rows. Polling will
  // also pull the new rows in via the websocket, but refreshing
  // explicitly keeps the UX snappy when the user is staring at
  // the sidebar waiting for it.
  const ok = await sync.startRezkaCollections()
  if (ok) await collections.refresh()
}

async function toggleNewOnly(): Promise<void> {
  await visits.toggleNewOnly()
  feed.setPage(1)
  await feed.fetchFeed()
}

const newCollectionName = ref('')
const showAddForm = ref(false)
const collectionsExpanded = ref(
  localStorage.getItem('par2_sidebar_collections_expanded') !== 'false',
)

watch(collectionsExpanded, (val) => {
  localStorage.setItem('par2_sidebar_collections_expanded', String(val))
})
const submitting = ref(false)

const selectedCategoryId = computed(() => feed.filters.categoryId)
const selectedCollectionId = computed(() => collections.selectedId)
const selectedCategoryValue = computed({
  get: () => String(selectedCategoryId.value),
  set: (value: string) => {
    void selectCategory(Number(value))
  },
})

const dropdownCategories = computed(() =>
  categories.items.filter((cat) => cat.id !== -1)
)

async function selectCategory(id: number): Promise<void> {
  collections.select(null)
  feed.setFilters({ categoryId: id, collectionId: null })
  await feed.fetchFeed()
}

async function selectCollection(id: number): Promise<void> {
  collections.select(id)
  feed.setFilters({ collectionId: id })
  await feed.fetchFeed()
}

async function submitNewCollection(): Promise<void> {
  if (!newCollectionName.value.trim()) return
  submitting.value = true
  const ok = await collections.createCollection(newCollectionName.value)
  submitting.value = false
  if (ok) {
    newCollectionName.value = ''
    showAddForm.value = false
  }
}

function toggleAddForm(): void {
  collectionsExpanded.value = true
  showAddForm.value = !showAddForm.value
}

async function onDelete(id: number): Promise<void> {
  if (!window.confirm('Удалить эту коллекцию?')) return
  // Snapshot the relevant state before the request: the store wipes
  // its own `selectedId` on success, so checking after would always
  // miss the "this was the active collection" case.
  const wasActive = feed.filters.collectionId === id
  const ok = await collections.deleteCollection(id)
  if (!ok) return
  if (wasActive) {
    feed.setFilters({ collectionId: null })
  }
  // Counts on the feed may have shifted (items un-tagged).
  await feed.fetchFeed()
}

async function onRename(id: number, currentName: string): Promise<void> {
  const next = window.prompt('Новое имя коллекции:', currentName)
  if (!next || next.trim() === currentName) return
  const ok = await collections.renameCollection(id, next)
  if (ok && selectedCollectionId.value === id) {
    await feed.fetchFeed()
  }
}

// ── Drag-and-drop reorder — mirrors the legacy SortableJS hookup ──
// We use the native HTML5 DnD API (no new dependency) and persist the
// resulting order via `collections.saveOrder([...ids])`. The store
// optimistically reorders its local array so the UI feels instant
// and rolls back via `refresh()` on a failed POST.
const draggedId = ref<number | null>(null)

function onDragStart(event: DragEvent, id: number): void {
  draggedId.value = id
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    // Some browsers require setData() to be called for dragover
    // events to fire reliably.
    event.dataTransfer.setData('text/plain', String(id))
  }
}

function onDragOver(event: DragEvent): void {
  // Calling preventDefault() is what enables the drop target. Without
  // it the `drop` event never fires.
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
}

async function onDrop(targetId: number): Promise<void> {
  const sourceId = draggedId.value
  draggedId.value = null
  if (sourceId === null || sourceId === targetId) return
  const order = collections.items.map((c) => c.id)
  const fromIdx = order.indexOf(sourceId)
  const toIdx = order.indexOf(targetId)
  if (fromIdx < 0 || toIdx < 0) return
  order.splice(fromIdx, 1)
  order.splice(toIdx, 0, sourceId)
  await collections.saveOrder(order)
}

function onDragEnd(): void {
  draggedId.value = null
}

const categoryLabel = (id: number, name: string): string => {
  // The legacy frontend overrides the displayed name for negative
  // sentinel IDs (-1: all video, -2: ignored, -100..-104: data-quality
  // buckets). Keep that mapping here so the sidebar matches.
  switch (id) {
    case -1:
      return 'Все видео'
    case -2:
      return 'В корзине'
    case -100:
      return 'Без постера'
    case -101:
      return 'Без рейтинга'
    case -102:
      return 'Без KP'
    case -103:
      return 'Без IMDB'
    case -104:
      return 'Без ID'
    default:
      return name
  }
}
</script>

<template>
  <aside
    class="w-full lg:w-64 lg:shrink-0 flex flex-col gap-6"
    data-testid="sidebar"
  >
    <section
      class="flex items-center justify-between rounded-2xl border border-slate-200/60 bg-white/50 backdrop-blur-sm px-4 py-3 shadow-sm"
      data-testid="sidebar-new-only"
    >
      <span class="text-xs font-semibold uppercase tracking-wide text-slate-600">
        Только новое
      </span>
      <button
        type="button"
        class="relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none"
        :class="visits.showNewOnly ? 'bg-orange-500' : 'bg-slate-300'"
        :aria-pressed="visits.showNewOnly"
        data-testid="sidebar-new-only-toggle"
        @click="toggleNewOnly"
      >
        <span
          class="inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform"
          :class="visits.showNewOnly ? 'translate-x-6' : 'translate-x-1'"
        />
      </button>
    </section>
    <section data-testid="sidebar-categories">
      <h2 class="px-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Категории
      </h2>
      <div class="mt-2 rounded-2xl border border-slate-200/60 bg-white/50 backdrop-blur-sm p-3 shadow-sm">
        <button
          type="button"
          class="w-full mb-2 rounded-xl border border-slate-200/80 bg-white/80 px-3 py-2 text-[13px] font-semibold shadow-sm hover:bg-slate-100 focus:outline-none transition-all flex justify-between items-center"
          :class="selectedCategoryId === -1 && !selectedCollectionId ? 'bg-indigo-50 text-indigo-700 border-indigo-200' : 'bg-white text-slate-700 border-slate-200/80'"
          data-testid="sidebar-all-videos-btn"
          @click="selectCategory(-1)"
        >
          <span>Все видео</span>
          <span class="text-xs text-slate-400" v-if="categories.items.find(c => c.id === -1)">
            {{ categories.items.find(c => c.id === -1)?.count }}
          </span>
        </button>
        <label class="block">
          <span class="sr-only">Выбрать категорию</span>
          <select
            v-model="selectedCategoryValue"
            class="w-full rounded-xl border border-slate-200/80 bg-white/80 px-3 py-2 text-[13px] font-semibold text-slate-800 shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 transition-all"
            data-testid="sidebar-category-select"
            :disabled="categories.loading || !dropdownCategories.length"
          >
            <option
              v-for="cat in dropdownCategories"
              :key="cat.id"
              :value="String(cat.id)"
              :data-testid="`sidebar-category-option-${cat.id}`"
            >
              {{ categoryLabel(cat.id, cat.name) }} — {{ cat.count }}
            </option>
          </select>
        </label>
        <p
          v-if="!categories.items.length && !categories.loading"
          class="px-1 py-1.5 text-xs text-slate-400"
        >
          Категорий пока нет.
        </p>
      </div>
    </section>

    <section data-testid="sidebar-collections">
      <div class="flex items-center justify-between px-1">
        <button
          type="button"
          class="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500 hover:text-slate-900"
          :aria-expanded="collectionsExpanded"
          data-testid="sidebar-collections-toggle"
          @click="collectionsExpanded = !collectionsExpanded"
        >
          <span>{{ collectionsExpanded ? '▾' : '▸' }}</span>
          <span>Коллекции</span>
          <span class="font-medium text-slate-400">({{ collections.items.length }})</span>
        </button>
        <button
          type="button"
          class="text-xs font-semibold text-slate-600 hover:text-slate-900"
          data-testid="sidebar-collections-toggle-add"
          @click="toggleAddForm"
        >
          {{ showAddForm ? '×' : '+' }}
        </button>
      </div>

      <form
        v-if="collectionsExpanded && showAddForm"
        class="mt-2 flex items-center gap-2"
        data-testid="sidebar-collections-add-form"
        @submit.prevent="submitNewCollection"
      >
        <input
          v-model="newCollectionName"
          type="text"
          placeholder="Имя коллекции"
          class="flex-1 rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs shadow-sm focus:border-slate-900 focus:outline-none"
          data-testid="sidebar-collections-input"
          :disabled="submitting"
        />
        <button
          type="submit"
          class="rounded-md bg-slate-900 px-2 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
          data-testid="sidebar-collections-submit"
          :disabled="submitting || !newCollectionName.trim()"
        >
          OK
        </button>
      </form>

      <ul
        v-if="collectionsExpanded"
        class="mt-2 flex flex-col"
        data-testid="sidebar-collections-list"
      >
        <li
          v-for="coll in collections.items"
          :key="coll.id"
          class="group flex items-center gap-1 rounded-md transition"
          :class="[
            selectedCollectionId === coll.id
              ? 'bg-indigo-50 border border-indigo-100 shadow-sm'
              : 'hover:bg-white/80 border border-transparent',
            draggedId === coll.id ? 'opacity-50' : '',
          ]"
          :draggable="true"
          :data-testid="`sidebar-collection-row-${coll.id}`"
          @dragstart="onDragStart($event, coll.id)"
          @dragover="onDragOver"
          @drop="onDrop(coll.id)"
          @dragend="onDragEnd"
        >
          <span
            class="cursor-grab select-none px-1 text-slate-300 hover:text-slate-500"
            :data-testid="`sidebar-collection-handle-${coll.id}`"
            title="Перетащите для изменения порядка"
            aria-hidden="true"
          >
            ⋮⋮
          </span>
          <button
            type="button"
            class="flex flex-1 items-center justify-between gap-2 px-2 py-1.5 text-left text-sm"
            :class="
              selectedCollectionId === coll.id
                ? 'text-indigo-700 font-semibold'
                : 'text-slate-700'
            "
            :data-testid="`sidebar-collection-${coll.id}`"
            @click="selectCollection(coll.id)"
          >
            <span class="truncate">{{ coll.name }}</span>
            <span class="text-xs text-slate-400">{{ coll.count ?? 0 }}</span>
          </button>
          <div
            class="flex items-center gap-0.5 pr-1 opacity-0 transition group-hover:opacity-100"
          >
            <button
              type="button"
              class="rounded p-1 text-slate-400 hover:bg-slate-200 hover:text-slate-900"
              :data-testid="`sidebar-collection-rename-${coll.id}`"
              title="Переименовать"
              @click.stop="onRename(coll.id, coll.name)"
            >
              ✎
            </button>
            <button
              type="button"
              class="rounded p-1 text-slate-400 hover:bg-red-100 hover:text-red-600"
              :data-testid="`sidebar-collection-delete-${coll.id}`"
              title="Удалить"
              @click.stop="onDelete(coll.id)"
            >
              ×
            </button>
          </div>
        </li>
        <li
          v-if="!collections.items.length"
          class="mt-1 rounded-md border border-dashed border-slate-300 bg-slate-50 px-3 py-3 text-xs text-slate-600"
          data-testid="sidebar-collections-empty"
        >
          <p class="font-medium text-slate-700">Коллекций пока нет.</p>
          <p class="mt-1 text-slate-500">
            Подтяните их одной кнопкой с HDRezka — либо создайте
            свою через «+» выше.
          </p>
          <button
            type="button"
            class="mt-2 w-full rounded-md bg-slate-900 px-2 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-slate-700 disabled:opacity-50"
            data-testid="sidebar-collections-empty-sync"
            :disabled="collectionsSyncing"
            @click="syncCollectionsNow"
          >
            {{ collectionsSyncing ? '⏳ Синхронизация…' : '▶ Sync с HDRezka' }}
          </button>
        </li>
      </ul>
    </section>
  </aside>
</template>
