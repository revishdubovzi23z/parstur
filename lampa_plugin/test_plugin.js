/*!
 * Smoke test for tmdb_folders.js — verifies the plugin loads without throwing,
 * registers both components, the settings page, and hooks the `app:ready`
 * listener so a menu button would appear at runtime.
 *
 * Runs under plain Node (no browser): `node lampa_plugin/test_plugin.js`.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const pluginPath = path.join(__dirname, 'tmdb_folders.js');
const pluginSrc = fs.readFileSync(pluginPath, 'utf8');

let assertions = 0;
function assert(cond, msg) {
    assertions += 1;
    if (!cond) {
        console.error('FAIL:', msg);
        process.exit(1);
    }
}

const registeredComponents = {};
const registeredSettingsComponents = {};
const registeredSettingsParams = [];
const menuListeners = [];

class FakeJQueryCollection {
    constructor(els) { this.els = els || []; this.length = this.els.length; }
    eq() { return this; }
    on(event, cb) { menuListeners.push({ event: event, cb: cb }); return this; }
    append() { return this; }
    find() { return new FakeJQueryCollection([]); }
}

function $jq() { return new FakeJQueryCollection([]); }

const fakeReguestProto = {
    timeout() {},
    silent() {},
    clear() {}
};
function FakeReguest() { return Object.create(fakeReguestProto); }

const Lampa = {
    Manifest: { plugins: null, cub_domain: 'cub.red', app_digital: 999 },
    Storage: {
        _data: {},
        get(key, def) { return key in this._data ? this._data[key] : def; },
        set(key, value) { this._data[key] = value; },
        cache(key, _max, def) { return def; }
    },
    Reguest: FakeReguest,
    Activity: { push() {} },
    Component: {
        add(name, factory) { registeredComponents[name] = factory; }
    },
    InteractionCategory: function (object) { this.object = object; this.build = function () {}; this.empty = function () {}; },
    SettingsApi: {
        addComponent(opts) { registeredSettingsComponents[opts.component] = opts; },
        addParam(opts) { registeredSettingsParams.push(opts); }
    },
    Listener: {
        follow(name, cb) {
            if (name === 'app') {
                // Don't fire immediately — the plugin should also handle window.appready
                // synchronously. We just record the registration.
                Lampa.Listener._cbs = (Lampa.Listener._cbs || []);
                Lampa.Listener._cbs.push(cb);
            }
        }
    },
    Noty: { show() {} },
    Utils: { protocol() { return 'https://'; } },
    Lang: { translate(s) { return s; } }
};

const sandbox = {
    Lampa,
    $: $jq,
    window: {},
    console,
    setTimeout,
    clearTimeout,
    document: { addEventListener() {} }
};
sandbox.window = sandbox;
sandbox.window.tmdb_folders_ready = undefined;
sandbox.window.appready = false;

vm.createContext(sandbox);
vm.runInContext(pluginSrc, sandbox, { filename: 'tmdb_folders.js' });

assert(Lampa.Manifest.plugins && Lampa.Manifest.plugins.component === 'tmdb_folders',
    'manifest.plugins should be set');

assert(typeof registeredComponents.tmdb_folders === 'function',
    'tmdb_folders component should be registered');
assert(typeof registeredComponents.tmdb_folder_content === 'function',
    'tmdb_folder_content component should be registered');

assert(registeredSettingsComponents.tmdb_folders,
    'settings component should be registered');

const paramNames = registeredSettingsParams.map(p => p.param.name);
['tmdb_folders_token', 'tmdb_folders_account_id', 'tmdb_folders_manual_ids',
    'tmdb_folders_language', 'tmdb_folders_cache_ttl_min'].forEach(name => {
    assert(paramNames.includes(name), 'setting param ' + name + ' should be registered');
});

assert(Lampa.Listener._cbs && Lampa.Listener._cbs.length > 0,
    'should subscribe to Lampa.Listener app event when window.appready is false');

// Re-running the plugin should be a no-op thanks to window.tmdb_folders_ready guard.
vm.runInContext(pluginSrc, sandbox, { filename: 'tmdb_folders.js' });
assert(Object.keys(registeredComponents).length === 2,
    'second load should not register components again');

console.log('OK — ' + assertions + ' assertions passed');
