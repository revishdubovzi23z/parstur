import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import CredentialSettingsPanel from './CredentialSettingsPanel.vue'
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

const STATUS = {
  credentials: {
    REZKA_EMAIL: { configured: true, value: 'user@example.com' },
    REZKA_PASSWORD: { configured: true, value: '' },
    KINOPOISK_API_KEY: { configured: false, value: '' },
    POISKKINO_API_KEY: { configured: false, value: '' },
    TMDB_API_KEY: { configured: false, value: '' },
    TMDB_API_TOKEN: { configured: true, value: '' },
  },
}

describe('CredentialSettingsPanel.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('loads credential status without exposing stored secrets', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(mockJson(STATUS))
    authorise()

    const wrapper = mount(CredentialSettingsPanel, { props: { visible: true } })
    await flushPromises()

    expect(wrapper.find('[data-testid="credential-settings-panel"]').text()).toContain(
      '3/6 задано',
    )
    expect(
      (wrapper.find('[data-testid="credentials-rezka-email"]').element as HTMLInputElement)
        .value,
    ).toBe('user@example.com')
    expect(
      (
        wrapper.find('[data-testid="credentials-rezka-password"]')
          .element as HTMLInputElement
      ).value,
    ).toBe('')
  })

  it('saves edited Rezka/API values', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson(STATUS))
      .mockResolvedValueOnce(
        mockJson({
          status: 'success',
          credentials: {
            ...STATUS.credentials,
            REZKA_EMAIL: { configured: true, value: 'new@example.com' },
            REZKA_PASSWORD: { configured: true, value: '' },
          },
        }),
      )
    authorise()
    const wrapper = mount(CredentialSettingsPanel, { props: { visible: true } })
    await flushPromises()

    await wrapper
      .find('[data-testid="credentials-rezka-email"]')
      .setValue('new@example.com')
    await wrapper.find('[data-testid="credentials-rezka-password"]').setValue('secret')
    await wrapper.find('[data-testid="credentials-save"]').trigger('click')
    await flushPromises()

    expect(globalThis.fetch).toHaveBeenLastCalledWith(
      '/api/settings/credentials',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({
          values: {
            REZKA_EMAIL: 'new@example.com',
            REZKA_PASSWORD: 'secret',
          },
        }),
      }),
    )
    expect(wrapper.find('[data-testid="credentials-success"]').text()).toContain(
      'Настройки сохранены',
    )
  })
})
