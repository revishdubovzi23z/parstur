/*!
 * TMDB Folders for Lampa
 *
 * Adds a "TMDB папки" entry to Lampa's left menu. Inside it shows the user's
 * TMDB lists ("folders") and lets the user drill into each folder to see its
 * movies / TV shows.
 *
 * Configuration is done from Lampa's settings under "TMDB папки":
 *   - TMDB v4 Bearer token (required, used in the Authorization header)
 *   - TMDB account object id (optional, used to auto-discover lists)
 *   - Manual list ids (optional, comma-separated, useful for public lists or
 *     when account_object_id is not available)
 *
 * Network strategy:
 *   - Bearer auth via Authorization header
 *   - In-memory cache with a TTL (configurable, default 10 minutes)
 *   - Soft rate limiter (>= 200 ms between requests, well under TMDB's
 *     ~40 requests / 10 seconds budget)
 */
(function () {
    'use strict';

    if (typeof Lampa === 'undefined') {
        try { console.warn('[tmdb_folders] Lampa global is missing, plugin will not load'); } catch (e) {}
        return;
    }
    if (window.tmdb_folders_ready) {
        try { console.log('[tmdb_folders] already loaded, skipping'); } catch (e) {}
        return;
    }
    window.tmdb_folders_ready = true;

    var MANIFEST = {
        type: 'video',
        version: '0.2.0',
        name: 'TMDB папки',
        description: 'Показывает списки (папки) с TMDB в левом меню Lampa',
        component: 'tmdb_folders'
    };

    function safeLog() {
        try {
            var args = ['[tmdb_folders]'].concat(Array.prototype.slice.call(arguments));
            console.log.apply(console, args);
        } catch (e) { /* noop */ }
    }

    function safeWarn() {
        try {
            var args = ['[tmdb_folders]'].concat(Array.prototype.slice.call(arguments));
            console.warn.apply(console, args);
        } catch (e) { /* noop */ }
    }

    function safeError() {
        try {
            var args = ['[tmdb_folders]'].concat(Array.prototype.slice.call(arguments));
            console.error.apply(console, args);
        } catch (e) { /* noop */ }
    }

    safeLog('init start, Lampa.Manifest.app_version =',
        (Lampa.Manifest && Lampa.Manifest.app_version) || 'unknown');

    try {
        Lampa.Manifest.plugins = MANIFEST;
    } catch (e) {
        safeWarn('Lampa.Manifest.plugins setter threw, ignoring:', e && e.message);
    }

    var STORAGE = {
        token: 'tmdb_folders_token',
        account: 'tmdb_folders_account_id',
        manual: 'tmdb_folders_manual_ids',
        language: 'tmdb_folders_language',
        cache_ttl: 'tmdb_folders_cache_ttl_min'
    };

    var TMDB_HOST = 'https://api.themoviedb.org';
    var POSTER_HOST = 'https://image.tmdb.org/t/p';

    // ---------- helpers ----------

    var log = safeLog;

    function token() {
        return (Lampa.Storage.get(STORAGE.token, '') || '').toString().trim();
    }

    function accountId() {
        return (Lampa.Storage.get(STORAGE.account, '') || '').toString().trim();
    }

    function manualListIds() {
        var raw = (Lampa.Storage.get(STORAGE.manual, '') || '').toString();
        return raw
            .split(/[,\s]+/)
            .map(function (s) { return s.trim(); })
            .filter(function (s) { return /^\d+$/.test(s); });
    }

    function language() {
        var lang = (Lampa.Storage.get(STORAGE.language, '') || '').toString().trim();
        if (lang) return lang;
        try {
            var loc = Lampa.Storage.get('language', 'ru');
            if (loc === 'ru') return 'ru-RU';
            if (loc === 'en') return 'en-US';
            if (loc === 'uk') return 'uk-UA';
            if (loc === 'be') return 'be-BY';
            return (loc || 'ru') + '-' + (loc || 'ru').toUpperCase();
        } catch (e) {
            return 'ru-RU';
        }
    }

    function cacheTtlMs() {
        var raw = parseInt(Lampa.Storage.get(STORAGE.cache_ttl, 10), 10);
        if (!isFinite(raw) || raw < 1) raw = 10;
        return raw * 60 * 1000;
    }

    function hasToken() {
        return token().length > 0;
    }

    function noty(msg) {
        try {
            if (Lampa.Noty && typeof Lampa.Noty.show === 'function') Lampa.Noty.show(msg);
            else safeLog('noty (no Lampa.Noty):', msg);
        } catch (e) { /* noop */ }
    }

    // ---------- rate-limited request queue with cache ----------

    var network = null;
    function getNetwork() {
        if (network) return network;
        try {
            if (typeof Lampa.Reguest === 'function') network = new Lampa.Reguest();
            else if (typeof Lampa.Request === 'function') network = new Lampa.Request();
        } catch (e) {
            safeWarn('failed to construct Lampa.Reguest:', e && e.message);
        }
        return network;
    }

    var cache = {};
    var queue = [];
    var inflight = false;
    var lastSent = 0;
    var MIN_INTERVAL_MS = 200;

    function cacheGet(key) {
        var entry = cache[key];
        if (!entry) return null;
        if (Date.now() - entry.t > cacheTtlMs()) {
            delete cache[key];
            return null;
        }
        return entry.v;
    }

    function cacheSet(key, value) {
        cache[key] = { t: Date.now(), v: value };
    }

    function clearCache() {
        cache = {};
    }

    function tmdbRequest(path, params, oncomplete, onerror) {
        if (!hasToken()) {
            onerror({ status: 401, message: 'Не задан TMDB v4 токен' });
            return;
        }

        var qs = [];
        Object.keys(params || {}).forEach(function (k) {
            var v = params[k];
            if (v === undefined || v === null || v === '') return;
            qs.push(encodeURIComponent(k) + '=' + encodeURIComponent(v));
        });

        var url = TMDB_HOST + path + (qs.length ? ('?' + qs.join('&')) : '');
        var cached = cacheGet(url);
        if (cached) {
            // Defer to keep call-site flow async.
            setTimeout(function () { oncomplete(cached); }, 0);
            return;
        }

        queue.push({ url: url, oncomplete: oncomplete, onerror: onerror });
        processQueue();
    }

    function processQueue() {
        if (inflight) return;
        var job = queue.shift();
        if (!job) return;
        inflight = true;

        var wait = Math.max(0, MIN_INTERVAL_MS - (Date.now() - lastSent));
        setTimeout(function () {
            lastSent = Date.now();

            var cached = cacheGet(job.url);
            if (cached) {
                inflight = false;
                job.oncomplete(cached);
                processQueue();
                return;
            }

            var net = getNetwork();
            if (!net) {
                inflight = false;
                job.onerror({ status: 0, message: 'Lampa.Reguest unavailable' });
                processQueue();
                return;
            }
            try { net.timeout(15000); } catch (e) { /* older forks may not have .timeout */ }
            net.silent(
                job.url,
                function (data) {
                    cacheSet(job.url, data);
                    inflight = false;
                    job.oncomplete(data);
                    processQueue();
                },
                function (xhr, status) {
                    inflight = false;
                    job.onerror({ status: status, xhr: xhr });
                    processQueue();
                },
                false,
                {
                    dataType: 'json',
                    headers: {
                        Authorization: 'Bearer ' + token(),
                        'Content-Type': 'application/json;charset=utf-8'
                    }
                }
            );
        }, wait);
    }

    // ---------- TMDB API wrappers ----------

    /**
     * Fetch all lists for the configured account_object_id, paginating until
     * we've seen every page. Returns an array of list-summary objects.
     */
    function fetchAccountLists(oncomplete, onerror) {
        var acc = accountId();
        if (!acc) {
            oncomplete([]);
            return;
        }

        var collected = [];
        var page = 1;

        function loadPage() {
            tmdbRequest('/4/account/' + encodeURIComponent(acc) + '/lists', { page: page }, function (data) {
                var results = (data && data.results) || [];
                collected = collected.concat(results);
                var totalPages = (data && data.total_pages) || 1;
                if (page >= totalPages || results.length === 0) {
                    oncomplete(collected);
                } else {
                    page += 1;
                    loadPage();
                }
            }, function (err) {
                if (collected.length) {
                    // Return whatever we got plus a warning.
                    log('account lists partial error', err);
                    oncomplete(collected);
                } else {
                    onerror(err);
                }
            });
        }

        loadPage();
    }

    /**
     * Fetch a single list (page 1 by default) — used both to enumerate manual
     * list ids as folders and to render the contents of a folder.
     */
    function fetchListDetails(listId, page, oncomplete, onerror) {
        tmdbRequest('/4/list/' + encodeURIComponent(listId), {
            page: page || 1,
            language: language()
        }, oncomplete, onerror);
    }

    /**
     * Merge account-derived lists with manually-configured list ids. Manual
     * ids are appended only if they're not already present in the account
     * results, so we never duplicate folders.
     */
    function loadAllFolders(oncomplete, onerror) {
        var manualIds = manualListIds();

        fetchAccountLists(function (accountLists) {
            var seen = {};
            accountLists.forEach(function (item) { if (item && item.id != null) seen[String(item.id)] = true; });

            var missingManual = manualIds.filter(function (id) { return !seen[id]; });

            if (!missingManual.length) {
                oncomplete(accountLists);
                return;
            }

            // For manual ids we have to hit /4/list/{id} to discover their
            // metadata (name, number_of_items, etc.). We do this sequentially
            // via the queue so we stay polite to the API.
            var resolved = accountLists.slice();
            var pending = missingManual.length;
            var hadError = false;

            missingManual.forEach(function (listId) {
                fetchListDetails(listId, 1, function (data) {
                    if (data && data.id != null) {
                        resolved.push({
                            id: data.id,
                            name: data.name || ('Список ' + data.id),
                            description: data.description || '',
                            number_of_items: (typeof data.total_results === 'number')
                                ? data.total_results
                                : ((data.results || []).length),
                            poster_path: data.poster_path || null,
                            backdrop_path: data.backdrop_path || null,
                            iso_639_1: data.iso_639_1 || null,
                            iso_3166_1: data.iso_3166_1 || null,
                            public: data.public != null ? data.public : 1,
                            _preview_results: data.results || [],
                            _manual: true
                        });
                    }
                    pending -= 1;
                    if (pending === 0) oncomplete(resolved);
                }, function (err) {
                    hadError = true;
                    log('manual list ' + listId + ' failed', err);
                    pending -= 1;
                    if (pending === 0) {
                        if (resolved.length || !hadError) oncomplete(resolved);
                        else onerror(err);
                    }
                });
            });
        }, function (err) {
            if (!manualIds.length) {
                onerror(err);
                return;
            }
            // Account lookup failed (likely auth scope). Fall back to manual
            // ids only.
            var resolved = [];
            var pending = manualIds.length;
            var hadError = false;

            manualIds.forEach(function (listId) {
                fetchListDetails(listId, 1, function (data) {
                    if (data && data.id != null) {
                        resolved.push({
                            id: data.id,
                            name: data.name || ('Список ' + data.id),
                            description: data.description || '',
                            number_of_items: (typeof data.total_results === 'number')
                                ? data.total_results
                                : ((data.results || []).length),
                            poster_path: data.poster_path || null,
                            backdrop_path: data.backdrop_path || null,
                            iso_639_1: data.iso_639_1 || null,
                            iso_3166_1: data.iso_3166_1 || null,
                            public: data.public != null ? data.public : 1,
                            _preview_results: data.results || [],
                            _manual: true
                        });
                    }
                    pending -= 1;
                    if (pending === 0) {
                        if (resolved.length) oncomplete(resolved);
                        else onerror(err);
                    }
                }, function (e) {
                    hadError = true;
                    log('manual list ' + listId + ' failed (fallback)', e);
                    pending -= 1;
                    if (pending === 0) {
                        if (resolved.length) oncomplete(resolved);
                        else onerror(err);
                    }
                });
            });
        });
    }

    // ---------- adapters between TMDB and Lampa card shape ----------

    /**
     * Convert a raw TMDB list item into a card object Lampa can render via
     * its standard `full` component when the user enters the card.
     */
    function adaptItem(item) {
        if (!item || typeof item !== 'object') return null;

        // Some endpoints don't expose media_type; default to 'movie' so the
        // card is at least navigable, but prefer the explicit field.
        var mediaType = item.media_type || (item.first_air_date ? 'tv' : 'movie');

        var title = item.title || item.name || item.original_title || item.original_name || '';
        var origTitle = item.original_title || item.original_name || title;
        var releaseDate = item.release_date || item.first_air_date || '';

        var card = {
            source: 'tmdb',
            type: mediaType === 'tv' ? 'tv' : 'movie',
            id: item.id,
            title: title,
            original_title: origTitle,
            name: title,
            original_name: origTitle,
            overview: item.overview || '',
            release_date: mediaType === 'tv' ? '' : releaseDate,
            first_air_date: mediaType === 'tv' ? releaseDate : '',
            poster_path: item.poster_path || '',
            backdrop_path: item.backdrop_path || '',
            vote_average: item.vote_average || 0,
            vote_count: item.vote_count || 0,
            adult: !!item.adult,
            genre_ids: item.genre_ids || [],
            original_language: item.original_language || '',
            popularity: item.popularity || 0,
            media_type: mediaType
        };

        if (!card.poster_path && card.backdrop_path) card.poster_path = card.backdrop_path;
        return card;
    }

    function adaptFolderToCard(folder) {
        // Render a folder as a "fake card" so InteractionCategory can lay it
        // out next to real movie cards. We borrow the list's metadata for the
        // poster / backdrop fields and remember the real id in _list_id.
        var name = folder.name || ('Список ' + folder.id);
        var count = folder.number_of_items != null ? folder.number_of_items : '';
        var card = {
            source: 'tmdb',
            type: 'movie',
            id: 'tmdb_folder_' + folder.id,
            title: name,
            original_title: name,
            name: name,
            original_name: name,
            overview: folder.description || (count ? (count + ' элементов') : ''),
            release_date: '',
            first_air_date: '',
            poster_path: folder.poster_path || folder.backdrop_path || '',
            backdrop_path: folder.backdrop_path || folder.poster_path || '',
            vote_average: 0,
            vote_count: 0,
            adult: false,
            genre_ids: [],
            original_language: folder.iso_639_1 || '',
            popularity: 0,
            media_type: 'movie',
            _list_id: folder.id,
            _list_name: name,
            _list_meta: folder
        };
        return card;
    }

    // ---------- Lampa components ----------

    /**
     * Folders view — top-level screen reached from the left menu.
     * Renders each TMDB list as a card; entering a card drills into its
     * contents.
     */
    function makeInteractionCategory(object) {
        if (typeof Lampa.InteractionCategory === 'function') return new Lampa.InteractionCategory(object);
        if (typeof Lampa.InteractionMain === 'function') {
            safeWarn('Lampa.InteractionCategory missing, falling back to InteractionMain');
            return new Lampa.InteractionMain(object);
        }
        throw new Error('Neither Lampa.InteractionCategory nor Lampa.InteractionMain are available');
    }

    function foldersComponent(object) {
        var comp = makeInteractionCategory(object);

        comp.create = function () {
            var self = this;

            if (!hasToken()) {
                noty('Сначала задайте TMDB v4 токен в Настройки → ' + MANIFEST.name);
                self.empty();
                return;
            }

            loadAllFolders(function (folders) {
                if (!folders || !folders.length) {
                    noty('TMDB не вернул ни одной папки. Проверьте account_object_id или укажите list ids вручную.');
                    self.empty();
                    return;
                }

                var cards = folders
                    .filter(function (f) { return f && f.id != null; })
                    .map(adaptFolderToCard);

                self.build({
                    results: cards,
                    total_pages: 1,
                    page: 1
                });
            }, function (err) {
                log('foldersComponent.create error', err);
                noty('Не удалось получить список папок TMDB');
                self.empty();
            });
        };

        comp.nextPageReuest = function (obj, resolve, reject) {
            // Only ever one page of folders for now.
            resolve.call(comp, { results: [], total_pages: 1, page: 1 });
        };

        comp.cardRender = function (obj, element, card) {
            card.onMenu = false;
            card.onEnter = function () {
                Lampa.Activity.push({
                    url: '',
                    title: element._list_name || element.title,
                    component: 'tmdb_folder_content',
                    list_id: element._list_id,
                    page: 1
                });
            };
        };

        return comp;
    }

    /**
     * Folder content — second-level screen showing the items inside one
     * specific TMDB list. Each card opens Lampa's standard 'full' component.
     */
    function folderContentComponent(object) {
        var comp = makeInteractionCategory(object);

        function loadPage(page, resolve, reject) {
            if (!object.list_id) {
                reject.call(comp);
                return;
            }

            fetchListDetails(object.list_id, page, function (data) {
                var items = ((data && data.results) || [])
                    .map(adaptItem)
                    .filter(function (c) { return c && c.id != null; });

                resolve.call(comp, {
                    results: items,
                    page: page,
                    total_pages: (data && data.total_pages) || 1
                });
            }, function (err) {
                log('folder content page', page, 'error', err);
                reject.call(comp);
            });
        }

        comp.create = function () {
            loadPage(object.page || 1, this.build.bind(this), this.empty.bind(this));
        };

        comp.nextPageReuest = function (obj, resolve, reject) {
            loadPage(obj.page, resolve, reject);
        };

        comp.cardRender = function (obj, element, card) {
            card.onMenu = false;
            card.onEnter = function () {
                Lampa.Activity.push({
                    url: '',
                    component: 'full',
                    id: element.id,
                    method: element.type === 'tv' ? 'tv' : 'movie',
                    source: 'tmdb',
                    card: element
                });
            };
        };

        return comp;
    }

    // ---------- settings page ----------

    function registerSettings() {
        if (!Lampa.SettingsApi ||
            typeof Lampa.SettingsApi.addComponent !== 'function' ||
            typeof Lampa.SettingsApi.addParam !== 'function') {
            safeWarn('Lampa.SettingsApi missing, skipping settings registration');
            return;
        }

        var icon =
            '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">' +
            '<path d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2z" fill="currentColor"/>' +
            '</svg>';

        Lampa.SettingsApi.addComponent({
            component: 'tmdb_folders',
            name: MANIFEST.name,
            icon: icon
        });

        Lampa.SettingsApi.addParam({
            component: 'tmdb_folders',
            param: {
                name: STORAGE.token,
                type: 'input',
                values: '',
                default: ''
            },
            field: {
                name: 'TMDB v4 Bearer token',
                description: 'Read Access Token из TMDB → Settings → API'
            },
            onChange: function () {
                clearCache();
            }
        });

        Lampa.SettingsApi.addParam({
            component: 'tmdb_folders',
            param: {
                name: STORAGE.account,
                type: 'input',
                values: '',
                default: ''
            },
            field: {
                name: 'TMDB account_object_id',
                description: 'Опционально. Нужен для автоматической подгрузки ваших списков (v4 user token).'
            },
            onChange: function () {
                clearCache();
            }
        });

        Lampa.SettingsApi.addParam({
            component: 'tmdb_folders',
            param: {
                name: STORAGE.manual,
                type: 'input',
                values: '',
                default: ''
            },
            field: {
                name: 'Список ID папок (через запятую)',
                description: 'Опционально. Подходит для публичных списков, если account_object_id не задан.'
            },
            onChange: function () {
                clearCache();
            }
        });

        Lampa.SettingsApi.addParam({
            component: 'tmdb_folders',
            param: {
                name: STORAGE.language,
                type: 'input',
                values: '',
                default: ''
            },
            field: {
                name: 'Язык TMDB (BCP-47)',
                description: 'Например ru-RU, en-US. По умолчанию определяется языком Lampa.'
            },
            onChange: function () {
                clearCache();
            }
        });

        Lampa.SettingsApi.addParam({
            component: 'tmdb_folders',
            param: {
                name: STORAGE.cache_ttl,
                type: 'select',
                values: { 1: '1 мин', 5: '5 мин', 10: '10 мин', 30: '30 мин', 60: '60 мин' },
                default: 10
            },
            field: {
                name: 'TTL кэша',
                description: 'Как долго плагин хранит ответы TMDB в памяти.'
            },
            onChange: function () {
                clearCache();
            }
        });
    }

    // ---------- left menu integration ----------

    function findMenuList() {
        if (typeof $ !== 'function') return null;
        // Try the canonical selector first; fall back to a couple of common
        // variants used by Lampa forks. We always pick the first `.menu__list`
        // (the user-content section, above the system buttons).
        var $primary = $('.menu .menu__list').eq(0);
        if ($primary && $primary.length) return $primary;
        var $alt = $('.menu__list').eq(0);
        if ($alt && $alt.length) return $alt;
        return null;
    }

    function buildMenuButton() {
        return $(
            '<li class="menu__item selector" data-action="tmdb_folders">' +
            '<div class="menu__ico">' +
            '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">' +
            '<path d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2z" fill="currentColor"/>' +
            '</svg>' +
            '</div>' +
            '<div class="menu__text">' + MANIFEST.name + '</div>' +
            '</li>'
        );
    }

    function addMenuButton() {
        var $list = findMenuList();
        if (!$list) {
            safeWarn('addMenuButton: no .menu__list found yet');
            return false;
        }
        // If the button is already attached somewhere in the menu, do nothing.
        if ($('.menu li[data-action="tmdb_folders"]').length) {
            safeLog('addMenuButton: button already present');
            return true;
        }

        var $button = buildMenuButton();
        $button.on('hover:enter', function () {
            if (!hasToken()) {
                noty('Сначала задайте TMDB v4 токен в Настройки → ' + MANIFEST.name);
                return;
            }

            try {
                Lampa.Activity.push({
                    url: '',
                    title: MANIFEST.name,
                    component: 'tmdb_folders',
                    page: 1
                });
            } catch (err) {
                safeError('Activity.push failed:', err && err.message);
            }
        });

        $list.append($button);
        safeLog('menu button appended');
        return true;
    }

    function scheduleMenuAttach() {
        // Try immediately; if the menu isn't in the DOM yet, retry on a short
        // interval (covers slow Lampa boot and async plugin loading).
        if (addMenuButton()) return;

        var attempts = 0;
        var max = 60; // 60 * 500ms = 30s
        var timer = setInterval(function () {
            attempts += 1;
            if (addMenuButton() || attempts >= max) {
                clearInterval(timer);
                if (attempts >= max) safeWarn('gave up after ' + max + ' attempts, menu DOM never appeared');
            }
        }, 500);
    }

    // ---------- bootstrap ----------

    function bootstrap() {
        try {
            if (!Lampa.Component || typeof Lampa.Component.add !== 'function') {
                safeError('Lampa.Component.add is missing — incompatible Lampa version');
                return;
            }
            Lampa.Component.add('tmdb_folders', foldersComponent);
            Lampa.Component.add('tmdb_folder_content', folderContentComponent);

            registerSettings();

            scheduleMenuAttach();

            // Re-attach on app:ready (covers the case where Lampa rebuilds the
            // menu after our initial attempts).
            try {
                if (Lampa.Listener && typeof Lampa.Listener.follow === 'function') {
                    Lampa.Listener.follow('app', function (e) {
                        if (e && e.type === 'ready') {
                            safeLog('app:ready fired, re-attaching menu button');
                            scheduleMenuAttach();
                        }
                    });
                    Lampa.Listener.follow('menu', function (e) {
                        if (e && (e.type === 'end' || e.type === 'start')) {
                            safeLog('menu:' + e.type + ' fired, re-attaching menu button');
                            scheduleMenuAttach();
                        }
                    });
                }
            } catch (listenErr) {
                safeWarn('failed to subscribe to Lampa.Listener:', listenErr && listenErr.message);
            }

            safeLog('bootstrap finished');
        } catch (err) {
            safeError('bootstrap failed:', err && err.message, err && err.stack);
        }
    }

    bootstrap();
})();
