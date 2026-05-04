# Frontend notes (`index.html`)

Single-file Vue 3 app, served as `text/html` from `GET /` and pulling
runtime libraries from public CDNs (Vue 3, Tailwind Play, SortableJS).
There is intentionally no bundler — each section here documents
something that is invisible from the source itself.

## Storage strategy (7.3)

Two stores are used and mixed for a reason:

| Key                       | Store            | Why                                                                 |
| ------------------------- | ---------------- | ------------------------------------------------------------------- |
| `authToken`               | `sessionStorage` | Token must die with the tab — closes the door on tab-jacking and on coffee-shop "stay signed in forever" footguns. |
| `f_cat`, `f_search`, `f_minKp`, `f_maxKp`, `f_minImdb`, `f_maxImdb`, `f_page` | `sessionStorage` | Filters belong to *this* browsing session: opening the app in a new tab should not reuse a 23-page-deep filter from yesterday. |
| `scroll_pos`              | `sessionStorage` | Used by `restoreScroll()` after navigation — only meaningful while the tab is alive. |
| `hideWatched`, `hideCollected` | `localStorage` | User-level preferences — survive across sessions and devices that reuse the same browser profile. |

Rule of thumb: anything tied to the *current view* lives in
`sessionStorage`; anything that should follow the user lives in
`localStorage`. Auth tokens are sessionStorage on purpose.

## CDN integrity & supply-chain (7.4)

`<script src="...">` tags for Vue runtime and SortableJS use pinned
versions and SRI (`integrity="sha384-..." crossorigin="anonymous"`).
Tailwind Play (`cdn.tailwindcss.com`) intentionally returns a
non-stable bundle (it compiles utility classes against the live page
on every load) so SRI is impossible there — the script-src whitelist
in the CSP is the only line of defence for that one origin.

If you bump a CDN version in `index.html`, recompute the SRI hash:

```bash
curl -fsSL "https://unpkg.com/vue@3.4.27/dist/vue.global.prod.js" \
  | openssl dgst -sha384 -binary \
  | openssl base64 -A
# prints the value to put after `sha384-` in the integrity= attribute
```

## XSS / `v-html` policy (7.5)

`v-html` is **not used anywhere** in `index.html` and must stay that
way. Any future `v-html` usage requires DOMPurify or an equivalent
sanitiser, because `description`, `title`, and rezka-derived strings
are partly user-influenced and partly attacker-controllable (rezka
synopses, rutor torrent titles, etc.).

To check before committing:

```bash
git grep -n 'v-html' index.html
# expected output: empty (no matches)
```

If this command starts returning matches, either revert the change or
add DOMPurify (`<script src="https://cdn.jsdelivr.net/npm/dompurify@3/...">`
+ SRI, then `v-html="DOMPurify.sanitize(value)"`).

## Service worker (7.1)

`sw.js` is served from `GET /sw.js` (see `main.py:_sw_version` and
`get_sw`). The placeholder `__SW_VERSION__` is replaced at request
time with a 12-char SHA-1 prefix derived from `(mtime, size)` of
`index.html`, `sw.js`, `manifest.json`. Any deploy-time change to
those files yields different bytes, browsers re-install the worker,
and the `activate` handler purges the old `caches` entries by name.

Strategy:
- Navigation (HTML): network-first, fall back to cache when offline.
- `manifest.json` and `/icon.png`: cache-first.
- Everything else (CDN, API, posters): pass through, no caching —
  this avoids stale auth/streaming/CSP and keeps API calls live.

The `/sw.js` response itself sends `Cache-Control: no-cache,
max-age=0` so the worker file is never sticky in the browser cache.

## Process-elapsed indicator (7.6)

`logPanel` shows `⏱ N с / N м N с / N ч N м` next to the log filename
while a process is `running`. The clock is computed from
`processStartTimes[currentStatusKey]` (set by the WS status handler)
and `nowTick` (a `Date.now()` value updated every second by
`setInterval` in `mounted`). Because the timer only triggers a
re-render via `nowTick`, it costs ~1 reactive update per second and
nothing while no log panel is open.

## CSP highlights

`Content-Security-Policy` (see `main.py:_CSP`) allows:

- `script-src`: self + unpkg + jsdelivr + tailwindcss CDN; `unsafe-eval`
  (Vue runtime templates) and `unsafe-inline` (Tailwind Play's runtime
  `<style>` injection). Tightening these requires moving to a build step.
- `worker-src 'self' blob:` — for `navigator.serviceWorker.register('/sw.js')`.
- `frame-src` — restricted to YouTube origins (8.7 trailers).
- `media-src 'self' blob: https:` — HLS via blob URL + remote segments (8.12).
- `frame-ancestors 'none'` — clickjacking guard, paired with `X-Frame-Options: DENY`.

If a feature ever needs a different host/scheme, add it explicitly
here and document why above. Don't widen to `'unsafe-inline'` for
script-src under any circumstances.
