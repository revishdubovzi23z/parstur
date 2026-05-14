import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import KinopubAuthPanel from './KinopubAuthPanel.vue'
import { useSessionStore } from '../stores/session'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function authorise(): void {
  const session = useSessionStore()
  session.$patch({ status: 'disabled' })
}

describe('KinopubAuthPanel.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('shows backend errors when starting Device Flow fails', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(
        mockJson({
          enabled: true,
          authenticated: false,
          expires_at: null,
          expires_in: null,
          client_id: 'xbmc',
        }),
      )
      .mockResolvedValueOnce(
        mockJson({ detail: 'kino.pub disabled' }, { status: 503 }),
      )

    authorise()
    const wrapper = mount(KinopubAuthPanel, { props: { visible: true } })
    await flushPromises()

    await wrapper.find('[data-testid="kinopub-connect"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="kinopub-status-error"]').text()).toContain(
      'kino.pub disabled',
    )
    expect(wrapper.find('[data-testid="kinopub-connect"]').text()).toContain(
      'Подключить kino.pub',
    )
  })
})
