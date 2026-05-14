<script setup lang="ts">
// PR 2 of the kino.pub integration — Device-Flow connect / logout
// section. Mounted inside `AdminPanel.vue`.
//
// Behavior:
//  * On mount / when the parent panel opens, ping /api/kinopub/status.
//  * If not authenticated, show a "Подключить" button. Pressing it
//    starts the Device Flow, displays the `user_code` + a clickable
//    verification URL, and begins polling.
//  * On confirmation the section flips into the "connected" state
//    (expiry in minutes, "Отключить" button).
//  * If disabled (KINOPUB_ENABLED=false on the backend), the section
//    explains how to enable it and offers no actions.

import { computed, onBeforeUnmount, onMounted, watch } from 'vue'

import { useKinopubStore } from '../stores/kinopub'

const props = defineProps<{ visible: boolean }>()

const store = useKinopubStore()

const expiryHuman = computed(() => {
  const s = store.status?.expiresIn
  if (s == null || s <= 0) return null
  const mins = Math.floor(s / 60)
  if (mins < 60) return `~${mins} мин`
  const hrs = Math.floor(mins / 60)
  return `~${hrs} ч`
})

const deviceCodeTimeoutHuman = computed(() => {
  if (!store.flow) return null
  const remainingMs = store.flow.expiresAtMs - Date.now()
  const secs = Math.max(0, Math.floor(remainingMs / 1000))
  const mins = Math.floor(secs / 60)
  return `${mins}:${String(secs % 60).padStart(2, '0')}`
})

onMounted(() => {
  if (props.visible) void store.fetchStatus()
})

// Refresh status whenever the parent panel becomes visible — keeps
// the badge in sync if the operator was authenticated in another tab.
watch(
  () => props.visible,
  (open) => {
    if (open) void store.fetchStatus()
  },
)

onBeforeUnmount(() => {
  store.stopPolling()
})

async function onConnect(): Promise<void> {
  await store.startDeviceFlow()
  if (store.flow) store.startPolling()
}

function onCancelFlow(): void {
  store.cancelDeviceFlow()
}

async function onLogout(): Promise<void> {
  if (!window.confirm('Отключить parstur от kino.pub?')) return
  await store.logout()
}
</script>

<template>
  <section
    class="rounded-lg border border-slate-200 p-4"
    data-testid="kinopub-auth-panel"
  >
    <h3 class="text-sm font-semibold text-slate-900">kino.pub</h3>

    <!-- DISABLED on the backend ─────────────────────────────────── -->
    <template v-if="store.status && !store.status.enabled">
      <p class="mt-1 text-xs text-slate-500" data-testid="kinopub-disabled-hint">
        Интеграция выключена. Установите
        <code>KINOPUB_ENABLED=true</code> в <code>.env</code> и
        перезапустите сервер.
      </p>
    </template>

    <!-- ENABLED but not yet authenticated ───────────────────────── -->
    <template v-else-if="store.status && !store.status.authenticated && !store.flow">
      <p class="mt-1 text-xs text-slate-500">
        Авторизация через Device Flow: parstur не увидит ваш
        пароль — вы введёте короткий код у себя в браузере на
        kino.pub.
      </p>
      <button
        type="button"
        class="mt-3 inline-flex items-center justify-center rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
        data-testid="kinopub-connect"
        :disabled="store.statusBusy || store.flowBusy || store.pollBusy"
        @click="onConnect"
      >
        {{ store.flowBusy ? 'Запрашиваем код…' : 'Подключить kino.pub' }}
      </button>
      <p
        v-if="store.statusError"
        class="mt-2 rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-800"
        data-testid="kinopub-status-error"
      >
        {{ store.statusError }}
      </p>
    </template>

    <!-- DEVICE FLOW in progress ─────────────────────────────────── -->
    <template v-else-if="store.flow && store.pollState === 'pending'">
      <p class="mt-1 text-xs text-slate-500">
        1. Откройте
        <a
          :href="store.flow.verificationUri"
          target="_blank"
          rel="noopener noreferrer"
          class="font-mono text-indigo-700 hover:underline"
          data-testid="kinopub-verification-uri"
        >{{ store.flow.verificationUri }}</a>
        в новом окне и войдите в свой аккаунт.<br>
        2. Введите там этот код:
      </p>
      <div
        class="mt-3 rounded-md border border-slate-300 bg-slate-50 px-4 py-3 text-center font-mono text-2xl tracking-widest text-slate-900"
        data-testid="kinopub-user-code"
      >
        {{ store.flow.userCode }}
      </div>
      <p class="mt-2 text-xs text-slate-500">
        Код истечёт через <span data-testid="kinopub-flow-timer">{{ deviceCodeTimeoutHuman }}</span>.
        После подтверждения этот блок закроется сам.
      </p>
      <button
        type="button"
        class="mt-3 inline-flex items-center justify-center rounded-md bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-200"
        data-testid="kinopub-cancel-flow"
        @click="onCancelFlow"
      >
        Отменить
      </button>
      <p
        v-if="store.pollError"
        class="mt-2 rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-800"
        data-testid="kinopub-poll-error"
      >
        {{ store.pollError }}
      </p>
    </template>

    <!-- Device code expired without confirmation ────────────────── -->
    <template v-else-if="store.pollState === 'expired' && !store.status?.authenticated">
      <p
        class="mt-2 rounded border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-900"
        data-testid="kinopub-flow-expired"
      >
        Код истёк до подтверждения. Попробуйте ещё раз.
      </p>
      <button
        type="button"
        class="mt-3 inline-flex items-center justify-center rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700"
        data-testid="kinopub-retry"
        @click="onConnect"
      >
        Повторить
      </button>
    </template>

    <!-- AUTHENTICATED ───────────────────────────────────────────── -->
    <template v-else-if="store.isAuthenticated">
      <p class="mt-1 text-xs text-slate-500" data-testid="kinopub-connected-line">
        Подключено. Access-token обновится автоматически.
        <span v-if="expiryHuman">Истекает через {{ expiryHuman }}.</span>
      </p>
      <button
        type="button"
        class="mt-3 inline-flex items-center justify-center rounded-md bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-200 disabled:opacity-50"
        data-testid="kinopub-logout"
        :disabled="store.logoutBusy"
        @click="onLogout"
      >
        {{ store.logoutBusy ? 'Отключение…' : 'Отключить' }}
      </button>
    </template>

    <!-- LOADING (initial paint, status still null) ──────────────── -->
    <template v-else>
      <p class="mt-1 text-xs text-slate-400" data-testid="kinopub-loading">
        Загрузка состояния…
      </p>
    </template>
  </section>
</template>
