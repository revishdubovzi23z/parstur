// ROADMAP Stage 10.7c — StatsPanel modal component tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import StatsPanel from './StatsPanel.vue'
import { useStatsStore } from '../stores/stats'
import { useSessionStore } from '../stores/session'

function mockJson(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
}

function setup(): { stats: ReturnType<typeof useStatsStore> } {
  setActivePinia(createPinia())
  const session = useSessionStore()
  session.$patch({ status: 'disabled' })
  return { stats: useStatsStore() }
}

const STATS = {
  total_video: 100,
  no_poster: 4,
  no_ratings: 2,
  no_rezka: 1,
  no_ids: 0,
  last_runs: {},
}

const HISTORY = [
  {
    id: 11,
    job_type: 'sync_video',
    start_time: '2024-05-10 12:00:00',
    end_time: '2024-05-10 12:01:30',
    duration: 90,
    items_processed: 50,
    total_items: 50,
    status: 'completed',
  },
  {
    id: 12,
    job_type: 'rezka',
    start_time: '2024-05-09 09:00:00',
    end_time: '2024-05-09 09:00:30',
    duration: 30,
    items_processed: 5,
    total_items: 10,
    status: 'error',
  },
]

describe('StatsPanel.vue', () => {
  beforeEach(() => {
    vi.spyOn(globalThis, 'fetch')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders nothing when open=false', () => {
    setup()
    const wrapper = mount(StatsPanel, { props: { open: false } })
    expect(wrapper.find('[data-testid="stats-panel"]').exists()).toBe(false)
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('fetches stats and job history on open and renders the four tiles', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson(STATS))
      .mockResolvedValueOnce(mockJson(HISTORY))
    setup()
    const wrapper = mount(StatsPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()

    expect(wrapper.find('[data-testid="stats-tile-total"]').text()).toContain('100')
    expect(wrapper.find('[data-testid="stats-tile-no-poster"]').text()).toContain('4')
    expect(wrapper.find('[data-testid="stats-tile-no-ratings"]').text()).toContain('2')
    expect(wrapper.find('[data-testid="stats-tile-no-rezka"]').text()).toContain('1')
  })

  it('renders one row per job history entry with translated labels', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson(STATS))
      .mockResolvedValueOnce(mockJson(HISTORY))
    setup()
    const wrapper = mount(StatsPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()

    const rows = wrapper.findAll('[data-testid="stats-panel-history-row"]')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain('Видео')
    expect(rows[0].text()).toContain('Успешно')
    expect(rows[1].text()).toContain('Rezka')
    expect(rows[1].text()).toContain('Ошибка')
  })

  it('shows the "история пуста" placeholder when no jobs are returned', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson(STATS))
      .mockResolvedValueOnce(mockJson([]))
    setup()
    const wrapper = mount(StatsPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()

    expect(wrapper.find('[data-testid="stats-panel-history-empty"]').exists()).toBe(
      true,
    )
  })

  it('renders the enrichment banner only when a counter is positive', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson(STATS))
      .mockResolvedValueOnce(mockJson([]))
    setup()
    const wrapper = mount(StatsPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    expect(wrapper.find('[data-testid="stats-panel-backlog"]').exists()).toBe(true)
    expect(
      wrapper.find('[data-testid="stats-panel-run-full-pipeline"]').exists(),
    ).toBe(true)
  })

  it('emits run-full-pipeline when the banner CTA is clicked', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson(STATS))
      .mockResolvedValueOnce(mockJson([]))
    setup()
    const wrapper = mount(StatsPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    await wrapper
      .find('[data-testid="stats-panel-run-full-pipeline"]')
      .trigger('click')
    expect(wrapper.emitted('run-full-pipeline')).toHaveLength(1)
  })

  it('hides the enrichment banner when all backlog counters are zero', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(
        mockJson({
          total_video: 1,
          no_poster: 0,
          no_ratings: 0,
          no_rezka: 0,
          no_ids: 0,
          last_runs: {},
        }),
      )
      .mockResolvedValueOnce(mockJson([]))
    setup()
    const wrapper = mount(StatsPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    expect(wrapper.find('[data-testid="stats-panel-backlog"]').exists()).toBe(false)
  })

  it('surfaces the error banner when the API fails', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(new Response('boom', { status: 500 }))
      .mockResolvedValueOnce(mockJson([]))
    setup()
    const wrapper = mount(StatsPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()

    expect(wrapper.find('[data-testid="stats-panel-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="stats-panel-error"]').text()).toContain('500')
  })

  it('emits close and resets the store when closed via backdrop or ×', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(mockJson(STATS))
      .mockResolvedValueOnce(mockJson(HISTORY))
    const { stats } = setup()
    const wrapper = mount(StatsPanel, { props: { open: false } })
    await wrapper.setProps({ open: true })
    await flushPromises()
    expect(stats.stats.total_video).toBe(100)

    await wrapper.find('[data-testid="stats-panel-backdrop"]').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)

    await wrapper.find('[data-testid="stats-panel-close"]').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(2)

    await wrapper.setProps({ open: false })
    expect(stats.stats.total_video).toBe(0)
    expect(stats.jobHistory).toEqual([])
  })
})
