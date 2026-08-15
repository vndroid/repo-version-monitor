"""One place to build the httpx client, so every request honours [proxy].

httpx talks to an HTTP proxy with CONNECT and to a SOCKS5 proxy through
socksio. In both cases the target hostname is handed to the proxy, so DNS is
resolved on the proxy side.
"""

from __future__ import annotations

import httpx

from repo_version_monitor.config import ProxyConfig

DEFAULT_TIMEOUT = 30.0


def new_async_client(
    proxy: ProxyConfig | None = None, timeout: float = DEFAULT_TIMEOUT
) -> httpx.AsyncClient:
    """AsyncClient for all outgoing requests.

    Without an enabled proxy this is a plain client, so httpx keeps honouring
    the HTTP_PROXY/HTTPS_PROXY environment variables. With one, the configured
    proxy is used for every request and the environment is ignored.
    """
    if proxy is None or not proxy.enabled:
        return httpx.AsyncClient(timeout=timeout)

    # An explicit transport (rather than the proxy argument) keeps httpx from
    # mounting the environment proxies alongside the configured one.
    transport = httpx.AsyncHTTPTransport(
        proxy=httpx.Proxy(
            url=proxy.url,
            auth=(proxy.username, proxy.password) if proxy.username else None,
        )
    )
    return httpx.AsyncClient(timeout=timeout, transport=transport)


def describe(proxy: ProxyConfig | None) -> str:
    """One-line proxy summary for command output."""
    if proxy is None or not proxy.enabled:
        return "not set (direct connection or *_PROXY environment variables)"
    auth = " (authenticated)" if proxy.username else ""
    return f"{proxy.url}{auth}"
