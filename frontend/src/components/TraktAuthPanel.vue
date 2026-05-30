<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

const props = defineProps<{ visible: boolean }>()

const status = ref<{ enabled: boolean; authenticated: boolean } | null>(null)
const loading = ref(false)
const exchanging = ref(false)
const error = ref<string | null>(null)
const authUrl = ref<string | null>(null)
const pinCode = ref('')

async function fetchStatus() {
  try {
    const resp = await fetch('/api/trakt/status')
    status.value = await resp.json()
  } catch (e) {
    console.error('Failed to fetch Trakt status', e)
  }
}

onMounted(() => {
  if (props.visible) void fetchStatus()
})

watch(
  () => props.visible,
  (open) => {
    if (open) void fetchStatus()
  },
)

async function onConnect() {
  loading.value = true
  error.value = null
  try {
    const resp = await fetch('/api/trakt/auth/start', { method: 'POST' })
    const data = await resp.json()
    if (data.status === 'success' && data.url) {
      authUrl.value = data.url
      window.open(data.url, '_blank')
    } else {
      error.value = data.message || 'Ошибка запуска авторизации'
    }
  } catch (e: any) {
    error.value = e.message || 'Ошибка сети'
  } finally {
    loading.value = false
  }
}

async function onConfirmPin() {
  const pin = pinCode.value.trim()
  if (!pin) {
    error.value = 'Введите PIN-код'
    return
  }
  exchanging.value = true
  error.value = null
  try {
    const resp = await fetch('/api/trakt/auth/exchange', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin }),
    })
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}))
      throw new Error(data.detail || `HTTP ${resp.status}`)
    }
    pinCode.value = ''
    authUrl.value = null
    await fetchStatus()
  } catch (e: any) {
    error.value = e.message || 'Ошибка обмена токена'
  } finally {
    exchanging.value = false
  }
}

function onCancelAuth() {
  authUrl.value = null
  pinCode.value = ''
  error.value = null
}

async function onLogout() {
  if (!window.confirm('Отключить Trakt.tv?')) return
  try {
    await fetch('/api/trakt/logout', { method: 'POST' })
    await fetchStatus()
  } catch (e) {
    console.error('Failed to logout from Trakt', e)
  }
}
</script>

<template>
  <section class="rounded-lg border border-slate-200 p-4">
    <h3 class="text-sm font-semibold text-slate-900">Trakt.tv (Списки и история)</h3>

    <!-- NOT AUTHENTICATED & NOT IN PIN PROGRESS -->
    <template v-if="status && !status.authenticated && !authUrl">
      <p class="mt-1 text-xs text-slate-500">
        Авторизация через Trakt API: вы перейдете на сайт Trakt.tv для подтверждения, а затем скопируете PIN-код сюда.
      </p>
      <button
        type="button"
        class="mt-3 inline-flex items-center justify-center rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
        :disabled="loading"
        @click="onConnect"
      >
        {{ loading ? 'Загрузка…' : 'Подключить Trakt.tv' }}
      </button>
      <p v-if="error" class="mt-2 text-xs text-red-600">{{ error }}</p>
    </template>

    <!-- OAUTH PIN EXCHANGE IN PROGRESS -->
    <template v-else-if="status && !status.authenticated && authUrl">
      <p class="mt-1 text-xs text-slate-500">
        1. Войдите на Trakt и подтвердите доступ (ссылка открылась в новой вкладке). Если нет:
        <a :href="authUrl" target="_blank" class="text-indigo-600 underline font-semibold ml-1">Открыть вручную</a>.<br>
        2. Скопируйте полученный 8-значный PIN-код и введите его ниже:
      </p>
      <div class="mt-3 flex items-center gap-2 max-w-sm">
        <input
          v-model="pinCode"
          placeholder="Введите PIN-код"
          class="rounded-md border border-slate-300 px-3 py-1.5 text-xs w-full font-mono text-center tracking-widest"
          @keyup.enter="onConfirmPin"
        />
        <button
          type="button"
          class="inline-flex items-center justify-center rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
          :disabled="exchanging"
          @click="onConfirmPin"
        >
          {{ exchanging ? 'Проверка…' : 'ОК' }}
        </button>
      </div>
      <div class="mt-2 flex items-center gap-2">
        <button
          type="button"
          class="text-xs text-slate-500 hover:text-slate-700"
          @click="onCancelAuth"
        >
          Отмена
        </button>
      </div>
      <p v-if="error" class="mt-2 text-xs text-red-600">{{ error }}</p>
    </template>

    <!-- AUTHENTICATED -->
    <template v-else-if="status && status.authenticated">
      <p class="mt-1 text-xs text-slate-500">
        Подключено. Вы можете использовать двунаправленную синхронизацию коллекций с вашими списками на Trakt.tv.
      </p>
      <button
        type="button"
        class="mt-3 inline-flex items-center justify-center rounded-md bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-200"
        @click="onLogout"
      >
        Отключить
      </button>
    </template>

    <!-- LOADING -->
    <template v-else>
      <p class="mt-1 text-xs text-slate-400">Загрузка состояния…</p>
    </template>
  </section>
</template>
