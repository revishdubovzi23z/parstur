// ROADMAP Stage 10.6 — admin panel modal tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import AdminPanel from './AdminPanel.vue'
import { useAdminStore } from '../stores/admin'
import { useSessionStore } from '../stores/session'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function setup(): { admin: ReturnType<typeof useAdminStore> } {
  setActivePinia(createPinia())
  const session = useSessionStore()
  session.$patch({ status: 'disabled' })
  const admin = useAdminStore()
  return { admin }
}

/** Mount AdminPanel with credential/auth subpanels stubbed out. These
 * sections have their own test files; stubbing them here keeps the
 * AdminPanel suite focused on its own buttons and prevents auto-fetch
 * calls from consuming queued `mockResolvedValueOnce` values. */
function mountPanel(open = true) {
  return mount(AdminPanel, {
    props: { open },
    global: { stubs: { KinopubAuthPanel: true, CredentialSettingsPanel: true } },
  })
}

describe('AdminPanel.vue', () => {
  beforeEach(() => {
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders nothing when open=false', () => {
    setup()
    const wrapper = mountPanel(false)
    expect(wrapper.find('[data-testid="admin-panel"]').exists()).toBe(false)
  })

  it('emits close when backdrop or × is clicked', async () => {
    setup()
    const wrapper = mountPanel()

    await wrapper.find('[data-testid="admin-panel-backdrop"]').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)

    await wrapper.find('[data-testid="admin-panel-close"]').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(2)
  })

  it('runs self-update and emits restart-triggered on success', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'updated', message: 'ok' }),
    )
    const { admin } = setup()
    const wrapper = mountPanel()

    await wrapper.find('[data-testid="admin-self-update"]').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('restart-triggered')).toHaveLength(1)
    expect(admin.lastResult?.tone).toBe('success')
    expect(wrapper.find('[data-testid="admin-panel-result"]').text()).toBe(
      'ok',
    )
  })

  it('does not emit restart-triggered when self-update is a no-op', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'up_to_date' }),
    )
    setup()
    const wrapper = mountPanel()

    await wrapper.find('[data-testid="admin-self-update"]').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('restart-triggered')).toBeUndefined()
  })

  it('asks for two confirmations before resetting the DB', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson({ token: 'abc' }))
      .mockResolvedValueOnce(mockJson({ status: 'success' }))

    setup()
    const wrapper = mountPanel()
    await wrapper.find('[data-testid="admin-db-reset"]').trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalledTimes(2)
    expect(wrapper.emitted('restart-triggered')).toHaveLength(1)
  })

  it('aborts reset on the first confirmation reject', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    setup()
    const wrapper = mountPanel()
    await wrapper.find('[data-testid="admin-db-reset"]').trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('forwards the picked file to importDatabase after confirming', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      mockJson({ status: 'success', message: 'imported' }),
    )
    setup()
    const wrapper = mountPanel()

    const file = new File([new Uint8Array([1, 2])], 'a.db', {
      type: 'application/x-sqlite3',
    })
    const input = wrapper.find(
      '[data-testid="admin-db-import-input"]',
    ).element as HTMLInputElement
    Object.defineProperty(input, 'files', { value: [file] })
    await wrapper
      .find('[data-testid="admin-db-import-input"]')
      .trigger('change')
    await flushPromises()

    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/database_import',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(wrapper.emitted('restart-triggered')).toHaveLength(1)
  })

  it('does not call importDatabase when the user cancels confirm', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    setup()
    const wrapper = mountPanel()
    const file = new File([new Uint8Array([1])], 'a.db')
    const input = wrapper.find(
      '[data-testid="admin-db-import-input"]',
    ).element as HTMLInputElement
    Object.defineProperty(input, 'files', { value: [file] })
    await wrapper
      .find('[data-testid="admin-db-import-input"]')
      .trigger('change')
    await flushPromises()
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('clears the result banner when the modal is closed', async () => {
    const { admin } = setup()
    const wrapper = mountPanel()
    admin.lastResult = { tone: 'info', message: 'still here', willRestart: false }
    await wrapper.setProps({ open: false })
    expect(admin.lastResult).toBeNull()
  })

  it('triggers admin.downloadBackup() when "↓ Бэкап" is clicked', async () => {
    const { admin } = setup()
    const spy = vi
      .spyOn(admin, 'downloadBackup')
      .mockResolvedValue({ tone: 'success', message: 'ok', willRestart: false })
    const wrapper = mountPanel()
    await wrapper.find('[data-testid="admin-backup-download"]').trigger('click')
    await flushPromises()
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('triggers admin.exportItems() with the picked format and category_id', async () => {
    const { admin } = setup()
    const spy = vi
      .spyOn(admin, 'exportItems')
      .mockResolvedValue({ tone: 'success', message: 'ok', willRestart: false })
    const wrapper = mount(AdminPanel, { props: { open: true } })

    await wrapper
      .find('[data-testid="admin-items-export-fmt"]')
      .setValue('csv')
    await wrapper
      .find('[data-testid="admin-items-export-category"]')
      .setValue('5')
    await wrapper.find('[data-testid="admin-items-export"]').trigger('click')
    await flushPromises()
    expect(spy).toHaveBeenCalledWith('csv', 5)
  })

  it('asks for confirmation before rebuilding FTS and forwards the call', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { admin } = setup()
    const spy = vi
      .spyOn(admin, 'rebuildFts')
      .mockResolvedValue({ tone: 'success', message: 'ok', willRestart: false })
    const wrapper = mount(AdminPanel, { props: { open: true } })

    await wrapper.find('[data-testid="admin-rebuild-fts"]').trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(spy).toHaveBeenCalledTimes(1)
  })

  it('skips rebuildFts when the user cancels the confirm', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const { admin } = setup()
    const spy = vi.spyOn(admin, 'rebuildFts')
    const wrapper = mount(AdminPanel, { props: { open: true } })
    await wrapper.find('[data-testid="admin-rebuild-fts"]').trigger('click')
    await flushPromises()
    expect(spy).not.toHaveBeenCalled()
  })
})
