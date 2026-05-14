<script setup lang="ts">
// ROADMAP Stage 10.2/10.3 — root component (auth wiring).
// ROADMAP Stage 10.4 — mounts the feed grid as the default screen
// inside the AppShell. The login modal still overlays whenever the
// session store flips into `unauthenticated`; until that resolves,
// the feed shows its own loading / empty state behind the overlay.
import { onMounted } from 'vue'
import AppShell from './components/AppShell.vue'
import LoginModal from './components/LoginModal.vue'
import FeedView from './components/feed/FeedView.vue'
import { useSessionStore } from './stores/session'

const session = useSessionStore()

onMounted(() => {
  // Don't await — render the shell immediately and let the badge in
  // the header transition out of "Проверка…" once the request lands.
  void session.init()
})
</script>

<template>
  <AppShell>
    <FeedView />
  </AppShell>
  <LoginModal />
</template>
