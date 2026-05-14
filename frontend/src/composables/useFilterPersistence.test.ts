// ROADMAP Stage 10.7a — filter persistence composable tests.

import { setActivePinia, createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { effectScope, nextTick } from 'vue'

import {
  FILTER_STORAGE_KEYS,
  attachFilterPersistence,
  loadPersistedFilters,
} from './useFilterPersistence'
import { useFeedStore } from '../stores/feed'
import { DEFAULT_FEED_FILTERS } from '../types/feed'

describe('useFilterPersistence', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    window.sessionStorage.clear()
  })

  afterEach(() => {
    window.sessionStorage.clear()
  })

  describe('loadPersistedFilters', () => {
    it('returns defaults when nothing is stored', () => {
      expect(loadPersistedFilters()).toEqual(DEFAULT_FEED_FILTERS)
    })

    it('parses each key including null / blank sentinels', () => {
      window.sessionStorage.setItem(FILTER_STORAGE_KEYS.categoryId, '2')
      window.sessionStorage.setItem(FILTER_STORAGE_KEYS.collectionId, '7')
      window.sessionStorage.setItem(FILTER_STORAGE_KEYS.search, 'matrix')
      window.sessionStorage.setItem(FILTER_STORAGE_KEYS.minYear, '1990')
      window.sessionStorage.setItem(FILTER_STORAGE_KEYS.maxYear, 'null')
      window.sessionStorage.setItem(FILTER_STORAGE_KEYS.minDate, '2024-01-15')
      window.sessionStorage.setItem(FILTER_STORAGE_KEYS.maxDate, '')
      window.sessionStorage.setItem(FILTER_STORAGE_KEYS.minKp, '6.5')
      window.sessionStorage.setItem(FILTER_STORAGE_KEYS.hideRated, 'true')

      const filters = loadPersistedFilters()
      expect(filters.categoryId).toBe(2)
      expect(filters.collectionId).toBe(7)
      expect(filters.search).toBe('matrix')
      expect(filters.minYear).toBe(1990)
      expect(filters.maxYear).toBeNull()
      expect(filters.minDate).toBe('2024-01-15')
      expect(filters.maxDate).toBeNull()
      expect(filters.minKp).toBe(6.5)
      expect(filters.hideRated).toBe(true)
    })
  })

  describe('attachFilterPersistence', () => {
    it('hydrates the feed store from sessionStorage on attach', () => {
      window.sessionStorage.setItem(FILTER_STORAGE_KEYS.search, 'inception')
      window.sessionStorage.setItem(FILTER_STORAGE_KEYS.page, '3')

      const scope = effectScope()
      let detach: (() => void) | undefined
      scope.run(() => {
        detach = attachFilterPersistence()
      })

      const feed = useFeedStore()
      expect(feed.filters.search).toBe('inception')
      expect(feed.page).toBe(3)

      detach?.()
      scope.stop()
    })

    it('writes back to sessionStorage when filters mutate', async () => {
      const scope = effectScope()
      let detach: (() => void) | undefined
      scope.run(() => {
        detach = attachFilterPersistence()
      })
      const feed = useFeedStore()
      feed.filters.search = 'tenet'
      feed.filters.minYear = 2020
      feed.filters.maxDate = '2024-12-31'
      feed.filters.hideCollected = true
      feed.page = 4
      await nextTick()

      expect(window.sessionStorage.getItem(FILTER_STORAGE_KEYS.search)).toBe('tenet')
      expect(window.sessionStorage.getItem(FILTER_STORAGE_KEYS.minYear)).toBe('2020')
      expect(window.sessionStorage.getItem(FILTER_STORAGE_KEYS.maxDate)).toBe(
        '2024-12-31',
      )
      expect(
        window.sessionStorage.getItem(FILTER_STORAGE_KEYS.hideCollected),
      ).toBe('true')
      expect(window.sessionStorage.getItem(FILTER_STORAGE_KEYS.page)).toBe('4')

      detach?.()
      scope.stop()
    })

    it('removes the key when a nullable field is cleared', async () => {
      window.sessionStorage.setItem(FILTER_STORAGE_KEYS.minYear, '1990')
      const scope = effectScope()
      let detach: (() => void) | undefined
      scope.run(() => {
        detach = attachFilterPersistence()
      })
      const feed = useFeedStore()
      feed.filters.minYear = null
      await nextTick()
      expect(window.sessionStorage.getItem(FILTER_STORAGE_KEYS.minYear)).toBeNull()
      detach?.()
      scope.stop()
    })

    it('stops writing back after detach is called', async () => {
      const scope = effectScope()
      let detach: (() => void) | undefined
      scope.run(() => {
        detach = attachFilterPersistence()
      })
      const feed = useFeedStore()
      feed.filters.search = 'first'
      await nextTick()
      expect(window.sessionStorage.getItem(FILTER_STORAGE_KEYS.search)).toBe('first')
      detach?.()
      feed.filters.search = 'second'
      await nextTick()
      expect(window.sessionStorage.getItem(FILTER_STORAGE_KEYS.search)).toBe('first')
      scope.stop()
    })
  })
})
