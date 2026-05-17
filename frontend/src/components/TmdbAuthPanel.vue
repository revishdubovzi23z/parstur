<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'

const props = defineProps<{ visible: boolean }>()

const status = ref<{ enabled: boolean; authenticated: boolean } | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

async function fetchStatus() {
  try {
    const resp = await fetch('/api/tmdb/status')
    status.value = await resp.json()
  } catch (e) {
    console.error('Failed to fetch TMDB status', e)
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
    const resp = await fetch('/api/tmdb/auth/start', { method: 'POST' })
    const data = await resp.json()
    if (data.status === 'success' && data.url) {
      window.open(data.url, '_blank')
      // Start polling status
      const timer = setInterval(async () => {
        await fetchStatus()
        if (status.value?.authenticated) {
          clearInterval(timer)
        }
      }, 3000)
      // Stop polling after 5 mins
      setTimeout(() => clearInterval(timer), 300000)
    } else {
      error.value = data.message || 'Ошибка запуска авторизации'
    }
  } catch (e: any) {
    error.value = e.message || 'Ошибка сети'
  } finally {
    loading.value = false
  }
}

async function onLogout() {
  if (!window.confirm('Отключить TMDB?')) return
  try {
    await fetch('/api/tmdb/logout', { method: 'POST' })
    await fetchStatus()
  } catch (e) {
    console.error('Failed to logout', e)
  }
}
</script>

<template>
  <section class="rounded-lg border border-slate-200 p-4">
    <h3 class="text-sm font-semibold text-slate-900">TMDB (Списки)</h3>

    <template v-if="status && !status.authenticated">
      <p class="mt-1 text-xs text-slate-500">
        Для управления списками требуется авторизация. Вы будете перенаправлены на сайт TMDB для подтверждения.
      </p>
      <button
        type="button"
        class="mt-3 inline-flex items-center justify-center rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
        :disabled="loading"
        @click="onConnect"
      >
        {{ loading ? 'Загрузка…' : 'Подключить TMDB' }}
      </button>
      <p v-if="error" class="mt-2 text-xs text-red-600">{{ error }}</p>
    </template>

    <template v-else-if="status && status.authenticated">
      <p class="mt-1 text-xs text-slate-500">
        Подключено. Вы можете синхронизировать коллекции со списками TMDB.
      </p>
      <button
        type="button"
        class="mt-3 inline-flex items-center justify-center rounded-md bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-200"
        @click="onLogout"
      >
        Отключить
      </button>
    </template>

    <template v-else>
      <p class="mt-1 text-xs text-slate-400">Загрузка состояния…</p>
    </template>
  </section>
</template>
