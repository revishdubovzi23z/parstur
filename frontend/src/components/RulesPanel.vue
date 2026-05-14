<script setup lang="ts">
// ROADMAP Stage 10.7h — filter-rules manager modal.
//
// Visual continuity with the legacy modal at `index.html:980-1021`:
// scrollable rules table with a per-row enable toggle + delete button,
// inline "new rule" form below. Adds an inline edit affordance the
// legacy variant lacked (legacy only supported create / toggle /
// delete), so the same row can be patched without recreating it.

import { computed, reactive, ref, watch } from 'vue'

import {
  type FilterRule,
  type FilterRuleAction,
  type FilterRuleField,
  useRulesStore,
} from '../stores/rules'
import { useFeedStore } from '../stores/feed'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const rules = useRulesStore()
const feed = useFeedStore()

const FIELDS: { value: FilterRuleField; label: string }[] = [
  { value: 'title', label: 'title' },
  { value: 'original_title', label: 'original_title' },
  { value: 'description', label: 'description' },
]

const ACTIONS: { value: FilterRuleAction; label: string }[] = [
  { value: 'hide', label: 'hide (скрыть)' },
  { value: 'highlight', label: 'highlight (выделить)' },
]

interface NewRuleDraft {
  name: string
  field: FilterRuleField
  pattern: string
  action: FilterRuleAction
}

const newRule = reactive<NewRuleDraft>({
  name: '',
  field: 'title',
  pattern: '',
  action: 'hide',
})

const newRuleError = ref<string | null>(null)
const submitting = ref(false)

/** Row currently in edit mode (one-at-a-time). Holds a partial copy
 *  of the row so cancelling reverts cleanly without an extra refresh. */
interface EditDraft {
  id: number
  name: string
  field: FilterRuleField
  pattern: string
  action: FilterRuleAction
}

const editDraft = ref<EditDraft | null>(null)
const editError = ref<string | null>(null)

const editingId = computed(() => editDraft.value?.id ?? null)

function resetNewRule(): void {
  newRule.name = ''
  newRule.field = 'title'
  newRule.pattern = ''
  newRule.action = 'hide'
  newRuleError.value = null
}

function onClose(): void {
  emit('close')
}

async function onCreate(): Promise<void> {
  newRuleError.value = null
  if (!newRule.name.trim() || !newRule.pattern.trim()) {
    newRuleError.value = 'Имя и regex обязательны'
    return
  }
  submitting.value = true
  try {
    const id = await rules.create({
      name: newRule.name.trim(),
      field: newRule.field,
      pattern: newRule.pattern,
      action: newRule.action,
      enabled: true,
    })
    if (id === null) {
      newRuleError.value = rules.error ?? 'Не удалось создать правило'
      return
    }
    resetNewRule()
    // Refresh the feed so the new rule takes visual effect immediately,
    // mirroring legacy `createRule()` at `index.html:2453-2454`.
    void feed.fetchFeed()
  } finally {
    submitting.value = false
  }
}

async function onToggle(rule: FilterRule): Promise<void> {
  const ok = await rules.toggle(rule)
  if (ok) void feed.fetchFeed()
}

async function onDelete(rule: FilterRule): Promise<void> {
  if (!window.confirm(`Удалить правило "${rule.name}"?`)) return
  const ok = await rules.remove(rule.id)
  if (ok) void feed.fetchFeed()
}

function onEdit(rule: FilterRule): void {
  editDraft.value = {
    id: rule.id,
    name: rule.name,
    field: rule.field,
    pattern: rule.pattern,
    action: rule.action,
  }
  editError.value = null
}

function onCancelEdit(): void {
  editDraft.value = null
  editError.value = null
}

async function onSaveEdit(): Promise<void> {
  const draft = editDraft.value
  if (!draft) return
  editError.value = null
  if (!draft.name.trim() || !draft.pattern.trim()) {
    editError.value = 'Имя и regex обязательны'
    return
  }
  const ok = await rules.update(draft.id, {
    name: draft.name.trim(),
    field: draft.field,
    pattern: draft.pattern,
    action: draft.action,
  })
  if (ok) {
    editDraft.value = null
    void feed.fetchFeed()
  } else {
    editError.value = rules.error ?? 'Не удалось обновить правило'
  }
}

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      void rules.refresh()
      resetNewRule()
      editDraft.value = null
    }
  },
)
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
    data-testid="rules-panel-backdrop"
    @click.self="onClose"
  >
    <div
      class="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
      data-testid="rules-panel"
      role="dialog"
      aria-modal="true"
      aria-labelledby="rules-panel-title"
    >
      <header
        class="flex items-center justify-between border-b border-slate-200 px-5 py-3"
      >
        <h2
          id="rules-panel-title"
          class="text-base font-semibold text-slate-900"
        >
          Фильтр-правила
          <span
            v-if="rules.rules.length"
            class="ml-2 text-xs font-medium text-slate-500"
            data-testid="rules-panel-count"
          >
            {{ rules.enabledCount }} / {{ rules.rules.length }} активно
          </span>
        </h2>
        <button
          type="button"
          class="rounded-md p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-700"
          aria-label="Закрыть"
          data-testid="rules-panel-close"
          @click="onClose"
        >
          ×
        </button>
      </header>

      <div class="flex-1 space-y-3 overflow-y-auto p-5 text-sm">
        <p
          v-if="rules.error && !submitting"
          class="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800"
          data-testid="rules-panel-error"
        >
          {{ rules.error }}
        </p>

        <ul
          v-if="rules.rules.length"
          class="space-y-2"
          data-testid="rules-panel-list"
        >
          <li
            v-for="rule in rules.rules"
            :key="rule.id"
            class="rounded-lg border border-slate-200 p-3"
            :data-testid="`rules-row-${rule.id}`"
          >
            <template v-if="editingId === rule.id && editDraft">
              <div class="space-y-2">
                <input
                  v-model="editDraft.name"
                  type="text"
                  placeholder="Название"
                  class="w-full rounded-md bg-slate-100 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900"
                  :data-testid="`rules-edit-name-${rule.id}`"
                />
                <div class="grid grid-cols-2 gap-2">
                  <select
                    v-model="editDraft.field"
                    class="rounded-md bg-slate-100 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900"
                    :data-testid="`rules-edit-field-${rule.id}`"
                  >
                    <option
                      v-for="opt in FIELDS"
                      :key="opt.value"
                      :value="opt.value"
                    >
                      {{ opt.label }}
                    </option>
                  </select>
                  <select
                    v-model="editDraft.action"
                    class="rounded-md bg-slate-100 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900"
                    :data-testid="`rules-edit-action-${rule.id}`"
                  >
                    <option
                      v-for="opt in ACTIONS"
                      :key="opt.value"
                      :value="opt.value"
                    >
                      {{ opt.label }}
                    </option>
                  </select>
                </div>
                <input
                  v-model="editDraft.pattern"
                  type="text"
                  placeholder="regex (Python re)"
                  class="w-full rounded-md bg-slate-100 px-3 py-2 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-slate-900"
                  :data-testid="`rules-edit-pattern-${rule.id}`"
                />
                <p
                  v-if="editError"
                  class="text-xs text-red-600"
                  data-testid="rules-edit-error"
                >
                  {{ editError }}
                </p>
                <div class="flex justify-end gap-2">
                  <button
                    type="button"
                    class="rounded-md bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-200"
                    :data-testid="`rules-edit-cancel-${rule.id}`"
                    @click="onCancelEdit"
                  >
                    Отмена
                  </button>
                  <button
                    type="button"
                    class="rounded-md bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-800"
                    :data-testid="`rules-edit-save-${rule.id}`"
                    @click="onSaveEdit"
                  >
                    Сохранить
                  </button>
                </div>
              </div>
            </template>
            <template v-else>
              <div class="flex items-center gap-3">
                <input
                  type="checkbox"
                  :checked="rule.enabled"
                  class="h-4 w-4 cursor-pointer"
                  :data-testid="`rules-toggle-${rule.id}`"
                  :aria-label="`Активность правила ${rule.name}`"
                  @change="onToggle(rule)"
                />
                <div class="min-w-0 flex-1">
                  <div class="truncate text-sm font-semibold text-slate-900">
                    {{ rule.name }}
                  </div>
                  <div class="truncate text-xs text-slate-500">
                    <span class="rounded bg-slate-100 px-1 font-mono">
                      {{ rule.field }}
                    </span>
                    <span class="rounded bg-slate-100 px-1 font-mono">
                      {{ rule.action }}
                    </span>
                    <span class="ml-1 font-mono">/{{ rule.pattern }}/</span>
                  </div>
                </div>
                <button
                  type="button"
                  class="rounded-md bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-200"
                  :data-testid="`rules-edit-${rule.id}`"
                  @click="onEdit(rule)"
                >
                  ✎
                </button>
                <button
                  type="button"
                  class="rounded-md bg-red-50 px-2 py-1 text-xs font-semibold text-red-700 hover:bg-red-100"
                  :data-testid="`rules-delete-${rule.id}`"
                  @click="onDelete(rule)"
                >
                  ✕
                </button>
              </div>
            </template>
          </li>
        </ul>
        <p
          v-else-if="!rules.loading"
          class="py-3 text-center text-sm text-slate-400"
          data-testid="rules-panel-empty"
        >
          Правил пока нет
        </p>

        <section
          class="space-y-2 border-t border-slate-200 pt-4"
          data-testid="rules-panel-form"
        >
          <h3 class="text-xs font-semibold uppercase tracking-widest text-slate-500">
            Новое правило
          </h3>
          <input
            v-model="newRule.name"
            type="text"
            placeholder="Название"
            class="w-full rounded-md bg-slate-100 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900"
            data-testid="rules-new-name"
          />
          <div class="grid grid-cols-2 gap-2">
            <select
              v-model="newRule.field"
              class="rounded-md bg-slate-100 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900"
              data-testid="rules-new-field"
            >
              <option
                v-for="opt in FIELDS"
                :key="opt.value"
                :value="opt.value"
              >
                {{ opt.label }}
              </option>
            </select>
            <select
              v-model="newRule.action"
              class="rounded-md bg-slate-100 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900"
              data-testid="rules-new-action"
            >
              <option
                v-for="opt in ACTIONS"
                :key="opt.value"
                :value="opt.value"
              >
                {{ opt.label }}
              </option>
            </select>
          </div>
          <input
            v-model="newRule.pattern"
            type="text"
            placeholder="regex (Python re)"
            class="w-full rounded-md bg-slate-100 px-3 py-2 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-slate-900"
            data-testid="rules-new-pattern"
          />
          <p
            v-if="newRuleError"
            class="text-xs text-red-600"
            data-testid="rules-new-error"
          >
            {{ newRuleError }}
          </p>
          <button
            type="button"
            class="w-full rounded-md bg-slate-900 px-3 py-2 text-xs font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
            data-testid="rules-new-submit"
            :disabled="submitting"
            @click="onCreate"
          >
            {{ submitting ? 'Создаём…' : '+ Добавить' }}
          </button>
        </section>
      </div>
    </div>
  </div>
</template>
