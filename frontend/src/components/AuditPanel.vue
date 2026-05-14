<script setup lang="ts">
// ROADMAP Stage 10.7h — audit-log modal.
//
// Visual continuity with the legacy modal at `index.html:1023-1047`:
// a vertical list of the last N audit rows, each with a small undo
// button when the action supports rollback. Adds a "group by action"
// toggle (off by default) backed by `useAuditStore.groupedByAction`,
// which the legacy variant approximated with mixed inline rendering.

import { computed, ref, watch } from 'vue'

import {
  type AuditEntry,
  isUndoable,
  useAuditStore,
} from '../stores/audit'
import { useFeedStore } from '../stores/feed'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const audit = useAuditStore()
const feed = useFeedStore()

const grouped = ref(false)

const totalCount = computed(() => audit.entries.length)
const undoableCount = computed(() => audit.undoableCount)

function onClose(): void {
  emit('close')
}

function onToggleGroup(): void {
  grouped.value = !grouped.value
}

async function onRefresh(): Promise<void> {
  await audit.refresh()
}

async function onUndo(entry: AuditEntry): Promise<void> {
  if (
    !window.confirm(
      `Откатить #${entry.id} (${entry.action})?`,
    )
  )
    return
  const ok = await audit.undo(entry.id)
  if (ok) {
    void feed.fetchFeed()
  }
}

function entryUndone(entry: AuditEntry): boolean {
  return entry.undone === 1 || entry.undone === true
}

function formatTimestamp(value: string | null): string {
  if (!value) return '—'
  // Audit rows are stored in UTC; the legacy UI just printed them
  // raw which is enough signal.
  return value
}

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      void audit.refresh()
    }
  },
)
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
    data-testid="audit-panel-backdrop"
    @click.self="onClose"
  >
    <div
      class="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
      data-testid="audit-panel"
      role="dialog"
      aria-modal="true"
      aria-labelledby="audit-panel-title"
    >
      <header
        class="flex items-center justify-between border-b border-slate-200 px-5 py-3"
      >
        <div class="flex items-baseline gap-3">
          <h2
            id="audit-panel-title"
            class="text-base font-semibold text-slate-900"
          >
            Аудит / откат
          </h2>
          <span
            class="text-xs font-medium text-slate-500"
            data-testid="audit-panel-count"
          >
            {{ totalCount }} записей · {{ undoableCount }} с откатом
          </span>
        </div>
        <div class="flex items-center gap-1">
          <button
            type="button"
            class="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:border-slate-300 hover:bg-slate-50"
            data-testid="audit-panel-refresh"
            aria-label="Обновить"
            @click="onRefresh"
          >
            ↻
          </button>
          <button
            type="button"
            class="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:border-slate-300 hover:bg-slate-50"
            data-testid="audit-panel-group-toggle"
            :aria-pressed="grouped"
            @click="onToggleGroup"
          >
            {{ grouped ? '↕ Сплошной' : '⌹ Группы' }}
          </button>
          <button
            type="button"
            class="rounded-md p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-700"
            aria-label="Закрыть"
            data-testid="audit-panel-close"
            @click="onClose"
          >
            ×
          </button>
        </div>
      </header>

      <div class="flex-1 space-y-2 overflow-y-auto p-5 text-sm">
        <p
          v-if="audit.error"
          class="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800"
          data-testid="audit-panel-error"
        >
          {{ audit.error }}
        </p>

        <template v-if="!totalCount && !audit.loading">
          <p
            class="py-3 text-center text-sm text-slate-400"
            data-testid="audit-panel-empty"
          >
            История пуста
          </p>
        </template>

        <template v-else-if="!grouped">
          <ul class="space-y-2" data-testid="audit-panel-list">
            <li
              v-for="entry in audit.entries"
              :key="entry.id"
              class="rounded-lg border border-slate-200 p-3"
              :class="entryUndone(entry) ? 'opacity-60' : ''"
              :data-testid="`audit-row-${entry.id}`"
            >
              <div class="flex items-start gap-3">
                <div class="min-w-0 flex-1 space-y-1">
                  <div class="text-xs font-semibold text-slate-900">
                    #{{ entry.id }} — {{ entry.action }}
                    <span
                      v-if="entry.item_id !== null"
                      class="text-slate-500"
                    >
                      item {{ entry.item_id }}
                    </span>
                    <span
                      v-if="entry.field"
                      class="ml-1 rounded bg-slate-100 px-1 font-mono text-[10px]"
                    >
                      {{ entry.field }}
                    </span>
                  </div>
                  <div
                    class="truncate font-mono text-[11px] text-slate-500"
                  >
                    {{ entry.old_value ?? '—' }} →
                    {{ entry.new_value ?? '—' }}
                  </div>
                  <div class="text-[10px] text-slate-400">
                    {{ formatTimestamp(entry.created_at) }}
                  </div>
                </div>
                <button
                  v-if="isUndoable(entry)"
                  type="button"
                  class="rounded-md bg-amber-500 px-3 py-1.5 text-[10px] font-semibold text-white hover:bg-amber-600"
                  :data-testid="`audit-undo-${entry.id}`"
                  @click="onUndo(entry)"
                >
                  ↶ Отменить
                </button>
                <span
                  v-else-if="entryUndone(entry)"
                  class="text-[10px] font-semibold text-slate-400"
                  :data-testid="`audit-undone-${entry.id}`"
                >
                  отменено
                </span>
              </div>
            </li>
          </ul>
        </template>

        <template v-else>
          <div
            v-for="(rows, action) in audit.groupedByAction"
            :key="action"
            class="space-y-2"
            :data-testid="`audit-group-${action}`"
          >
            <h3
              class="text-[11px] font-semibold uppercase tracking-widest text-slate-500"
            >
              {{ action }}
              <span class="ml-1 font-medium normal-case text-slate-400">
                ({{ rows.length }})
              </span>
            </h3>
            <ul class="space-y-2">
              <li
                v-for="entry in rows"
                :key="entry.id"
                class="rounded-lg border border-slate-200 p-3"
                :class="entryUndone(entry) ? 'opacity-60' : ''"
                :data-testid="`audit-row-${entry.id}`"
              >
                <div class="flex items-start gap-3">
                  <div class="min-w-0 flex-1 space-y-1">
                    <div class="text-xs font-semibold text-slate-900">
                      #{{ entry.id }}
                      <span
                        v-if="entry.item_id !== null"
                        class="text-slate-500"
                      >
                        item {{ entry.item_id }}
                      </span>
                      <span
                        v-if="entry.field"
                        class="ml-1 rounded bg-slate-100 px-1 font-mono text-[10px]"
                      >
                        {{ entry.field }}
                      </span>
                    </div>
                    <div
                      class="truncate font-mono text-[11px] text-slate-500"
                    >
                      {{ entry.old_value ?? '—' }} →
                      {{ entry.new_value ?? '—' }}
                    </div>
                    <div class="text-[10px] text-slate-400">
                      {{ formatTimestamp(entry.created_at) }}
                    </div>
                  </div>
                  <button
                    v-if="isUndoable(entry)"
                    type="button"
                    class="rounded-md bg-amber-500 px-3 py-1.5 text-[10px] font-semibold text-white hover:bg-amber-600"
                    :data-testid="`audit-undo-${entry.id}`"
                    @click="onUndo(entry)"
                  >
                    ↶ Отменить
                  </button>
                  <span
                    v-else-if="entryUndone(entry)"
                    class="text-[10px] font-semibold text-slate-400"
                    :data-testid="`audit-undone-${entry.id}`"
                  >
                    отменено
                  </span>
                </div>
              </li>
            </ul>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
