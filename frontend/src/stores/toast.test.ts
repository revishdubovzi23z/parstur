// ROADMAP Stage 10.7a — toast store unit tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useToastStore } from './toast'

describe('useToastStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders a toast with default tone info', () => {
    const toast = useToastStore()
    toast.show('hello')
    expect(toast.current).toMatchObject({ message: 'hello', tone: 'info' })
  })

  it('replaces the active toast with the new one', () => {
    const toast = useToastStore()
    toast.show('first')
    const firstId = toast.current!.id
    toast.show('second')
    expect(toast.current?.message).toBe('second')
    expect(toast.current?.id).not.toBe(firstId)
  })

  it('auto-dismisses after the default duration', () => {
    const toast = useToastStore()
    toast.show('bye')
    expect(toast.current).not.toBeNull()
    vi.advanceTimersByTime(toast.defaultDurationMs - 1)
    expect(toast.current).not.toBeNull()
    vi.advanceTimersByTime(1)
    expect(toast.current).toBeNull()
  })

  it('does not auto-dismiss a newer toast when an older timer fires', () => {
    const toast = useToastStore()
    toast.success('first', 1000)
    vi.advanceTimersByTime(500)
    toast.error('second', 2000)
    vi.advanceTimersByTime(500)
    // First toast's timer fires, but `current` is now the second toast,
    // so nothing should change.
    expect(toast.current?.message).toBe('second')
    vi.advanceTimersByTime(1500)
    expect(toast.current).toBeNull()
  })

  it('exposes success/error shortcuts that set the tone', () => {
    const toast = useToastStore()
    toast.success('ok')
    expect(toast.current?.tone).toBe('success')
    toast.error('boom')
    expect(toast.current?.tone).toBe('error')
  })

  it('dismiss() clears the current toast immediately', () => {
    const toast = useToastStore()
    toast.show('hi')
    toast.dismiss()
    expect(toast.current).toBeNull()
  })
})
