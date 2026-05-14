// ROADMAP Stage 10.3 — LoginModal component tests.
//
// The modal is a thin shell around `useSessionStore.login()`. These
// tests cover the visibility gate (only shown when `needsLogin`),
// the submit flow (calls store, closes on success), and the error
// branch (shows the localized message from the store).

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

import LoginModal from './LoginModal.vue'
import { useSessionStore } from '../stores/session'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

describe('LoginModal.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('is hidden while session.status is unknown / disabled / authenticated', async () => {
    const wrapper = mount(LoginModal)
    const session = useSessionStore()

    // unknown — initial state, no init() called yet
    expect(wrapper.find('[data-testid="login-modal"]').exists()).toBe(false)

    session.$patch({ status: 'disabled' })
    await flushPromises()
    expect(wrapper.find('[data-testid="login-modal"]').exists()).toBe(false)

    session.$patch({ status: 'authenticated' })
    await flushPromises()
    expect(wrapper.find('[data-testid="login-modal"]').exists()).toBe(false)
  })

  it('appears when the store flips to unauthenticated', async () => {
    const wrapper = mount(LoginModal)
    const session = useSessionStore()

    session.$patch({ status: 'unauthenticated' })
    await flushPromises()
    expect(wrapper.find('[data-testid="login-modal"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="login-username"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="login-password"]').exists()).toBe(true)
  })

  it('submits credentials and closes on successful login', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ token: 'good-token', auth_enabled: true }),
    )

    const wrapper = mount(LoginModal, {
      attachTo: document.body,
    })
    const session = useSessionStore()
    session.$patch({ status: 'unauthenticated' })
    await flushPromises()

    await wrapper.find('[data-testid="login-username"]').setValue('admin')
    await wrapper.find('[data-testid="login-password"]').setValue('s3cret')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    // /api/login was hit with the right body
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
    const [url, init] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(url).toBe('/api/login')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({
      username: 'admin',
      password: 's3cret',
    })

    // Store flipped to authenticated → modal hides itself
    expect(session.status).toBe('authenticated')
    expect(session.token).toBe('good-token')
    expect(wrapper.find('[data-testid="login-modal"]').exists()).toBe(false)

    wrapper.unmount()
  })

  it('surfaces the localized error from the store on 401', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('Unauthorized', { status: 401 }),
    )

    const wrapper = mount(LoginModal)
    const session = useSessionStore()
    session.$patch({ status: 'unauthenticated' })
    await flushPromises()

    await wrapper.find('[data-testid="login-username"]').setValue('admin')
    await wrapper.find('[data-testid="login-password"]').setValue('wrong')
    await wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    expect(session.status).toBe('unauthenticated')
    const errBanner = wrapper.find('[data-testid="login-error"]')
    expect(errBanner.exists()).toBe(true)
    expect(errBanner.text()).toBe('Неверный логин или пароль')
    // Modal stays open for retry
    expect(wrapper.find('[data-testid="login-modal"]').exists()).toBe(true)
  })

  it('focuses password field on Enter in username WITHOUT submitting the form', async () => {
    // Regression test against the live-test finding: with `@keyup.enter`,
    // the browser's implicit "submit form on Enter in a single text
    // input" fired before the handler, submitting the form with an
    // empty password and producing a spurious 401. Fix uses
    // `@keydown.enter` + `event.preventDefault()`.
    const wrapper = mount(LoginModal, { attachTo: document.body })
    const session = useSessionStore()
    session.$patch({ status: 'unauthenticated' })
    await flushPromises()

    const userInput = wrapper.find('[data-testid="login-username"]')
    await userInput.setValue('admin')
    await userInput.trigger('keydown', { key: 'Enter' })
    await flushPromises()

    // Should NOT have triggered a fetch (no submit).
    expect(globalThis.fetch).not.toHaveBeenCalled()
    // Password input should now have focus.
    const passEl = wrapper.find('[data-testid="login-password"]')
      .element as HTMLInputElement
    expect(document.activeElement).toBe(passEl)
    // Modal still open, no error banner.
    expect(wrapper.find('[data-testid="login-modal"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="login-error"]').exists()).toBe(false)

    wrapper.unmount()
  })

  it('disables inputs and shows loading state while submitting', async () => {
    let resolveLogin!: (value: Response) => void
    const pending = new Promise<Response>((resolve) => {
      resolveLogin = resolve
    })
    vi.mocked(globalThis.fetch).mockReturnValueOnce(pending)

    const wrapper = mount(LoginModal)
    const session = useSessionStore()
    session.$patch({ status: 'unauthenticated' })
    await flushPromises()

    await wrapper.find('[data-testid="login-username"]').setValue('admin')
    await wrapper.find('[data-testid="login-password"]').setValue('pw')
    wrapper.find('form').trigger('submit.prevent')
    await flushPromises()

    const submit = wrapper.find('[data-testid="login-submit"]')
    expect(submit.attributes('disabled')).toBeDefined()
    expect(submit.text()).toBe('Входим…')
    expect(
      (wrapper.find('[data-testid="login-username"]').element as HTMLInputElement)
        .disabled,
    ).toBe(true)

    resolveLogin(mockJson({ token: 'tok', auth_enabled: true }))
    await flushPromises()
  })
})
