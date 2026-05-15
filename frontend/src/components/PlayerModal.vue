<script setup lang="ts">
// ROADMAP Stage 10.7g — Trailer + HLS stream player modal.
//
// Replaces the legacy stream + trailer modals from `index.html`
// (`showTrailerModal`, `showStreamModal`, lines ~775-960 + 920-960 +
// embedded player JS at ~2514-2566).
//
// State lives in `useItemPlayerStore` (see `stores/player.ts`); this
// component is purely presentational. The hls.js attach is the one
// piece of imperative side-effect — kept inside this component so
// the store stays unit-testable without a real <video> element.

import Hls from 'hls.js'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { useItemPlayerStore } from '../stores/player'

const player = useItemPlayerStore()

/** Active hls.js instance — held in a non-reactive ref so we can
 *  call `.destroy()` on cleanup without round-tripping through
 *  Pinia state. */
const hlsRef = ref<Hls | null>(null)
const videoRef = ref<HTMLVideoElement | null>(null)
/** Tracks the URL currently piped through hls.js so subsequent
 *  watcher fires don't re-attach the same source. */
const attachedUrl = ref<string | null>(null)
const playerError = ref<string | null>(null)

const isOpen = computed(() => player.isOpen)
const mode = computed(() => player.mode)

const trailerKey = computed(() => player.currentTrailerKey)
const trailerSrc = computed(() => {
  const key = trailerKey.value
  if (!key) return ''
  return `https://www.youtube.com/embed/${key}?autoplay=1&rel=0&playsinline=1&enablejsapi=1`
})

const translatorEntries = computed(() => {
  const dict = player.info?.translators ?? {}
  return Object.entries(dict).map(([id, t]) => ({
    id,
    name: t?.name ?? id,
    premium: Boolean(t?.premium),
  }))
})

const seasonEntries = computed(() => {
  const map = player.seasonsMap
  if (!map) return []
  return Object.entries(map).map(([id, name]) => ({ id, name }))
})

const episodeEntries = computed(() => {
  const map = player.episodesMap
  if (!map) return []
  return Object.entries(map).map(([id, name]) => ({ id, name }))
})

/** Subtitle pairs (lang, entry) for `<track>` elements. Only `.vtt`
 *  subs are wired straight through; everything else still works
 *  because `subtitle_proxy` converts SRT → VTT on the backend.
 *  When the active source is kino.pub, swap to the kinopub video's
 *  subtitles[] list instead of the rezka `player.subtitles` dict. */
const subtitleEntries = computed(() => {
  if (player.source === 'kinopub') {
    const subs = player.kinopubVideo?.subtitles ?? []
    return subs
      .filter((s) => Boolean(s.url))
      .map((s, idx) => ({
        lang: s.lang || `sub-${idx}`,
        title: s.lang || `Subtitle ${idx + 1}`,
        src: `/api/subtitle_proxy?url=${encodeURIComponent(s.url)}`,
      }))
  }
  return Object.entries(player.subtitles).map(([lang, sub]) => ({
    lang,
    title: sub.title || lang,
    src: `/api/subtitle_proxy?url=${encodeURIComponent(sub.link)}`,
  }))
})

// PR 5 — kino.pub picker entries. Computed off the active video so
// switching seasons/episodes re-renders the quality and audio lists
// without an explicit reset in PlayerModal.
const kinopubFileEntries = computed(() => {
  const files = player.kinopubVideo?.files ?? []
  return files.map((f, idx) => ({
    idx,
    label: f.quality
      ? `${f.quality}${f.codec ? ` (${f.codec})` : ''}`
      : `Источник ${idx + 1}`,
  }))
})

const kinopubAudioEntries = computed(() => {
  const audios = player.kinopubVideo?.audios ?? []
  return audios.map((a, idx) => {
    const parts = [a.lang, a.author, a.type].filter(Boolean) as string[]
    return {
      idx,
      label: parts.length > 0 ? parts.join(' • ') : `Дорожка ${idx + 1}`,
    }
  })
})

function destroyHls(): void {
  if (hlsRef.value) {
    try {
      hlsRef.value.destroy()
    } catch {
      /* best-effort */
    }
    hlsRef.value = null
  }
  attachedUrl.value = null
  playerError.value = null
}

/** Attach the current `player.streamUrl` to the `<video>` element.
 *  Uses hls.js for `.m3u8` URLs on browsers without native HLS
 *  support; otherwise sets `src` directly so Safari / iOS play
 *  natively. */
async function attachStream(): Promise<void> {
  const url = player.streamUrl
  if (!url) {
    destroyHls()
    return
  }
  if (url === attachedUrl.value) return
  destroyHls()
  await nextTick()
  const video = videoRef.value
  if (!video) return
  attachedUrl.value = url
  playerError.value = null
  if (player.streamIsHls && !video.canPlayType('application/vnd.apple.mpegurl')) {
    if (!Hls.isSupported()) {
      playerError.value = 'Браузер не поддерживает HLS'
      return
    }
    const hls = new Hls()
    hls.loadSource(url)
    hls.attachMedia(video)
    hlsRef.value = hls
  } else {
    video.src = url
  }
}

watch(
  () => player.streamUrl && player.streamConfirmed,
  (confirmed) => {
    if (!isOpen.value || !confirmed) return
    void attachStream()
  },
)

watch(isOpen, (open) => {
  if (!open) {
    destroyHls()
    if (videoRef.value) {
      try {
        videoRef.value.pause()
        videoRef.value.removeAttribute('src')
        videoRef.value.load()
      } catch {
        /* ignore — video might be unmounted already */
      }
    }
  }
})

onBeforeUnmount(() => {
  destroyHls()
})

function onClose(): void {
  player.close()
}

async function onTranslatorChange(event: Event): Promise<void> {
  const value = (event.target as HTMLSelectElement).value
  if (value) await player.selectTranslator(value)
}

async function onQualityChange(event: Event): Promise<void> {
  const value = (event.target as HTMLSelectElement).value
  if (value) await player.selectRezkaQuality(value)
}

async function onSeasonChange(event: Event): Promise<void> {
  const value = (event.target as HTMLSelectElement).value
  if (value) await player.selectSeason(value)
}

async function onEpisodeChange(event: Event): Promise<void> {
  const value = (event.target as HTMLSelectElement).value
  if (value) await player.selectEpisode(value)
}

async function onMarkSeasonSeen(): Promise<void> {
  await player.markSeasonSeen(player.season, player.episode)
}

function onCycleTrailer(): void {
  player.cycleTrailerCandidate()
}

// PR 5 — kino.pub picker handlers. Selecting a season auto-seeds
// episode 1; the player store handles the cascade in
// `_refreshKinopubStream()`.
function onKinopubSeasonChange(event: Event): void {
  const value = Number((event.target as HTMLSelectElement).value)
  if (Number.isFinite(value)) player.selectKinopubSeason(value)
}

function onKinopubEpisodeChange(event: Event): void {
  const value = Number((event.target as HTMLSelectElement).value)
  if (Number.isFinite(value)) player.selectKinopubEpisode(value)
}

function onKinopubFileChange(event: Event): void {
  const value = Number((event.target as HTMLSelectElement).value)
  if (Number.isFinite(value)) player.selectKinopubFile(value)
}

function onKinopubSubtitleChange(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  player.selectKinopubSubtitle(value)
}

function onCopyStreamUrl(): void {
  if (!player.streamUrl) return
  navigator.clipboard.writeText(player.streamUrl).then(() => {
    // We don't have a toast store here directly but we can use alert for now
    // or just assume success.
  })
}
</script>

<template>
  <!--
    Z-index notice: this modal sits at z-[60] which is deliberately
    higher than the rest of the panels (z-50). That keeps it on top
    when opened from inside `<ItemCardModal>`, matching the
    "modal-in-modal" UX the roadmap calls for in 10.7g.
  -->
  <div
    v-if="isOpen"
    class="fixed inset-0 z-[60] flex items-start justify-center bg-slate-900/80 p-2 sm:p-4 overflow-y-auto"
    data-testid="player-modal-backdrop"
    @click.self="onClose"
  >
    <div
      class="my-4 flex w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl max-h-[92vh]"
      data-testid="player-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="player-modal-title"
    >
      <header class="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-slate-200 bg-white px-5 py-3">
        <h2
          id="player-modal-title"
          class="truncate text-base font-semibold text-slate-900"
          data-testid="player-modal-title"
        >
          <span v-if="mode === 'trailer'">🎬 Трейлер: </span>
          <span v-else>▶ Стрим: </span>
          {{ player.itemTitle ?? '' }}
        </h2>
        <button
          type="button"
          class="rounded-md p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-700"
          aria-label="Закрыть"
          data-testid="player-modal-close"
          @click="onClose"
        >
          ×
        </button>
      </header>

      <div class="flex-1 space-y-4 overflow-y-auto p-5 text-sm text-slate-700">
        <!-- ── Trailer surface ───────────────────────────────────── -->
        <section
          v-if="mode === 'trailer'"
          data-testid="player-trailer"
          class="space-y-3"
        >
          <p
            v-if="player.trailerLoading"
            class="text-xs text-slate-500"
            data-testid="player-trailer-loading"
          >
            Загрузка трейлера…
          </p>
          <p
            v-if="player.trailerError && !trailerKey"
            class="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800"
            data-testid="player-trailer-error"
          >
            {{ player.trailerError }}
          </p>
          <div
            v-if="trailerKey"
            class="aspect-video w-full bg-black"
            data-testid="player-trailer-frame-wrap"
          >
            <!--
              `allow="autoplay; encrypted-media"` mirrors the legacy
              sandbox policy so YouTube can autoplay + serve DRM-
              protected content. `:key` forces a remount when we
              cycle to the next candidate so the iframe reloads.
            -->
            <iframe
              :key="trailerKey ?? ''"
              :src="trailerSrc"
              class="h-full w-full"
              frameborder="0"
              allow="autoplay; encrypted-media; picture-in-picture"
              allowfullscreen
              data-testid="player-trailer-frame"
            />
          </div>
          <div
            v-if="trailerKey"
            class="flex flex-wrap items-center gap-2"
          >
            <a
              :href="`https://www.youtube.com/watch?v=${trailerKey}`"
              target="_blank"
              rel="noopener"
              class="rounded-md bg-rose-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-700"
              data-testid="player-trailer-open-yt"
            >
              Открыть на YouTube
            </a>
            <button
              v-if="player.trailerCandidates.length > 1"
              type="button"
              class="rounded-md bg-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-300"
              data-testid="player-trailer-cycle"
              @click="onCycleTrailer"
            >
              ↻ Другой вариант ({{ player.trailerIndex + 1 }}/{{ player.trailerCandidates.length }})
            </button>
            <span
              v-if="player.trailerCandidates[player.trailerIndex]?.name"
              class="ml-auto truncate text-[11px] text-slate-500"
              :title="player.trailerCandidates[player.trailerIndex]?.name"
            >
              {{ player.trailerCandidates[player.trailerIndex]?.name }}
            </span>
          </div>
        </section>

        <!-- ── Stream surface ───────────────────────────────────── -->
        <section
          v-if="mode === 'stream'"
          data-testid="player-stream"
          class="space-y-3"
        >
          <p
            v-if="player.infoLoading"
            class="text-xs text-slate-500"
            data-testid="player-stream-loading"
          >
            Загрузка плеера…
          </p>
          <p
            v-if="player.infoError"
            class="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800"
            data-testid="player-stream-info-error"
          >
            {{ player.infoError }}
          </p>

          <!-- ── Stream tabs ───────────────────────────────────── -->
          <div class="flex items-center gap-1 border-b border-slate-100 px-1 pb-1">
            <button
              v-if="player.sourcesPageUrl || player.sources.length > 0"
              type="button"
              class="rounded-md px-3 py-1.5 text-xs font-bold transition-colors"
              :class="player.activeTab === 'kinohub' ? 'bg-indigo-600 text-white' : 'text-slate-500 hover:bg-slate-100'"
              @click="player.setActiveTab('kinohub')"
            >
              🌐 Kinohub
            </button>
            <button
              type="button"
              class="rounded-md px-3 py-1.5 text-xs font-bold transition-colors"
              :class="player.activeTab === 'rezka' ? 'bg-emerald-600 text-white' : 'text-slate-500 hover:bg-slate-100'"
              @click="player.setActiveTab('rezka')"
            >
              🎬 Rezka
            </button>
            <button
              type="button"
              class="rounded-md px-3 py-1.5 text-xs font-bold transition-colors"
              :class="player.activeTab === 'kinopub' ? 'bg-fuchsia-600 text-white' : 'text-slate-500 hover:bg-slate-100'"
              @click="player.setActiveTab('kinopub')"
            >
              💎 Kino.pub
            </button>
          </div>

          <!-- ── Stream sections ───────────────────────────────────── -->
          <div class="grid grid-cols-1">
            <!-- 1. Kinohub & Alternative Players -->
            <section
              v-if="player.activeTab === 'kinohub' && (player.sourcesPageUrl || player.sources.length > 0)"
              class="rounded-xl border border-indigo-100 bg-indigo-50/30 p-4"
              data-testid="player-section-kinohub"
            >
              <h3 class="flex items-center gap-2 text-sm font-bold text-indigo-800">
                <span class="text-lg">🌐</span> 1. Kinohub и другие
              </h3>
              <div class="mt-3 space-y-3">
                <a
                  v-if="player.sourcesPageUrl"
                  :href="player.sourcesPageUrl"
                  target="_blank"
                  rel="noopener"
                  class="flex items-center gap-2 font-semibold text-indigo-700 hover:text-indigo-900"
                  data-testid="player-stream-page-url"
                >
                  ▶ Kinohub (все плееры)
                </a>
                <div v-if="player.sources.length > 0">
                  <p class="text-[11px] font-medium text-slate-500 uppercase tracking-wider">
                    Плееры:
                  </p>
                  <div class="mt-1.5 flex flex-wrap gap-2">
                    <a
                      v-for="src in player.sources"
                      :key="src.type"
                      :href="src.iframeUrl"
                      target="_blank"
                      rel="noopener"
                      class="rounded-md bg-violet-600 px-3 py-1 text-[11px] font-semibold text-white hover:bg-violet-700 shadow-sm"
                      :data-testid="`player-stream-source-${src.type}`"
                    >
                      {{ src.type }}
                    </a>
                  </div>
                </div>
              </div>
            </section>

            <!-- 2. REZKA -->
            <section
              v-if="player.activeTab === 'rezka'"
              class="rounded-xl border border-emerald-100 bg-emerald-50/30 p-4"
              data-testid="player-section-rezka"
            >
              <h3 class="flex items-center gap-2 text-sm font-bold text-emerald-800">
                <span class="text-lg">🎬</span> 2. REZKA
              </h3>
              
              <div class="mt-3 space-y-4">
                <p class="text-[11px] font-medium text-emerald-600 uppercase flex items-center gap-1.5">
                  <span class="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                  Встроенный плеер активен
                </p>

                <!-- Rezka controls -->
                <div v-if="player.info" class="space-y-3">
                  <label
                    v-if="translatorEntries.length > 0"
                    class="block"
                  >
                    <span class="block text-xs font-semibold text-slate-500 uppercase mb-1">Озвучка</span>
                    <select
                      class="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm shadow-sm focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
                      :value="player.translatorId ?? ''"
                      @change="onTranslatorChange"
                    >
                      <option
                        v-for="t in translatorEntries"
                        :key="t.id"
                        :value="t.id"
                      >
                        {{ t.name }}{{ t.premium ? ' ★' : '' }}
                      </option>
                    </select>
                  </label>
 
                  <label
                    v-if="player.rezkaQualities.length > 0"
                    class="block"
                  >
                    <span class="block text-xs font-semibold text-slate-500 uppercase mb-1">Качество</span>
                    <select
                      class="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm shadow-sm focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
                      :value="player.streamQuality ?? ''"
                      @change="onQualityChange"
                    >
                      <option
                        v-for="q in player.rezkaQualities"
                        :key="q"
                        :value="q"
                      >
                        {{ q }}
                      </option>
                    </select>
                  </label>

                  <div v-if="player.isSeries" class="grid grid-cols-2 gap-3">
                    <label v-if="seasonEntries.length > 0" class="block">
                      <span class="block text-xs font-semibold text-slate-500 uppercase mb-1">Сезон</span>
                      <select
                        class="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm shadow-sm focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
                        :value="player.season ?? ''"
                        @change="onSeasonChange"
                      >
                        <option v-for="s in seasonEntries" :key="s.id" :value="s.id">
                          {{ s.name }}
                        </option>
                      </select>
                    </label>
                    <label v-if="episodeEntries.length > 0" class="block">
                      <span class="block text-xs font-semibold text-slate-500 uppercase mb-1">Серия</span>
                      <select
                        class="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm shadow-sm focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
                        :value="player.episode ?? ''"
                        @change="onEpisodeChange"
                      >
                        <option v-for="ep in episodeEntries" :key="ep.id" :value="ep.id">
                          {{ ep.name }}
                        </option>
                      </select>
                    </label>
                  </div>
                  
                  <button
                    v-if="player.isSeries"
                    type="button"
                    class="w-full rounded-lg bg-emerald-600 px-4 py-2 text-xs font-bold text-white hover:bg-emerald-700 shadow-md transition-colors"
                    @click="onMarkSeasonSeen"
                  >
                    ✓ Отметить сезон как просмотренный
                  </button>
                </div>
              </div>

            </section>

            <!-- 3. KINOPUB -->
            <section
              v-if="player.activeTab === 'kinopub'"
              class="rounded-xl border border-fuchsia-100 bg-fuchsia-50/30 p-4"
              data-testid="player-section-kinopub"
            >
              <h3 class="flex items-center gap-2 text-sm font-bold text-fuchsia-800">
                <span class="text-lg">💎</span> 3. КИНОПАБ
              </h3>

              <div class="mt-3 space-y-4">
                 <p class="text-[11px] font-medium text-fuchsia-600 uppercase flex items-center gap-1.5">
                  <span class="h-1.5 w-1.5 rounded-full bg-fuchsia-500 animate-pulse"></span>
                  Kino.pub активен
                </p>

                <div v-if="player.kinopubInfo" class="space-y-3">
                  <div v-if="player.isKinopubSeries" class="grid grid-cols-2 gap-3">
                    <label v-if="player.kinopubSeasons.length > 0" class="block">
                      <span class="block text-xs font-semibold text-slate-500 uppercase mb-1">Сезон</span>
                      <select
                        class="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm shadow-sm focus:border-fuchsia-500 focus:ring-1 focus:ring-fuchsia-500"
                        :value="player.kinopubSeasonNumber ?? ''"
                        @change="onKinopubSeasonChange"
                      >
                        <option v-for="s in player.kinopubSeasons" :key="s.number" :value="s.number">
                          Сезон {{ s.number }} ({{ s.episodeCount }})
                        </option>
                      </select>
                    </label>
                    <label
                      v-if="(player.kinopubInfo?.seasons?.find((s) => s.number === player.kinopubSeasonNumber)?.episodes?.length ?? 0) > 0"
                      class="block"
                    >
                      <span class="block text-xs font-semibold text-slate-500 uppercase mb-1">Серия</span>
                      <select
                        class="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm shadow-sm focus:border-fuchsia-500 focus:ring-1 focus:ring-fuchsia-500"
                        :value="player.kinopubEpisodeNumber ?? ''"
                        @change="onKinopubEpisodeChange"
                      >
                        <option
                          v-for="ep in player.kinopubInfo?.seasons?.find((s) => s.number === player.kinopubSeasonNumber)?.episodes ?? []"
                          :key="ep.number ?? -1"
                          :value="ep.number ?? -1"
                        >
                          №{{ ep.number }}{{ ep.title ? `: ${ep.title}` : '' }}
                        </option>
                      </select>
                    </label>
                  </div>

                  <label v-if="kinopubFileEntries.length > 0" class="block">
                    <span class="block text-xs font-semibold text-slate-500 uppercase mb-1">Качество</span>
                    <select
                      class="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm shadow-sm focus:border-fuchsia-500 focus:ring-1 focus:ring-fuchsia-500"
                      :value="player.kinopubFileIdx ?? 0"
                      @change="onKinopubFileChange"
                    >
                      <option v-for="f in kinopubFileEntries" :key="f.idx" :value="f.idx">
                        {{ f.label }}
                      </option>
                    </select>
                  </label>

                  <p
                    v-if="kinopubAudioEntries.length > 0"
                    class="text-[11px] text-slate-500 bg-white/50 rounded px-2 py-1 border border-slate-100"
                  >
                    <span class="font-semibold uppercase text-[9px] text-slate-400 block mb-0.5">Дорожки</span>
                    {{ kinopubAudioEntries.map((a) => a.label).join(', ') }}
                  </p>

                  <label v-if="subtitleEntries.length > 0" class="block">

                    <span class="block text-xs font-semibold text-slate-500 uppercase mb-1">Субтитры</span>
                    <select
                      class="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm shadow-sm focus:border-fuchsia-500 focus:ring-1 focus:ring-fuchsia-500"
                      :value="player.kinopubSubtitleLang"
                      @change="onKinopubSubtitleChange"
                    >
                      <option value="">Отключены</option>
                      <option v-for="s in subtitleEntries" :key="s.lang" :value="s.lang">
                        {{ s.title }}
                      </option>
                    </select>
                  </label>
                </div>
              </div>
            </section>
          </div>


          <!-- ── Shared video element ───────────────────────────────── -->
          <p
            v-if="player.streamLoading"
            class="text-xs text-slate-500"
            data-testid="player-stream-resolving"
          >
            Резолвим поток…
          </p>
          <p
            v-if="player.streamError"
            class="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800"
            data-testid="player-stream-error"
          >
            {{ player.streamError }}
          </p>
          <p
            v-if="playerError"
            class="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
            data-testid="player-stream-attach-error"
          >
            {{ playerError }}
          </p>

          <div
            v-if="player.streamUrl && !player.streamConfirmed"
            class="flex flex-col items-center justify-center rounded-xl bg-slate-50 border border-slate-200 p-8 text-center"
            data-testid="player-confirm-wrap"
          >
            <p class="text-sm text-slate-600 mb-4">
              Поток готов к воспроизведению
            </p>
            <button
              type="button"
              class="group relative flex items-center gap-3 rounded-full bg-indigo-600 px-8 py-3.5 text-lg font-bold text-white shadow-lg transition-all hover:bg-indigo-700 hover:shadow-indigo-200 active:scale-95"
              @click="player.confirmStream()"
              data-testid="player-confirm-btn"
            >
              <span class="text-2xl transition-transform group-hover:scale-110">▶</span>
              СМОТРЕТЬ
            </button>
          </div>

          <div
            v-if="player.streamUrl && player.streamConfirmed"
            data-testid="player-stream-video-wrap"
          >
            <video
              ref="videoRef"
              controls
              autoplay
              crossorigin="anonymous"
              class="aspect-video max-h-[60vh] w-full rounded-lg bg-black object-contain"
              data-testid="player-stream-video"
            >
              <track
                v-for="(sub, idx) in subtitleEntries"
                :key="sub.lang"
                kind="subtitles"
                :label="sub.title"
                :srclang="sub.lang"
                :src="sub.src"
                :default="player.activeTab === 'kinopub' ? sub.lang === player.kinopubSubtitleLang : idx === 0"
              />
            </video>
            <div class="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
              <span class="rounded bg-slate-100 px-2 py-0.5 text-slate-600">
                Качество: {{ player.activeTab === 'kinopub' ? (player.kinopubVideo?.files?.[player.kinopubFileIdx ?? 0]?.quality ?? '—') : (player.streamQuality ?? '—') }}
              </span>
               <a
                v-if="player.streamM3uUrl"
                :href="player.streamM3uUrl"
                class="rounded-md bg-orange-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-orange-600 shadow-sm flex items-center gap-1"
                data-testid="player-stream-m3u"
                title="Скачать плейлист для VLC/PotPlayer/etc"
              >
                <span>📺</span> VLC / Плейлист
              </a>
              <a
                :href="player.streamUrl"
                target="_blank"
                rel="noopener"
                class="rounded-md bg-slate-800 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-900 shadow-sm flex items-center gap-1"
                data-testid="player-stream-direct-url"
              >
                <span>🔗</span> Прямая ссылка
              </a>
              <button
                type="button"
                class="rounded-md bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-200 border border-slate-200"
                @click="onCopyStreamUrl"
              >
                📋 Копировать URL
              </button>
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>
