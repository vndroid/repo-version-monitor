from __future__ import annotations

import httpcore
import httpx

from repo_version_monitor.config import ProxyConfig
from repo_version_monitor.http_client import describe, new_async_client


def _pool(client: httpx.AsyncClient):
    return client._transport._pool


def test_no_proxy_keeps_the_default_transport() -> None:
    # trust_env stays on, so *_PROXY environment variables still work.
    for proxy in (None, ProxyConfig(), ProxyConfig(enabled=False, type="socks5", host="h")):
        client = new_async_client(proxy)
        assert isinstance(_pool(client), httpcore.AsyncConnectionPool)


def test_http_proxy() -> None:
    client = new_async_client(ProxyConfig(enabled=True, type="http", host="127.0.0.1", port=7890))

    pool = _pool(client)
    assert isinstance(pool, httpcore.AsyncHTTPProxy)
    assert bytes(pool._proxy_url.host) == b"127.0.0.1"
    assert pool._proxy_url.port == 7890


def test_configured_proxy_ignores_environment_proxies(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy:9999")

    client = new_async_client(ProxyConfig(enabled=True, type="http", host="127.0.0.1", port=7890))

    assert client._mounts == {}
    assert bytes(_pool(client)._proxy_url.host) == b"127.0.0.1"


def test_socks5_proxy_with_credentials() -> None:
    proxy = ProxyConfig(
        enabled=True,
        type="socks5",
        host="127.0.0.1",
        port=1080,
        username="user",
        password="secret",
    )

    client = new_async_client(proxy)

    pool = _pool(client)
    assert isinstance(pool, httpcore.AsyncSOCKSProxy)
    assert pool._proxy_auth == (b"user", b"secret")


def test_describe() -> None:
    assert "not set" in describe(None)
    assert "not set" in describe(ProxyConfig())
    assert describe(ProxyConfig(enabled=True, host="127.0.0.1", port=7890)) == (
        "http://127.0.0.1:7890"
    )
    assert describe(ProxyConfig(enabled=True, type="socks5", host="127.0.0.1", port=1080)) == (
        "socks5://127.0.0.1:1080"
    )
    assert "(authenticated)" in describe(
        ProxyConfig(enabled=True, host="127.0.0.1", username="user", password="secret")
    )
