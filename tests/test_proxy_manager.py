"""Stage 14 — unit tests for the outbound proxy manager.

These pin the pure, network-free behaviour of ``ProxyManager``:

  * per-service proxy URL resolution from settings,
  * ``socks5://`` -> ``socks5h://`` rewrite so DNS resolves through the
    proxy,
  * ``http(s)://`` passthrough,
  * ``vless://`` share-link parsing,
  * ``status()`` shape over the known services.

The vless *runtime* (spawning Xray) is intentionally NOT exercised here:
it needs an external binary and a live network, so it belongs in a
manual/integration check, not the unit suite.

Settings are built with ``_env_file=None`` so neither the developer's
shell env nor a committed ``.env`` can leak proxy values into these
assertions.
"""

from __future__ import annotations

import pytest

import proxy_manager as pm_mod
from proxy_manager import KNOWN_SERVICES, ProxyManager
from settings import Settings, reload_settings


@pytest.fixture(autouse=True)
def _restore_settings_after_test():
    """Rebuild the cached settings singleton after each test so the
    isolated Settings instances used here never leak elsewhere."""
    yield
    reload_settings()


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides):
    """Point proxy_manager's module-level ``settings`` at a fresh,
    env-free Settings instance with the given field overrides."""
    s = Settings(_env_file=None, **overrides)
    monkeypatch.setattr(pm_mod, "settings", s)
    return s


def test_no_proxy_configured_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    assert ProxyManager().get_requests_proxies("tmdb") is None


def test_none_service_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    assert ProxyManager().get_requests_proxies(None) is None


def test_socks5_is_rewritten_to_socks5h(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, proxy_kinopub="socks5://1.2.3.4:1080")
    proxies = ProxyManager().get_requests_proxies("kinopub")
    assert proxies == {
        "http": "socks5h://1.2.3.4:1080",
        "https": "socks5h://1.2.3.4:1080",
    }


def test_http_proxy_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, proxy_rutor="http://user:pass@1.2.3.4:8080")
    proxies = ProxyManager().get_requests_proxies("rutor")
    assert proxies == {
        "http": "http://user:pass@1.2.3.4:8080",
        "https": "http://user:pass@1.2.3.4:8080",
    }


def test_blank_proxy_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, proxy_rezka="   ")
    assert ProxyManager().get_requests_proxies("rezka") is None


def test_parse_vless_url_extracts_fields() -> None:
    url = (
        "vless://11111111-2222-3333-4444-555555555555@example.com:443"
        "?type=tcp&security=reality&sni=foo.example&pbk=PUBKEY&sid=ab12&fp=chrome#my-node"
    )
    info = ProxyManager().parse_vless_url(url)
    assert info["uuid"] == "11111111-2222-3333-4444-555555555555"
    assert info["address"] == "example.com"
    assert info["port"] == 443
    assert info["security"] == "reality"
    assert info["sni"] == "foo.example"
    assert info["pbk"] == "PUBKEY"
    assert info["tag"] == "my-node"


def test_parse_vless_url_rejects_non_vless() -> None:
    with pytest.raises(ValueError):
        ProxyManager().parse_vless_url("socks5://1.2.3.4:1080")


def test_status_reports_all_known_services(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, proxy_tmdb="socks5://9.9.9.9:1080")
    st = ProxyManager().status()
    assert set(st["services"]) == set(KNOWN_SERVICES)
    assert st["services"]["tmdb"] == {"configured": True, "kind": "socks"}
    # An unconfigured service is still reported, just flagged off.
    assert st["services"]["rezka"]["configured"] is False
    assert "xray_processes" in st
