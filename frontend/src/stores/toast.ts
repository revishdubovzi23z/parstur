// ROADMAP Stage 10.7a — toast notifications.
//
// Pinia replacement for the legacy `showToast(msg)` method on the root
// Vue instance in `index.html` (~line 2299). A single message slot,
// auto-dismissed after `defaultDurationMs`, surfaced by
// `<ToastContainer>` in `AppShell`. Toasts are intentionally minimal:
// one line of text, one of three tones, no action buttons. Anything
// richer should be modeled as its own modal.

import { defineStore } from 'pinia'

export type ToastTone = 'info' | 'success' | 'error'

export interface Toast {
  /** Monotonically increasing id so concurrent toasts replace cleanly. */
  id: number
  message: string
  tone: ToastTone
}

interface ToastStoreState {
  current: Toast | null
  defaultDurationMs: number
  nextId: number
}

export const useToastStore = defineStore('toast', {
  state: (): ToastStoreState => ({
    current: null,
    defaultDurationMs: 3000,
    nextId: 1,
  }),

  actions: {
    /**
     * Replace the current toast with a new one. The pending timer for
     * the previous toast is cleared so the new message gets its full
     * display window.
     */
    show(message: string, tone: ToastTone = 'info', durationMs?: number): void {
      const id = this.nextId++
      this.current = { id, message, tone }
      const ms = durationMs ?? this.defaultDurationMs
      if (ms > 0 && typeof window !== 'undefined') {
        window.setTimeout(() => {
          // Only dismiss if it's still the same toast — a newer one
          // would have its own timer.
          if (this.current?.id === id) {
            this.current = null
          }
        }, ms)
      }
    },

    success(message: string, durationMs?: number): void {
      this.show(message, 'success', durationMs)
    },

    error(message: string, durationMs?: number): void {
      this.show(message, 'error', durationMs)
    },

    dismiss(): void {
      this.current = null
    },
  },
})
