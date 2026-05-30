"""Stage 14 — 🔒 Outbound proxy manager (VLESS + SOCKS5/HTTP).

Lets each upstream metadata/torrent source be routed through its own
proxy. Two proxy flavours are supported per service:

  * A ready SOCKS5/HTTP proxy URL (``socks5://host:port``,
    ``http://user:pass@host:port``) — used directly by ``requests``.
  * A ``vless://`` share link — par2 spins up a local Xray (or sing-box)
    process exposing a local SOCKS5 inbound, and routes ``requests``
    through ``socks5h://127.0.0.1:<port>``.

par2's HTTP clients are synchronous (``requests`` + ``requests_cache``),
so this manager returns a ``proxies`` dict suitable for
``session.get(..., proxies=...)`` rather than an httpx transport.

SOCKS support in requests needs PySocks (``pip install requests[socks]``
or ``pip install PySocks``).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from urllib.parse import parse_qs, unquote, urlparse

from settings import settings

logger = logging.getLogger("parsclode.proxy")

# Services that can be individually proxied. Each maps to a
# ``proxy_<service>`` settings field.
KNOWN_SERVICES = ("rezka", "kinopub", "rutor", "tmdb", "kinopoisk", "poiskkino")

_XRAY_CANDIDATES = ("xray", "/usr/local/bin/xray", "sing-box", "/usr/bin/sing-box")


class ProxyManager:
    def __init__(self):
        self.logger = logger
        self._lock = threading.Lock()
        # vless_url -> {"proc": Popen, "port": int}
        self._xray: dict[str, dict] = {}

    # ── service config ───────────────────────────────────────
    def _service_proxy_url(self, service: str | None) -> str | None:
        if not service:
            return None
        raw = getattr(settings, f"proxy_{service}", None)
        if raw and str(raw).strip():
            return str(raw).strip()
        return None

    # ── binary detection ───────────────────────────────────
    def detect_xray_binary(self) -> str | None:
        configured = settings.xray_binary
        if configured:
            found = shutil.which(configured) or (configured if os.path.exists(configured) else None)
            if found:
                return found
        for cand in _XRAY_CANDIDATES:
            found = shutil.which(cand) or (cand if os.path.exists(cand) else None)
            if found:
                return found
        return None

    # ── VLESS parsing ──────────────────────────────────────
    def parse_vless_url(self, url: str) -> dict:
        if not url.startswith("vless://"):
            raise ValueError("not a vless:// url")
        parsed = urlparse(url)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        return {
            "uuid": unquote(parsed.username or ""),
            "address": parsed.hostname or "",
            "port": int(parsed.port or 443),
            "type": params.get("type", "tcp"),
            "security": params.get("security", "none"),
            "sni": params.get("sni", ""),
            "flow": params.get("flow", ""),
            "pbk": params.get("pbk", ""),
            "sid": params.get("sid", ""),
            "fp": params.get("fp", ""),
            "path": unquote(params.get("path", "")),
            "host": params.get("host", ""),
            "tag": unquote(parsed.fragment or ""),
        }

    def _free_port(self, preferred: int) -> int:
        for port in (preferred, 0):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(("127.0.0.1", port))
                actual = s.getsockname()[1]
                s.close()
                return actual
            except OSError:
                continue
        return preferred

    def _build_xray_config(self, vless: dict, local_port: int) -> dict:
        stream: dict = {"network": vless["type"], "security": vless["security"]}
        if vless["security"] == "reality":
            stream["realitySettings"] = {
                "serverName": vless["sni"],
                "publicKey": vless["pbk"],
                "shortId": vless["sid"],
                "fingerprint": vless["fp"] or "chrome",
            }
        elif vless["security"] == "tls":
            stream["tlsSettings"] = {
                "serverName": vless["sni"],
                "fingerprint": vless["fp"] or "chrome",
            }
        if vless["type"] == "ws":
            stream["wsSettings"] = {
                "path": vless["path"] or "/",
                "headers": {"Host": vless["host"]} if vless["host"] else {},
            }
        if vless["type"] == "grpc":
            stream["grpcSettings"] = {"serviceName": vless["path"].lstrip("/")}

        return {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "listen": "127.0.0.1",
                    "port": local_port,
                    "protocol": "socks",
                    "settings": {"udp": True},
                }
            ],
            "outbounds": [
                {
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": vless["address"],
                                "port": vless["port"],
                                "users": [
                                    {
                                        "id": vless["uuid"],
                                        "encryption": "none",
                                        "flow": vless["flow"],
                                    }
                                ],
                            }
                        ]
                    },
                    "streamSettings": stream,
                }
            ],
        }

    def ensure_xray_running(self, vless_url: str) -> int:
        """Start (or reuse) an Xray process for ``vless_url``; return SOCKS port."""
        with self._lock:
            existing = self._xray.get(vless_url)
            if existing and existing["proc"].poll() is None:
                return existing["port"]

            binary = self.detect_xray_binary()
            if not binary:
                raise RuntimeError(
                    "Xray/sing-box binary not found. Install xray-core and/or set XRAY_BINARY."
                )
            vless = self.parse_vless_url(vless_url)
            base = settings.xray_port_base + len(self._xray)
            port = self._free_port(base)
            config = self._build_xray_config(vless, port)

            cfg_dir = os.path.join(settings.app_data_dir, "xray")
            os.makedirs(cfg_dir, exist_ok=True)
            cfg_path = os.path.join(cfg_dir, f"xray_{port}.json")
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)

            proc = subprocess.Popen(
                [binary, "run", "-c", cfg_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Give the inbound a moment to bind before first use.
            time.sleep(1.0)
            if proc.poll() is not None:
                raise RuntimeError(f"Xray exited immediately (config: {cfg_path})")
            self._xray[vless_url] = {"proc": proc, "port": port}
            self.logger.info("[PROXY] started xray for %s on 127.0.0.1:%s", vless["address"], port)
            return port

    # ── requests integration ─────────────────────────────────
    def get_requests_proxies(self, service: str | None) -> dict | None:
        url = self._service_proxy_url(service)
        if not url:
            return None
        try:
            if url.startswith("vless://"):
                port = self.ensure_xray_running(url)
                target = f"socks5h://127.0.0.1:{port}"
            elif url.startswith("socks"):
                # socks5:// -> socks5h:// so DNS resolves through the proxy.
                target = url.replace("socks5://", "socks5h://", 1)
            else:
                target = url  # http:// / https://
            return {"http": target, "https": target}
        except Exception as e:
            self.logger.error("[PROXY] proxy setup failed for %s: %s", service, e)
            return None

    # ── diagnostics ───────────────────────────────────────
    def test_proxy(self, service: str) -> dict:
        url = self._service_proxy_url(service)
        if not url:
            return {
                "service": service,
                "ok": False,
                "configured": False,
                "error": "no proxy configured",
            }
        proxies = self.get_requests_proxies(service)
        if not proxies:
            return {
                "service": service,
                "ok": False,
                "configured": True,
                "error": "could not initialise proxy",
            }
        try:
            import requests

            start = time.time()
            resp = requests.get("https://api.ipify.org?format=json", proxies=proxies, timeout=15)
            latency = round((time.time() - start) * 1000)
            try:
                ip = resp.json().get("ip", "")
            except Exception:
                ip = resp.text.strip()
            return {
                "service": service,
                "ok": resp.status_code == 200,
                "configured": True,
                "ip": ip,
                "latency_ms": latency,
                "status_code": resp.status_code,
            }
        except Exception as e:
            return {
                "service": service,
                "ok": False,
                "configured": True,
                "error": f"{type(e).__name__}: {e}",
            }

    def status(self) -> dict:
        services = {}
        for svc in KNOWN_SERVICES:
            url = self._service_proxy_url(svc)
            kind = None
            if url:
                if url.startswith("vless://"):
                    kind = "vless"
                elif url.startswith("socks"):
                    kind = "socks"
                elif url.startswith("http"):
                    kind = "http"
            services[svc] = {"configured": bool(url), "kind": kind}
        running = []
        with self._lock:
            for _vless_url, info in self._xray.items():
                running.append({"port": info["port"], "alive": info["proc"].poll() is None})
        return {
            "services": services,
            "xray_binary": self.detect_xray_binary(),
            "xray_configured": settings.xray_binary,
            "xray_processes": running,
        }

    def stop_all(self) -> None:
        with self._lock:
            for _vless_url, info in list(self._xray.items()):
                proc = info["proc"]
                if proc.poll() is None:
                    try:
                        proc.terminate()
                    except Exception as e:
                        self.logger.debug("[PROXY] terminate failed: %s", e)
            self._xray.clear()


proxy_manager = ProxyManager()
