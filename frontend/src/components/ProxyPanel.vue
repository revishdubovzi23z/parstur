<script setup lang="ts">
// Stage 14 — 🔒 Per-service outbound proxy panel.
//
// Each upstream source can be routed through its own SOCKS5/HTTP proxy
// or vless:// link. Credentials live under PROXY_<SERVICE> + XRAY_BINARY
// (/api/settings/credentials); connectivity tests hit /api/proxy/*.

import { onMounted, reactive, ref, watch } from 'vue'

import { apiFetch, UnauthorizedError } from '../api/client'
import { useSessionStore } from '../stores/session'

const props = defineProps<{ visible: boolean }>()

const session = useSessionStore()
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const success = ref('')
const loaded = ref(false)

const SERVICES = [
  { key: 'PROXY_REZKA', service: 'rezka', label: 'Rezka' },
  { key: 'PROXY_KINOPUB', service: 'kinopub', label: 'Kinopub' },
  { key: 'PROXY_RUTOR', service: 'rutor', label: 'Rutor' },
  { key: 'PROXY_TMDB', service: 'tmdb', label: 'TMDB' },
  { key: 'PROXY_KINOPOISK', service: 'kinopoisk', label: 'Kinopoisk' },
  { key: 'PROXY_POISKKINO', service: 'poiskkino', label: 'PoiskKino' },
] as const

const draft = reactive<Record<string, string>>({
  PROXY_REZKA: '',
  PROXY_KINOPUB: '',
  PROXY_RUTOR: '',
  PROXY_TMDB: '',
  PROXY_KINOPOISK: '',
  PROXY_POISKKINO: '',
  XRAY_BINARY: 'xray',
})

const testResults = reactive<Record<string, any>>({})
const testing = reactive<Record<string, boolean>>({})
const status = reactive<Record<string, any>>({ xray_binary: null, services: {} })

function applyCredentials(data: any): void {
  const c = data?.credentials
  if (!c) return
  for (const { key } of SERVICES) {
    if (c[key]) draft[key] = c[key].value || ''
  }
  if (c.XRAY_BINARY) draft.XRAY_BINARY = c.XRAY_BINARY.value || 'xray'
}

async function loadStatus(): Promise<void> {
  try {
    const res = await apiFetch('/api/proxy/status')
    if (res.ok) Object.assign(status, await res.json())
  } catch (err) {
    if (err instanceof UnauthorizedError) session.handleUnauthorized(err)
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
    await loadStatus()
  } catch (err) {
    if (err instanceof UnauthorizedError) session.handleUnauthorized(err)
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
  const values: Record<string, string> = { XRAY_BINARY: draft.XRAY_BINARY.trim() }
  for (const { key } of SERVICES) values[key] = draft[key].trim()
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
    success.value = 'Настройки прокси сохранены'
    await loadStatus()
  } catch (err) {
    if (err instanceof UnauthorizedError) session.handleUnauthorized(err)
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    saving.value = false
  }
}

async function testProxy(service: string): Promise<void> {
  if (!session.canCallApi) return
  testing[service] = true
  delete testResults[service]
  try {
    const res = await apiFetch(`/api/proxy/test/${service}`, { method: 'POST' })
    testResults[service] = await res.json().catch(() => ({ ok: false, error: `HTTP ${res.status}` }))
  } catch (err) {
    if (err instanceof UnauthorizedError) session.handleUnauthorized(err)
    testResults[service] = { ok: false, error: err instanceof Error ? err.message : String(err) }
  } finally {
    testing[service] = false
  }
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
  <section class="rounded-lg border border-slate-200 p-4" data-testid="proxy-panel">
    <div class="flex items-start justify-between gap-3">
      <div>
        <h3 class="text-sm font-semibold text-slate-900">🔒 Прокси для источников</h3>
        <p class="mt-1 text-xs text-slate-500">
          Отдельный прокси на каждый источник. Поддерживаются <code>socks5://</code>, <code>http://</code> и <code>vless://</code> (через Xray/sing-box).
        </p>
      </div>
    </div>

    <div class="mt-4 space-y-2">
      <div
        v-for="svc in SERVICES"
        :key="svc.service"
        class="flex flex-col gap-1"
      >
        <label class="flex flex-col gap-1 text-xs text-slate-600">
          <span class="font-semibold" v-text="svc.label"></span>
          <div class="flex items-center gap-1.5">
            <input
              v-model="draft[svc.key]"
              class="flex-1 min-w-0 rounded-md border border-slate-300 px-2 py-1.5 text-xs font-mono"
              placeholder="socks5://host:port  ·  vless://...  ·  пусто = напрямую"
              :data-testid="`proxy-input-${svc.service}`"
            />
            <button
              type="button"
              class="flex-shrink-0 inline-flex items-center justify-center rounded-md bg-teal-600 px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-teal-700 disabled:opacity-50"
              :disabled="testing[svc.service] || !draft[svc.key].trim()"
              :data-testid="`proxy-test-${svc.service}`"
              @click="testProxy(svc.service)"
              v-text="testing[svc.service] ? '...' : 'Проверить'"
            ></button>
          </div>
        </label>
        <p
          v-if="testResults[svc.service]"
          :class="[
            'text-[10px] font-semibold',
            testResults[svc.service].ok ? 'text-emerald-700' : 'text-red-700',
          ]"
        >
          <span v-if="testResults[svc.service].ok" v-text="'✓ IP ' + testResults[svc.service].ip + ' · ' + testResults[svc.service].latency_ms + ' мс'"></span>
          <span v-else v-text="'✗ ' + testResults[svc.service].error"></span>
        </p>
      </div>
    </div>

    <div class="mt-4 border-t border-slate-100 pt-4">
      <label class="flex flex-col gap-1 text-xs text-slate-600">
        <span class="font-semibold">Путь к Xray / sing-box (для vless://)</span>
        <input
          v-model="draft.XRAY_BINARY"
          class="rounded-md border border-slate-300 px-2 py-1.5 text-xs font-mono"
          placeholder="xray"
          data-testid="proxy-xray-binary"
        />
      </label>
      <p class="mt-1 text-[10px] text-slate-400">
        Обнаружено: <code v-text="status.xray_binary || 'не найдено'"></code>. Для vless:// требуется установленный бинарник xray-core.
      </p>
    </div>

    <div class="mt-4 flex flex-wrap items-center gap-2">
      <button
        type="button"
        class="inline-flex items-center justify-center rounded-md bg-slate-800 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-900 disabled:opacity-50"
        :disabled="loading || saving"
        @click="saveSettings"
      >
        Сохранить
      </button>
      <span v-if="success" class="text-xs font-semibold text-emerald-700" v-text="success"></span>
      <span v-if="error" class="text-xs font-semibold text-red-700" v-text="error"></span>
    </div>
  </section>
</template>
