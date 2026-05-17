<script setup lang="ts">
// ROADMAP Stage 10.6 — admin actions modal.
//
// Replaces the legacy "⬇ БД / ⬆ БД / ⬆ ОБНОВИТЬ С GITHUB / 🗑 СБРОСИТЬ БАЗУ"
// buttons that lived in the side panel of `index.html` (~lines 266, 501-505).
// Renders four sections (self-update, export, import, reset) and
// surfaces the most recent action's result inline instead of via
// `alert()`.
//
// Container only — all behaviour is delegated to `useAdminStore`. The
// parent owns the open/close state and the auto-reload-after-restart
// affordance (we don't reload from inside the store because that
// makes the store hard to test and easy to misuse from a test runner).

import { computed, ref, watch } from 'vue'

import CollectionsIO from './CollectionsIO.vue'
import CredentialSettingsPanel from './CredentialSettingsPanel.vue'
import KinopubAuthPanel from './KinopubAuthPanel.vue'
import TmdbAuthPanel from './TmdbAuthPanel.vue'
import { useAdminStore } from '../stores/admin'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'restart-triggered'): void
}>()

const admin = useAdminStore()

const importInput = ref<HTMLInputElement | null>(null)

const anyBusy = computed(
  () =>
    admin.selfUpdateBusy ||
    admin.resetBusy ||
    admin.importBusy ||
    admin.exportBusy ||
    admin.backupBusy ||
    admin.itemsExportBusy ||
    admin.rebuildFtsBusy ||
    admin.clearDatabaseBusy ||
    admin.rebuildBusy ||
    admin.restartBusy,
)

// Items-export (`/api/export`) controls. Backend defaults to all
// video (`category_id=-1`); we keep that as the form's initial
// value and let the user pick a different category from the same
// sentinel set as the sidebar.
const itemsExportFmt = ref<'json' | 'csv'>('json')
const itemsExportCategoryId = ref<number>(-1)

const toneClass = computed(() => {
  switch (admin.lastResult?.tone) {
    case 'success':
      return 'border-emerald-200 bg-emerald-50 text-emerald-900'
    case 'error':
      return 'border-red-200 bg-red-50 text-red-900'
    case 'info':
    default:
      return 'border-slate-200 bg-slate-50 text-slate-800'
  }
})

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) {
      admin.clearResult()
    }
  },
)

function onClose(): void {
  emit('close')
}

async function onSelfUpdate(): Promise<void> {
  const result = await admin.selfUpdate()
  if (result.willRestart) emit('restart-triggered')
}

async function onReset(): Promise<void> {
  if (
    !window.confirm(
      'ВНИМАНИЕ! Все данные будут удалены без возможности восстановления. Продолжить?',
    )
  )
    return
  if (!window.confirm('Точно уверены? Это необратимо!')) return
  const result = await admin.resetDatabase()
  if (result.willRestart) emit('restart-triggered')
}

function onImportClick(): void {
  importInput.value?.click()
}

async function onImportChange(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  // Clear the input straight away so the user can pick the same file
  // a second time and still get the change event.
  input.value = ''
  if (!file) return
  if (
    !window.confirm('Импортировать базу данных? Текущие данные будут заменены!')
  )
    return
  const result = await admin.importDatabase(file)
  if (result.willRestart) emit('restart-triggered')
}

async function onExport(): Promise<void> {
  await admin.exportDatabase()
}

async function onDownloadBackup(): Promise<void> {
  await admin.downloadBackup()
}

async function onItemsExport(): Promise<void> {
  await admin.exportItems(itemsExportFmt.value, itemsExportCategoryId.value)
}

async function onRebuildFts(): Promise<void> {
  if (
    !window.confirm(
      'Перестроить полнотекстовый индекс? Операция может занять до минуты.',
    )
  ) {
    return
  }
  await admin.rebuildFts()
}

async function onClearDatabase(): Promise<void> {
  if (
    !window.confirm(
      'Очистить все медиа-данные (фильмы, папки, историю)? Авторизация и настройки сохранятся.',
    )
  ) {
    return
  }
  await admin.clearDatabase()
}

async function onRestart(): Promise<void> {
  const result = await admin.restartServer()
  if (result.willRestart) emit('restart-triggered')
}

async function onRebuild(): Promise<void> {
  if (
    !window.confirm(
      'Пересобрать зависимости и фронтенд? Это может занять несколько минут.',
    )
  )
    return
  const result = await admin.rebuildServer()
  if (result.willRestart) emit('restart-triggered')
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
    data-testid="admin-panel-backdrop"
    @click.self="onClose"
  >
    <div
      class="flex max-h-[90vh] w-full max-w-xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
      data-testid="admin-panel"
      role="dialog"
      aria-modal="true"
      aria-labelledby="admin-panel-title"
    >
      <header class="flex items-center justify-between border-b border-slate-200 px-5 py-3">
        <h2
          id="admin-panel-title"
          class="text-base font-semibold text-slate-900"
        >
          Администрирование
        </h2>
        <button
          type="button"
          class="rounded-md p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-700"
          aria-label="Закрыть"
          data-testid="admin-panel-close"
          @click="onClose"
        >
          ×
        </button>
      </header>

      <div class="flex-1 space-y-4 overflow-y-auto p-5 text-sm text-slate-700">
        <p
          v-if="admin.lastResult"
          :class="['rounded-md border px-3 py-2 text-sm', toneClass]"
          data-testid="admin-panel-result"
        >
          {{ admin.lastResult.message }}
        </p>

        <section class="rounded-lg border border-slate-200 p-4">
          <h3 class="text-sm font-semibold text-slate-900">
            Обновить с GitHub
          </h3>
          <p class="mt-1 text-xs text-slate-500">
            Запускает <code>git pull</code> на сервере. После апдейта
            бэкенд попытается перезапуститься автоматически.
          </p>
          <div class="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              class="inline-flex items-center justify-center rounded-md bg-teal-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-teal-700 disabled:opacity-50"
              data-testid="admin-self-update"
              :disabled="anyBusy"
              @click="onSelfUpdate"
            >
              {{ admin.selfUpdateBusy ? 'Обновление…' : '⬆ Обновить' }}
            </button>
            <button
              type="button"
              class="inline-flex items-center justify-center rounded-md bg-teal-50 px-3 py-1.5 text-xs font-semibold text-teal-800 border border-teal-200 hover:bg-teal-100 disabled:opacity-50"
              data-testid="admin-rebuild"
              :disabled="anyBusy"
              @click="onRebuild"
            >
              {{ admin.rebuildBusy ? 'Сборка…' : '🛠 Собрать' }}
            </button>
            <button
              type="button"
              class="inline-flex items-center justify-center rounded-md bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-800 hover:bg-slate-200 disabled:opacity-50"
              data-testid="admin-restart"
              :disabled="anyBusy"
              @click="onRestart"
            >
              {{ admin.restartBusy ? 'Запуск…' : '🔄 Перезапуск' }}
            </button>
          </div>
        </section>

        <section class="rounded-lg border border-slate-200 p-4">
          <h3 class="text-sm font-semibold text-slate-900">База данных</h3>
          <p class="mt-1 text-xs text-slate-500">
            Скачайте текущий <code>app_data.db</code> или загрузите
            другой файл. Перед импортом сервер создаст резервную
            копию.
          </p>
          <div class="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              class="inline-flex items-center justify-center rounded-md bg-slate-800 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-900 disabled:opacity-50"
              data-testid="admin-db-export"
              :disabled="anyBusy"
              @click="onExport"
            >
              {{ admin.exportBusy ? 'Экспорт…' : '⬇ Экспорт' }}
            </button>
            <button
              type="button"
              class="inline-flex items-center justify-center rounded-md bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-800 hover:bg-slate-200 disabled:opacity-50"
              data-testid="admin-db-import"
              :disabled="anyBusy"
              @click="onImportClick"
            >
              {{ admin.importBusy ? 'Импорт…' : '⬆ Импорт' }}
            </button>
            <button
              type="button"
              class="inline-flex items-center justify-center rounded-md bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-800 border border-amber-200 hover:bg-amber-100 disabled:opacity-50"
              data-testid="admin-db-clear"
              :disabled="anyBusy"
              @click="onClearDatabase"
            >
              {{ admin.clearDatabaseBusy ? 'Очистка…' : '🧹 Очистить медиа' }}
            </button>
            <input
              ref="importInput"
              type="file"
              accept=".db,application/x-sqlite3"
              class="hidden"
              data-testid="admin-db-import-input"
              @change="onImportChange"
            />
          </div>
        </section>

        <section class="rounded-lg border border-slate-200 p-4">
          <h3 class="text-sm font-semibold text-slate-900">Бэкап БД</h3>
          <p class="mt-1 text-xs text-slate-500">
            Снимает версионированную копию
            <code>app_data.db</code> в <code>backups/</code> и сразу
            отдаёт её в браузер. Отличается от «Экспорт»
            выше тем, что файл сохраняется на сервере.
          </p>
          <button
            type="button"
            class="mt-3 inline-flex items-center justify-center rounded-md bg-slate-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
            data-testid="admin-backup-download"
            :disabled="anyBusy"
            @click="onDownloadBackup"
          >
            {{ admin.backupBusy ? 'Сохранение…' : '↓ Бэкап (снимок)' }}
          </button>
        </section>

        <section class="rounded-lg border border-slate-200 p-4">
          <h3 class="text-sm font-semibold text-slate-900">Экспорт items</h3>
          <p class="mt-1 text-xs text-slate-500">
            Выгружает таблицу <code>items</code> в JSON или CSV.
            Можно ограничить одной категорией
            (<code>-1</code> = все видео).
          </p>
          <div class="mt-3 flex flex-wrap items-end gap-2">
            <label class="flex flex-col text-xs text-slate-600">
              <span class="mb-0.5">Формат</span>
              <select
                v-model="itemsExportFmt"
                class="rounded-md border border-slate-300 px-2 py-1 text-xs"
                data-testid="admin-items-export-fmt"
              >
                <option value="json">JSON</option>
                <option value="csv">CSV</option>
              </select>
            </label>
            <label class="flex flex-col text-xs text-slate-600">
              <span class="mb-0.5">category_id</span>
              <input
                v-model.number="itemsExportCategoryId"
                type="number"
                class="w-24 rounded-md border border-slate-300 px-2 py-1 text-xs"
                data-testid="admin-items-export-category"
              />
            </label>
            <button
              type="button"
              class="inline-flex items-center justify-center rounded-md bg-slate-800 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-900 disabled:opacity-50"
              data-testid="admin-items-export"
              :disabled="anyBusy"
              @click="onItemsExport"
            >
              {{ admin.itemsExportBusy ? 'Экспорт…' : '↓ Экспорт items' }}
            </button>
          </div>
        </section>

        <CollectionsIO />

        <KinopubAuthPanel :visible="open" />

        <TmdbAuthPanel :visible="open" />

        <CredentialSettingsPanel :visible="open" />

        <section class="rounded-lg border border-slate-200 p-4">
          <h3 class="text-sm font-semibold text-slate-900">
            Перестроить FTS-индекс
          </h3>
          <p class="mt-1 text-xs text-slate-500">
            Пересборка полнотекстового индекса SQLite —
            нужно после ручных изменений БД или сбоев
            поиска.
          </p>
          <button
            type="button"
            class="mt-3 inline-flex items-center justify-center rounded-md bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-700 disabled:opacity-50"
            data-testid="admin-rebuild-fts"
            :disabled="anyBusy"
            @click="onRebuildFts"
          >
            {{ admin.rebuildFtsBusy ? 'Пересборка…' : '🔍 Перестроить FTS' }}
          </button>
        </section>

        <section class="rounded-lg border border-red-200 bg-red-50/30 p-4">
          <h3 class="text-sm font-semibold text-red-900">
            Сбросить базу
          </h3>
          <p class="mt-1 text-xs text-red-800/70">
            Удаляет <code>app_data.db</code> и пересоздаёт пустую
            схему. Резервная копия не создаётся — сделайте экспорт
            заранее.
          </p>
          <button
            type="button"
            class="mt-3 inline-flex items-center justify-center rounded-md bg-red-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-50"
            data-testid="admin-db-reset"
            :disabled="anyBusy"
            @click="onReset"
          >
            {{ admin.resetBusy ? 'Сброс…' : '🗑 Сбросить' }}
          </button>
        </section>
      </div>
    </div>
  </div>
</template>
