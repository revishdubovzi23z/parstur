<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { apiFetch, UnauthorizedError } from '../api/client'
import { useSessionStore } from '../stores/session'

const props = defineProps<{ visible: boolean }>()

type CredentialKey =
  | 'REZKA_EMAIL'
  | 'REZKA_PASSWORD'
  | 'KINOPOISK_API_KEY'
  | 'POISKKINO_API_KEY'
  | 'TMDB_API_KEY'
  | 'TMDB_API_TOKEN'
  | 'KINOPUB_ENABLED'
  | 'REZKA_ENABLED'
  | 'KINOHUB_ENABLED'
  | 'KINOPUB_CLIENT_ID'
  | 'KINOPUB_CLIENT_SECRET'

interface CredentialEntry {
  configured: boolean
  value: string
}

interface CredentialsResponse {
  credentials: Record<CredentialKey, CredentialEntry>
}

const session = useSessionStore()

const loading = ref(false)
const saving = ref(false)
const error = ref('')
const success = ref('')
const loaded = ref(false)
const status = reactive<Record<CredentialKey, CredentialEntry>>({
  REZKA_EMAIL: { configured: false, value: '' },
  REZKA_PASSWORD: { configured: false, value: '' },
  KINOPOISK_API_KEY: { configured: false, value: '' },
  POISKKINO_API_KEY: { configured: false, value: '' },
  TMDB_API_KEY: { configured: false, value: '' },
  TMDB_API_TOKEN: { configured: false, value: '' },
  KINOPUB_ENABLED: { configured: false, value: '' },
  REZKA_ENABLED: { configured: false, value: '' },
  KINOHUB_ENABLED: { configured: false, value: '' },
  KINOPUB_CLIENT_ID: { configured: false, value: '' },
  KINOPUB_CLIENT_SECRET: { configured: false, value: '' },
})
const draft = reactive<Record<CredentialKey, string>>({
  REZKA_EMAIL: '',
  REZKA_PASSWORD: '',
  KINOPOISK_API_KEY: '',
  POISKKINO_API_KEY: '',
  TMDB_API_KEY: '',
  TMDB_API_TOKEN: '',
  KINOPUB_ENABLED: '',
  REZKA_ENABLED: '',
  KINOHUB_ENABLED: '',
  KINOPUB_CLIENT_ID: '',
  KINOPUB_CLIENT_SECRET: '',
})

const sensitiveKeys = new Set<CredentialKey>([
  'REZKA_PASSWORD',
  'KINOPOISK_API_KEY',
  'POISKKINO_API_KEY',
  'TMDB_API_KEY',
  'TMDB_API_TOKEN',
])

const configuredCount = computed(
  () => Object.values(status).filter((entry) => entry.configured).length,
)

function applyCredentials(data: CredentialsResponse): void {
  for (const [key, entry] of Object.entries(data.credentials) as Array<
    [CredentialKey, CredentialEntry]
  >) {
    status[key] = entry
    draft[key] = sensitiveKeys.has(key) ? '' : entry.value
  }
}

async function loadCredentials(force = false): Promise<void> {
  if (!props.visible || !session.canCallApi || (loaded.value && !force)) return
  loading.value = true
  error.value = ''
  try {
    const res = await apiFetch('/api/settings/credentials')
    if (!res.ok) {
      error.value = `HTTP ${res.status}`
      return
    }
    applyCredentials((await res.json()) as CredentialsResponse)
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

async function saveCredentials(): Promise<void> {
  if (!session.canCallApi) {
    error.value = 'Сессия не авторизована'
    return
  }
  const values: Partial<Record<CredentialKey, string>> = {}
  for (const key of Object.keys(draft) as CredentialKey[]) {
    const value = draft[key].trim()
    if (sensitiveKeys.has(key) && value === '') continue
    if (!sensitiveKeys.has(key) && value === status[key].value) continue
    values[key] = value
  }
  if (!Object.keys(values).length) {
    success.value = 'Нет изменений'
    error.value = ''
    return
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
    applyCredentials((await res.json()) as CredentialsResponse)
    loaded.value = true
    success.value = 'Настройки сохранены'
  } catch (err) {
    if (err instanceof UnauthorizedError) {
      session.handleUnauthorized(err)
    }
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    saving.value = false
  }
}

watch(
  () => props.visible,
  (visible) => {
    if (visible) void loadCredentials()
  },
)

onMounted(() => {
  void loadCredentials()
})
</script>

<template>
  <section
    class="rounded-lg border border-slate-200 p-4"
    data-testid="credential-settings-panel"
  >
    <div class="flex items-start justify-between gap-3">
      <div>
        <h3 class="text-sm font-semibold text-slate-900">
          Rezka и API-ключи
        </h3>
        <p class="mt-1 text-xs text-slate-500">
          Здесь можно заполнить <code>.env</code> для Rezka и внешних API без
          ручного редактирования файла.
        </p>
      </div>
      <span class="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-semibold text-slate-600">
        {{ configuredCount }}/6 задано
      </span>
    </div>

    <div class="mt-4 grid gap-3 sm:grid-cols-2">
      <label class="flex flex-col gap-1 text-xs text-slate-600">
        <span class="font-semibold">Rezka email/login</span>
        <input
          v-model="draft.REZKA_EMAIL"
          class="rounded-md border border-slate-300 px-2 py-1.5 text-xs"
          data-testid="credentials-rezka-email"
          autocomplete="username"
          placeholder="REZKA_EMAIL"
        />
      </label>
      <label class="flex flex-col gap-1 text-xs text-slate-600">
        <span class="font-semibold">Rezka password</span>
        <input
          v-model="draft.REZKA_PASSWORD"
          class="rounded-md border border-slate-300 px-2 py-1.5 text-xs"
          data-testid="credentials-rezka-password"
          type="password"
          autocomplete="current-password"
          :placeholder="status.REZKA_PASSWORD.configured ? 'Сейчас сохранён' : 'REZKA_PASSWORD'"
        />
      </label>
    </div>

    <div class="mt-4 border-t border-slate-100 pt-4">
      <h4 class="text-xs font-semibold uppercase tracking-wide text-slate-500">
        API для скриптов
      </h4>
      <div class="mt-3 grid gap-3 sm:grid-cols-2">
        <label class="flex flex-col gap-1 text-xs text-slate-600">
          <span class="font-semibold">Kinopoisk API key</span>
          <input
            v-model="draft.KINOPOISK_API_KEY"
            class="rounded-md border border-slate-300 px-2 py-1.5 text-xs"
            data-testid="credentials-kinopoisk-api-key"
            type="password"
            :placeholder="status.KINOPOISK_API_KEY.configured ? 'Сейчас сохранён' : 'KINOPOISK_API_KEY'"
          />
        </label>
        <label class="flex flex-col gap-1 text-xs text-slate-600">
          <span class="font-semibold">PoiskKino API key</span>
          <input
            v-model="draft.POISKKINO_API_KEY"
            class="rounded-md border border-slate-300 px-2 py-1.5 text-xs"
            data-testid="credentials-poiskkino-api-key"
            type="password"
            :placeholder="status.POISKKINO_API_KEY.configured ? 'Сейчас сохранён' : 'POISKKINO_API_KEY'"
          />
        </label>
        <label class="flex flex-col gap-1 text-xs text-slate-600">
          <span class="font-semibold">TMDB API key (v3)</span>
          <input
            v-model="draft.TMDB_API_KEY"
            class="rounded-md border border-slate-300 px-2 py-1.5 text-xs"
            data-testid="credentials-tmdb-api-key"
            type="password"
            :placeholder="status.TMDB_API_KEY.configured ? 'Сейчас сохранён' : 'TMDB_API_KEY'"
          />
        </label>
        <label class="flex flex-col gap-1 text-xs text-slate-600">
          <span class="font-semibold">TMDB API token (v4)</span>
          <input
            v-model="draft.TMDB_API_TOKEN"
            class="rounded-md border border-slate-300 px-2 py-1.5 text-xs"
            data-testid="credentials-tmdb-api-token"
            type="password"
            :placeholder="status.TMDB_API_TOKEN.configured ? 'Сейчас сохранён' : 'TMDB_API_TOKEN'"
          />
        </label>
      </div>
    </div>



    <div class="mt-4 border-t border-slate-100 pt-4">
      <h4 class="text-xs font-semibold uppercase tracking-wide text-slate-500">
        Источники (Вкл/Выкл)
      </h4>
      <div class="mt-3 grid gap-3 sm:grid-cols-2">
        <label class="flex flex-col gap-1 text-xs text-slate-600">
          <span class="font-semibold">HDRezka</span>
          <select
            v-model="draft.REZKA_ENABLED"
            class="rounded-md border border-slate-300 px-2 py-1.5 text-xs"
            data-testid="credentials-rezka-enabled"
          >
            <option value="true">Включено</option>
            <option value="false">Скрыто</option>
          </select>
        </label>
        <label class="flex flex-col gap-1 text-xs text-slate-600">
          <span class="font-semibold">Kinohub</span>
          <select
            v-model="draft.KINOHUB_ENABLED"
            class="rounded-md border border-slate-300 px-2 py-1.5 text-xs"
            data-testid="credentials-kinohub-enabled"
          >
            <option value="true">Включено</option>
            <option value="false">Скрыто</option>
          </select>
        </label>
      </div>
    </div>

    <div class="mt-4 border-t border-slate-100 pt-4">
      <h4 class="text-xs font-semibold uppercase tracking-wide text-slate-500">
        Kino.pub
      </h4>
      <div class="mt-3 grid gap-3 sm:grid-cols-3">
        <label class="flex flex-col gap-1 text-xs text-slate-600">
          <span class="font-semibold">Статус</span>
          <select
            v-model="draft.KINOPUB_ENABLED"
            class="rounded-md border border-slate-300 px-2 py-1.5 text-xs"
            data-testid="credentials-kinopub-enabled"
          >
            <option value="true">Включено</option>
            <option value="false">Скрыто</option>
          </select>
        </label>
        <label class="flex flex-col gap-1 text-xs text-slate-600">
          <span class="font-semibold">Client ID (опционально)</span>
          <input
            v-model="draft.KINOPUB_CLIENT_ID"
            class="rounded-md border border-slate-300 px-2 py-1.5 text-xs"
            data-testid="credentials-kinopub-client-id"
            :placeholder="status.KINOPUB_CLIENT_ID.configured ? 'Сейчас сохранён' : 'По умолчанию: xbmc'"
          />
        </label>
        <label class="flex flex-col gap-1 text-xs text-slate-600">
          <span class="font-semibold">Client Secret (опционально)</span>
          <input
            v-model="draft.KINOPUB_CLIENT_SECRET"
            class="rounded-md border border-slate-300 px-2 py-1.5 text-xs"
            data-testid="credentials-kinopub-client-secret"
            type="password"
            :placeholder="status.KINOPUB_CLIENT_SECRET.configured ? 'Сейчас сохранён' : 'Секретный ключ'"
          />
        </label>
      </div>
    </div>

    <div class="mt-4 flex flex-wrap items-center gap-2">
      <button
        type="button"
        class="inline-flex items-center justify-center rounded-md bg-slate-800 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-900 disabled:opacity-50"
        data-testid="credentials-save"
        :disabled="loading || saving"
        @click="saveCredentials"
      >
        {{ saving ? 'Сохранение…' : 'Сохранить настройки' }}
      </button>
      <button
        type="button"
        class="inline-flex items-center justify-center rounded-md bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-200 disabled:opacity-50"
        data-testid="credentials-refresh"
        :disabled="loading || saving"
        @click="loadCredentials(true)"
      >
        {{ loading ? 'Проверка…' : 'Обновить статус' }}
      </button>
      <span
        v-if="success"
        class="text-xs font-semibold text-emerald-700"
        data-testid="credentials-success"
      >
        {{ success }}
      </span>
      <span
        v-if="error"
        class="text-xs font-semibold text-red-700"
        data-testid="credentials-error"
      >
        {{ error }}
      </span>
    </div>
  </section>
</template>
