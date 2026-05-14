<script setup lang="ts">
// ROADMAP Stage 10.3 — login modal.
//
// Mirrors the legacy modal that lives in `index.html` lines 48–58 in
// behaviour and markup: full-viewport dim overlay, centred card,
// username + password inputs, red-tinted error banner above the form,
// dark CTA button. Enter on the username focuses the password input;
// Enter on the password submits.
//
// All state lives in `useSessionStore` so the modal stays stateless
// across remounts and the `/beta` ↔ legacy `/` token-sharing remains
// transparent: a successful login here writes `authToken` into the
// exact same sessionStorage key the legacy index.html reads from.
import { computed, nextTick, ref, watch } from 'vue'
import { useSessionStore } from '../stores/session'

const session = useSessionStore()

const username = ref('')
const password = ref('')
const passInput = ref<HTMLInputElement | null>(null)
const userInput = ref<HTMLInputElement | null>(null)
const submitting = ref(false)

const visible = computed(() => session.needsLogin)

// Auto-focus the username input the moment the modal becomes visible.
// Using `watch(visible)` (instead of `onMounted`) covers both the
// "modal opens because /api/auth_status finished" case and the
// "modal reopens after a 401 from a stale token" case.
watch(
  visible,
  async (open) => {
    if (open) {
      await nextTick()
      userInput.value?.focus()
    } else {
      // Drop any half-typed password from memory when the modal
      // hides — defence-in-depth against accidental snapshots.
      password.value = ''
    }
  },
  { immediate: true },
)

async function submit(): Promise<void> {
  if (submitting.value) return
  submitting.value = true
  try {
    const ok = await session.login(username.value, password.value)
    if (ok) {
      // Clear the form so the next time the modal opens (e.g. after
      // an explicit logout) it doesn't show stale credentials.
      username.value = ''
      password.value = ''
    } else {
      // Move focus back to the password field so the user can retype
      // without reaching for the mouse. Username typically isn't the
      // typo.
      await nextTick()
      passInput.value?.select()
    }
  } finally {
    submitting.value = false
  }
}

function focusPasswordFromUser(event: KeyboardEvent): void {
  // Stop the browser's implicit "submit form on Enter in a text input"
  // before it fires — otherwise the user pressing Enter after typing
  // their username would submit with an empty password and get a
  // spurious 401. We have to intercept on `keydown` rather than
  // `keyup` because the browser dispatches its submit event from
  // `keydown`.
  event.preventDefault()
  passInput.value?.focus()
}
</script>

<template>
  <div
    v-if="visible"
    class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50"
    data-testid="login-modal"
    role="dialog"
    aria-modal="true"
    aria-labelledby="login-modal-title"
  >
    <form
      class="w-full max-w-sm rounded-2xl bg-white p-8 shadow-2xl"
      @submit.prevent="submit"
    >
      <h2 id="login-modal-title" class="mb-1 text-xl font-bold text-slate-900">
        Antigravity Tracker
      </h2>
      <p class="mb-6 text-sm text-slate-500">Введите логин и пароль</p>
      <div
        v-if="session.loginError"
        class="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600"
        data-testid="login-error"
        role="alert"
      >
        {{ session.loginError }}
      </div>
      <input
        ref="userInput"
        v-model="username"
        type="text"
        autocomplete="username"
        placeholder="Логин"
        :disabled="submitting"
        class="mb-3 w-full rounded-lg border border-slate-200 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900 disabled:opacity-60"
        data-testid="login-username"
        @keydown.enter="focusPasswordFromUser"
      />
      <input
        ref="passInput"
        v-model="password"
        type="password"
        autocomplete="current-password"
        placeholder="Пароль"
        :disabled="submitting"
        class="mb-5 w-full rounded-lg border border-slate-200 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900 disabled:opacity-60"
        data-testid="login-password"
      />
      <button
        type="submit"
        :disabled="submitting"
        class="w-full rounded-lg bg-slate-900 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
        data-testid="login-submit"
      >
        <span v-if="submitting">Входим…</span>
        <span v-else>Войти</span>
      </button>
    </form>
  </div>
</template>
