<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { apiFetch, UnauthorizedError } from '../api/client'
import { useSessionStore } from '../stores/session'

const props = defineProps<{ visible: boolean }>()

const session = useSessionStore()
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const success = ref('')
const loaded = ref(false)

const status = reactive({
  LAMPA_ENABLED: { configured: false, value: 'true' },
  LAMPA_API_KEY: { configured: false, value: '' },
})

const draft = reactive({
  LAMPA_ENABLED: 'true',
  LAMPA_API_KEY: '',
})

const copysuccess = ref(false)

function applyCredentials(data: any): void {
  if (data.credentials) {
    if (data.credentials.LAMPA_ENABLED) {
      status.LAMPA_ENABLED = data.credentials.LAMPA_ENABLED
      draft.LAMPA_ENABLED = data.credentials.LAMPA_ENABLED.value || 'true'
    }
    if (data.credentials.LAMPA_API_KEY) {
      status.LAMPA_API_KEY = data.credentials.LAMPA_API_KEY
      draft.LAMPA_API_KEY = data.credentials.LAMPA_API_KEY.value || ''
    }
  }
}

async function loadSettings(force = false): Promise<void> {
  if (!props.visible || !session.canCallApi || (loaded.value && !force)) return
  loading.value = true
  error.value = ''
  try {
    const res = await apiFetch('/api/settings/credentials')
    if (!res.ok) {
      error.value = `HTTP ${res.status}`
      return
    }
    applyCredentials(await res.json())
    loaded.value = true
  } catch (err) {
    if (err instanceof UnauthorizedError) {
      session.handleUnauthorized(err)
    }
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function saveSettings(): Promise<void> {
  if (!session.canCallApi) {
    error.value = 'Сессия не авторизована'
    return
  }
  const values: Record<string, string> = {
    LAMPA_ENABLED: draft.LAMPA_ENABLED,
    LAMPA_API_KEY: draft.LAMPA_API_KEY.trim(),
  }

  saving.value = true
  error.value = ''
  success.value = ''
  try {
    const res = await apiFetch('/api/settings/credentials', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values }),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      error.value = String(data.error ?? `HTTP ${res.status}`)
      return
    }
    applyCredentials(await res.json())
    loaded.value = true
    success.value = 'Настройки Lampa сохранены'
  } catch (err) {
    if (err instanceof UnauthorizedError) {
      session.handleUnauthorized(err)
    }
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    saving.value = false
  }
}

const pluginUrl = computed(() => {
  const origin = window.location.origin
  let url = `${origin}/api/lampa/plugin.js`
  if (draft.LAMPA_API_KEY.trim()) {
    url += `?key=${encodeURIComponent(draft.LAMPA_API_KEY.trim())}`
  }
  return url
})

const qrCodeUrl = computed(() => {
  return `https://api.qrserver.com/v1/create-qr-code/?size=160x160&data=${encodeURIComponent(
    pluginUrl.value,
  )}`
})

function copyToClipboard(): void {
  navigator.clipboard.writeText(pluginUrl.value).then(() => {
    copysuccess.value = true
    setTimeout(() => {
      copysuccess.value = false
    }, 2000)
  })
}

watch(
  () => props.visible,
  (visible) => {
    if (visible) void loadSettings()
  },
)

onMounted(() => {
  void loadSettings()
})
</script>

<template>
  <section class="rounded-lg border border-slate-200 p-4" data-testid="lampa-panel">
    <div class="flex items-start justify-between gap-3">
      <div>
        <h3 class="text-sm font-semibold text-slate-900">📺 Lampa.mx Интеграция</h3>
        <p class="mt-1 text-xs text-slate-500">
          Синхронизируйте ваши коллекции с ТВ-интерфейсом Lampa.mx через плагин-расширение.
        </p>
      </div>
      <span
        :class="[
          'rounded-full px-2 py-1 text-[10px] font-semibold',
          draft.LAMPA_ENABLED === 'true'
            ? 'bg-emerald-50 text-emerald-700'
            : 'bg-slate-100 text-slate-500',
        ]"
      >
        {{ draft.LAMPA_ENABLED === 'true' ? 'Активен' : 'Отключен' }}
      </span>
    </div>

    <div class="mt-4 grid gap-3 sm:grid-cols-2">
      <label class="flex flex-col gap-1 text-xs text-slate-600">
        <span class="font-semibold">Состояние плагина</span>
        <select
          v-model="draft.LAMPA_ENABLED"
          class="rounded-md border border-slate-300 px-2 py-1.5 text-xs bg-white"
          data-testid="lampa-enabled-select"
        >
          <option value="true">Включен</option>
          <option value="false">Отключен</option>
        </select>
      </label>

      <label class="flex flex-col gap-1 text-xs text-slate-600">
        <span class="font-semibold">Ключ доступа (X-API-Key / key=...)</span>
        <input
          v-model="draft.LAMPA_API_KEY"
          class="rounded-md border border-slate-300 px-2 py-1.5 text-xs"
          placeholder="Пусто = публичный доступ"
          data-testid="lampa-api-key-input"
        />
      </label>
    </div>

    <!-- Plugin URL and QR Code when active -->
    <div
      v-if="draft.LAMPA_ENABLED === 'true'"
      class="mt-4 border-t border-slate-100 pt-4 space-y-4"
    >
      <div>
        <h4 class="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Ссылка на расширение для Lampa
        </h4>
        <p class="mt-1 text-[11px] text-slate-500 leading-normal">
          Добавьте эту ссылку в настройки Lampa → Расширения → Добавить расширение.
        </p>
      </div>

      <div class="flex flex-col md:flex-row items-center gap-4 bg-slate-50 p-3 rounded-lg border border-slate-150">
        <!-- QR Code -->
        <div class="flex-shrink-0 bg-white p-1 rounded border border-slate-200">
          <img
            :src="qrCodeUrl"
            alt="QR Code"
            class="w-[120px] h-[120px]"
            title="Отсканируйте для быстрого ввода"
          />
        </div>

        <!-- URL copy input -->
        <div class="flex-1 min-w-0 w-full space-y-2">
          <div class="flex items-center gap-1.5 w-full">
            <input
              type="text"
              readonly
              :value="pluginUrl"
              class="flex-1 min-w-0 rounded-md border border-slate-300 bg-white px-2 py-1.5 text-xs font-mono select-all text-slate-700"
            />
            <button
              type="button"
              class="flex-shrink-0 inline-flex items-center justify-center rounded-md bg-teal-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-teal-700 active:scale-95 transition-all"
              @click="copyToClipboard"
            >
              {{ copysuccess ? 'Скопировано!' : 'Копировать' }}
            </button>
          </div>
          <p class="text-[10px] text-slate-400">
            💡 QR-код и ссылка содержат ключ авторизации, если он настроен. Сканируйте его телефоном или отправьте на ТВ для моментальной настройки.
          </p>
        </div>
      </div>
    </div>

    <div class="mt-4 flex flex-wrap items-center gap-2">
      <button
        type="button"
        class="inline-flex items-center justify-center rounded-md bg-slate-800 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-900 disabled:opacity-50"
        :disabled="loading || saving"
        @click="saveSettings"
      >
        {{ saving ? 'Сохранение…' : 'Сохранить настройки Lampa' }}
      </button>
      <span v-if="success" class="text-xs font-semibold text-emerald-700">
        {{ success }}
      </span>
      <span v-if="error" class="text-xs font-semibold text-red-700">
        {{ error }}
      </span>
    </div>
  </section>
</template>
