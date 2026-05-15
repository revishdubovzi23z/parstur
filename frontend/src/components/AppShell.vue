<script setup lang="ts">
// ROADMAP Stage 10.2 — basic page chrome.
// ROADMAP Stage 10.6 — added the admin gear button next to the auth
// badge and the `<AdminPanel>` modal it controls.
// ROADMAP Stage 10.7c — added the "📊 Статистика" button next to the
// admin gear and the `<StatsPanel>` modal it controls.
// ROADMAP Stage 10.7d — added the "⚡ Синхронизация" button and the
// `<SyncPanel>` modal it controls. AppShell also owns the WS
// connect/disconnect lifecycle for the sync store so the panel can
// open with live data even on first interaction.
// ROADMAP Stage 10.7e — added the "📜 Логи" button and the
// `<LogsPanel>` overlay it controls.
// ROADMAP Stage 10.7f — mounted the `<ItemCardModal>` at app-shell
// level. The modal reads its visibility from `useItemsStore.isOpen`
// so callers (FeedItemCard) just call `items.open(id, seed)` to
// pop it.
// ROADMAP Stage 10.7h — added the "📐 Правила" and "↶ Аудит"
// buttons + their `<RulesPanel>` / `<AuditPanel>` modals.
// Follow-up to 10.7z — the legacy title was a clickable link that
// returned the user to the main feed and closed every modal; mirror
// that here (`onLogoClick`). The sync panel now also asks the shell
// to pop the logs overlay when the user starts a parser, matching
// the legacy auto-tab-switch from `index.html:1924-1957`.
//
// Header with the app name + a small auth indicator that reflects the
// session store's `status` getter. The gear button mounts the admin
// modal that owns self-update / db export / db import / db reset.
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  LOG_TYPE_LABELS,
  PROCESS_TO_LOG,
  type LogType,
  useLogsStore,
} from '../stores/logs'
import { useSessionStore } from '../stores/session'
import { useSyncStore, type ProcessKey } from '../stores/sync'
import AdminPanel from './AdminPanel.vue'
import AuditPanel from './AuditPanel.vue'
import ItemCardModal from './ItemCardModal.vue'
import LogsPanel from './LogsPanel.vue'
import RulesPanel from './RulesPanel.vue'
import StatsPanel from './StatsPanel.vue'
import SyncPanel from './SyncPanel.vue'
import ToastContainer from './ToastContainer.vue'

const session = useSessionStore()
const sync = useSyncStore()
const logs = useLogsStore()
const showAdmin = ref(false)
const showStats = ref(false)
const showSync = ref(false)
const showLogs = ref(false)
const showRules = ref(false)
const showAudit = ref(false)

function openAdmin(): void {
  showAdmin.value = true
}

function closeAdmin(): void {
  showAdmin.value = false
}

function openStats(): void {
  showStats.value = true
}

function closeStats(): void {
  showStats.value = false
}

function openSync(): void {
  showSync.value = true
}

function closeSync(): void {
  showSync.value = false
}

function openLogs(): void {
  showLogs.value = true
}

function closeLogs(): void {
  showLogs.value = false
}

function openRules(): void {
  showRules.value = true
}

function closeRules(): void {
  showRules.value = false
}

function openAudit(): void {
  showAudit.value = true
}

function closeAudit(): void {
  showAudit.value = false
}

function onStatsRunFullPipeline(): void {
  showStats.value = false
  showSync.value = true
  void sync.startFullPipeline()
}

function onSyncStarted(key: ProcessKey): void {
  // ROADMAP follow-up to 10.7z — mirror the legacy auto-switch:
  // starting any parser pops the logs overlay and pins it to the
  // matching tab so the user sees the live tail without an extra
  // click. `userInitiated=true` flips `logs.userSelected` so the
  // sync store's WS auto-follow won't yank the tab back.
  const logType: LogType | undefined = PROCESS_TO_LOG[key]
  if (!logType) return
  if (!Object.prototype.hasOwnProperty.call(LOG_TYPE_LABELS, logType)) return
  showLogs.value = true
  void logs.selectType(logType, true)
}

async function onLogoClick(): Promise<void> {
  // Close every modal—the legacy logo also returned the user to a
  // clean state. We don't clear the auth token: the goal is "go
  // home", not "sign out".
  showAdmin.value = false
  showStats.value = false
  showSync.value = false
  showLogs.value = false
  showRules.value = false
  showAudit.value = false

  // 10.7z follow-up: user wants to keep filters when clicking the logo.
  // We only scroll to top and ensure any modal is closed.
  if (typeof window !== 'undefined' && typeof window.scrollTo === 'function') {
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }
}


onMounted(() => {
  if (session.canCallApi) void sync.connect()
})

onUnmounted(() => {
  sync.disconnect()
})

watch(
  () => session.canCallApi,
  (next, prev) => {
    if (next && !prev) void sync.connect()
    if (!next && prev) sync.disconnect()
  },
)

function onRestartTriggered(): void {
  // The backend reports a restart was kicked off (self_update / reset /
  // import). Mirror legacy behaviour: give the server ~10 s to come
  // back, then force a hard reload so the user sees the new state.
  window.setTimeout(() => {
    window.location.reload()
  }, 10000)
}

const authBadge = computed(() => {
  switch (session.status) {
    case 'disabled':
      return { label: 'Без авторизации', tone: 'bg-slate-100 text-slate-600' }
    case 'authenticated':
      return { label: 'Авторизован', tone: 'bg-emerald-50 text-emerald-700' }
    case 'unauthenticated':
      return { label: 'Требуется вход', tone: 'bg-amber-50 text-amber-700' }
    default:
      return { label: 'Проверка…', tone: 'bg-slate-100 text-slate-500' }
  }
})
</script>

<template>
  <div class="flex min-h-screen flex-col">
    <header class="sticky top-0 z-50 border-b border-slate-200/50 bg-white/70 backdrop-blur-md shadow-sm">
      <div class="mx-auto flex max-w-6xl flex-col md:flex-row items-start md:items-center justify-between px-4 py-2.5 md:py-3 gap-3 md:gap-0">
        <div class="flex items-center gap-3 shrink-0">
          <button
            type="button"
            class="flex items-center gap-2 text-base font-semibold text-slate-900 hover:text-slate-700 focus:outline-none focus:ring-2 focus:ring-slate-900/20 rounded"
            data-testid="logo-home"
            aria-label="На главную"
            @click="onLogoClick"
          >
            <img src="../assets/logo.png" alt="Logo" class="h-6 w-6 object-contain" />
            <span class="bg-gradient-to-br from-slate-900 to-slate-500 bg-clip-text text-transparent">Antigravity Tracker</span>
          </button>
        </div>
        <div class="flex items-center gap-1.5 md:gap-3 w-full md:w-auto overflow-x-auto pb-1 md:pb-0 no-scrollbar">
          <span
            class="rounded-full px-2.5 py-1 text-xs font-medium shrink-0"
            :class="authBadge.tone"
            data-testid="auth-badge"
          >
            {{ authBadge.label }}
          </span>
          <button
            v-if="session.canCallApi"
            type="button"
            class="rounded-full px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-200/50 hover:text-slate-900 transition-all shrink-0"
            data-testid="sync-open"
            aria-label="Открыть панель синхронизации"
            @click="openSync"
          >
            ⚡ Синхронизация
          </button>
          <button
            v-if="session.canCallApi"
            type="button"
            class="rounded-full px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-200/50 hover:text-slate-900 transition-all shrink-0"
            data-testid="logs-open"
            aria-label="Открыть панель логов"
            @click="openLogs"
          >
            📜 Логи
          </button>
          <button
            v-if="session.canCallApi"
            type="button"
            class="rounded-full px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-200/50 hover:text-slate-900 transition-all shrink-0"
            data-testid="rules-open"
            aria-label="Открыть панель фильтр-правил"
            @click="openRules"
          >
            📐 Правила
          </button>
          <button
            v-if="session.canCallApi"
            type="button"
            class="rounded-full px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-200/50 hover:text-slate-900 transition-all shrink-0"
            data-testid="audit-open"
            aria-label="Открыть журнал аудита"
            @click="openAudit"
          >
            ↶ Аудит
          </button>
          <button
            v-if="session.canCallApi"
            type="button"
            class="rounded-full px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-200/50 hover:text-slate-900 transition-all shrink-0"
            data-testid="stats-open"
            aria-label="Открыть статистику"
            @click="openStats"
          >
            📊 Статистика
          </button>
          <button
            v-if="session.canCallApi"
            type="button"
            class="rounded-full px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-200/50 hover:text-slate-900 transition-all shrink-0"
            data-testid="admin-open"
            aria-label="Открыть панель администратора"
            @click="openAdmin"
          >
            ⚙ Админ
          </button>
        </div>
      </div>
    </header>
    <main class="flex-1">
      <slot />
    </main>
    <AdminPanel
      :open="showAdmin"
      @close="closeAdmin"
      @restart-triggered="onRestartTriggered"
    />
    <StatsPanel
      :open="showStats"
      @close="closeStats"
      @run-full-pipeline="onStatsRunFullPipeline"
    />
    <SyncPanel
      :open="showSync"
      @close="closeSync"
      @started="onSyncStarted"
    />
    <LogsPanel :open="showLogs" @close="closeLogs" />
    <RulesPanel :open="showRules" @close="closeRules" />
    <AuditPanel :open="showAudit" @close="closeAudit" />
    <ItemCardModal />
    <ToastContainer />
  </div>
</template>
