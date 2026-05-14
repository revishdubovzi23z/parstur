import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import './style.css'

const app = createApp(App)
app.use(createPinia())
app.mount('#app')

// ROADMAP Stage 10.7z — the service worker (`/sw.js`) is served by the
// FastAPI backend with its version hash + precache list baked in. The
// legacy `index.html` used to register it inline; with that file gone
// the SPA entry point is the only place left to call `register()`.
//
// We skip registration when SW isn't available (older browsers, opaque
// origins, jsdom test runners) and when the page is served over plain
// HTTP from a host other than localhost — browsers won't activate a
// worker in that case, so log noise is the only outcome.
if ('serviceWorker' in navigator) {
  const isSecure =
    location.protocol === 'https:' ||
    location.hostname === 'localhost' ||
    location.hostname === '127.0.0.1'
  if (isSecure) {
    window.addEventListener('load', () => {
      void navigator.serviceWorker.register('/sw.js').catch((err) => {
        // Don't fall over if the SW fails to register — the app still
        // works without offline caching.
        console.warn('[sw] register failed', err)
      })
    })
  }
}
