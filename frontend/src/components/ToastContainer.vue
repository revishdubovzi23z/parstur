<script setup lang="ts">
// ROADMAP Stage 10.7a — toast UI host.
//
// Renders a single floating toast in the bottom-right corner. Toggled
// by `useToastStore`. Click to dismiss early.

import { computed } from 'vue'
import { useToastStore } from '../stores/toast'

const toast = useToastStore()

const toneClass = computed(() => {
  switch (toast.current?.tone) {
    case 'success':
      return 'bg-emerald-600 text-white'
    case 'error':
      return 'bg-red-600 text-white'
    case 'info':
    default:
      return 'bg-slate-900 text-white'
  }
})

function onDismiss(): void {
  toast.dismiss()
}
</script>

<template>
  <Transition
    enter-from-class="opacity-0 translate-y-2"
    enter-active-class="transition duration-150 ease-out"
    enter-to-class="opacity-100 translate-y-0"
    leave-from-class="opacity-100 translate-y-0"
    leave-active-class="transition duration-150 ease-in"
    leave-to-class="opacity-0 translate-y-2"
  >
    <button
      v-if="toast.current"
      type="button"
      class="fixed bottom-4 right-4 z-[60] max-w-sm rounded-lg px-4 py-2 text-sm shadow-lg"
      :class="toneClass"
      data-testid="toast"
      @click="onDismiss"
    >
      {{ toast.current.message }}
    </button>
  </Transition>
</template>
