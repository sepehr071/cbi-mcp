"""Shared async HTTP client for the public pages of www.cbi.ir (Central Bank of Iran).

cbi.ir is a classic ASP.NET site that serves HTML only, so `get` and `post_form` return page text. Both cap
concurrency, cache briefly and turn HTTP failures into `ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any

import httpx
from curl_cffi.const import CurlOpt
from httpx_curl_cffi import AsyncCurlTransport
from mcp.server.mcpserver.exceptions import ToolError

SITE = "https://www.cbi.ir"
# Only the main site answers; tsd.cbi.ir and tsdview.cis.cbi.ir (time-series bank) timed out on every line tried.
HOSTS = {"www.cbi.ir"}
HEADERS = {"Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8"}
# cbi.ir serves pages, so every call is a navigation. User-Agent, sec-ch-ua and TLS come from the impersonated Chrome.
NAV_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,"
    "application/signed-exchange;v=b3;q=0.7",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "upgrade-insecure-requests": "1",
    "priority": "u=0, i",
}

MAX_CONCURRENCY = 2
JITTER = (0.2, 0.6)  # seconds before each uncached call
MAX_RETRIES = 2
MAX_RETRY_WAIT = 10
RETRY_DELAY = 1.0  # seconds, doubled per retry
CACHE_TTL = 300  # seconds; rates change once a day
CACHE_MAX = 64
MAX_BODY = 5_000_000  # bytes; a normal page is 60-250 KB

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None
_proxy_index = 0
_retired: set[asyncio.Task] = set()
_cache: dict[tuple, tuple[float, str | bytes]] = {}


class _NavTransport(AsyncCurlTransport):
    """The Chrome profile sends reload/navigation extras; a None value makes curl drop them where Chrome would not."""

    def _create_request_params(self, req: httpx.Request) -> dict[str, Any]:
        # The parent drops "accept: */*" as an httpx default; curl would then send Chrome's page Accept instead.
        accept = req.headers.get("accept")
        p = super()._create_request_params(req)
        extra = []
        if req.method == "GET":
            extra.append((b"cache-control", None))  # Chrome sends max-age=0 only on a reload or form POST
        if req.headers.get("sec-fetch-mode") != "navigate":
            extra += [(b"upgrade-insecure-requests", None), (b"sec-fetch-user", None)]
        p["headers"] = [*p["headers"], *extra, *([(b"accept", b"*/*")] if accept == "*/*" else [])]
        return p


class ApiError(ToolError):
    """A failed upstream call. `status` is the HTTP status, or None for network errors."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client."""
    global _transport, _client, _limit, _proxy_index
    _transport, _client, _limit, _proxy_index = transport, None, None, 0
    _cache.clear()


def _proxies() -> list[str]:
    """CBI_MCP_PROXY: one proxy URL, or a comma-separated list used one at a time."""
    return [p.strip() for p in os.environ.get("CBI_MCP_PROXY", "").split(",") if p.strip()]


def _next_proxy(client: httpx.AsyncClient) -> None:
    """Sticky pool: move to the next proxy after a block or connect error; the next call rebuilds the client.

    Only the client that failed may rotate. The old client is closed late because other requests may still use it.
    """
    global _client, _limit, _proxy_index
    if len(_proxies()) < 2 or client is not _client:
        return
    _client, _limit, _proxy_index = None, None, _proxy_index + 1
    t = asyncio.create_task(_close_later(client))
    _retired.add(t)
    t.add_done_callback(_retired.discard)


async def _close_later(client: httpx.AsyncClient) -> None:
    await asyncio.sleep(60)  # longer than the 40 s request timeout
    await client.aclose()


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        # System proxy settings are ignored; the proxy only comes from CBI_MCP_PROXY. curl_cffi presents
        # Chrome's TLS/HTTP2 fingerprint and default headers.
        proxies = _proxies()
        _client = httpx.AsyncClient(
            transport=_transport
            or _NavTransport(
                impersonate="chrome",
                default_headers=True,
                curl_options={CurlOpt.NOPROXY: "*"},  # httpx-curl-cffi drops its own NOPROXY unless this is non-empty
                proxy=proxies[_proxy_index % len(proxies)] if proxies else None,
                max_connections=2 * MAX_CONCURRENCY,
            ),
            headers=HEADERS,
            timeout=httpx.Timeout(40, connect=15),
            follow_redirects=True,
            trust_env=False,
        )
        _limit = asyncio.Semaphore(MAX_CONCURRENCY)
    assert _limit is not None
    return _client, _limit


def url_for(path: str) -> str:
    return path if path.startswith("http") else SITE + path


async def get(path: str, params: dict[str, Any] | None = None) -> str:
    """GET a page the way a browser does and return its text (cached for CACHE_TTL seconds)."""
    return await _call("GET", path, params=params)


async def post_form(path: str, form: dict[str, str]) -> str:
    """POST an ASP.NET form (the year selector on a page), echoing the page's hidden state fields. Not cached."""
    return await _call("POST", path, form=form)


async def get_file(path: str) -> bytes:
    """GET a report file (Excel, PDF) and return its bytes (cached like pages)."""
    return await _call("GET", path, raw=True)


async def _call(method: str, path: str, params=None, form=None, raw=False):
    url = url_for(path)
    host = httpx.URL(url).host
    if host not in HOSTS:
        raise ApiError(f"Refusing to call unexpected host {host}.")
    key = (url, tuple(sorted((params or {}).items())), raw)
    if method == "GET" and (hit := _cache.get(key)) and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    for attempt in range(MAX_RETRIES + 1):
        last = attempt == MAX_RETRIES
        wait = RETRY_DELAY * 2**attempt * random.uniform(1, 1.5)
        try:
            r, content = await _send(method, url, params, form, host, raw)
        except ApiError as e:
            if last or e.status is not None:
                raise
            await asyncio.sleep(min(wait, MAX_RETRY_WAIT))
            continue
        if (r.status_code == 429 or r.status_code >= 500) and not last:
            await asyncio.sleep(min(_retry_after(r) or wait, MAX_RETRY_WAIT))
            continue
        break
    if r.status_code >= 400:
        raise ApiError(_status_message(r.status_code, host), r.status_code)
    text = content if raw else content.decode(r.encoding or "utf-8", errors="replace")
    if method == "GET":
        if len(_cache) >= CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic(), text)
    return text


def _headers(method: str, url: str, file: bool) -> dict[str, str]:
    """Chrome's headers for the three kinds of call: a typed URL, a link click (file download), a form submit."""
    if method == "POST":  # the year selector is an ASP.NET postback of the page itself
        return {
            **NAV_HEADERS,
            "Referer": url,
            "Origin": SITE,
            "sec-fetch-site": "same-origin",
            "sec-fetch-user": "?1",
            "Cache-Control": "max-age=0",
        }
    if file:
        return {**NAV_HEADERS, "Referer": SITE + "/", "sec-fetch-site": "same-origin", "sec-fetch-user": "?1"}
    return {**NAV_HEADERS, "sec-fetch-site": "none", "sec-fetch-user": "?1"}


async def _send(method, url, params, form, host, raw=False) -> tuple[httpx.Response, bytes]:
    client, limit = _get_client()
    headers = _headers(method, url, raw)
    try:
        async with limit:
            await asyncio.sleep(random.uniform(*JITTER))
            async with client.stream(method, url, params=params, data=form, headers=headers) as r:
                content = await _read_capped(r, host)
        if r.status_code in (403, 429):
            _next_proxy(client)  # blocked: try the next proxy on the following call
        return r, content
    except httpx.TimeoutException as e:
        raise ApiError(f"{host} did not answer in time. Try again in a moment.") from e
    except httpx.RequestError as e:
        if isinstance(e, httpx.ConnectError | httpx.ProxyError):
            _next_proxy(client)
        raise ApiError(
            f"Could not reach {host} ({type(e).__name__}). Check the internet connection "
            "(or the CBI_MCP_PROXY setting if you use one)."
        ) from e


async def _read_capped(r: httpx.Response, host: str) -> bytes:
    """Read the body, refusing anything over MAX_BODY so one huge answer cannot flood memory."""
    buf = bytearray()
    async for chunk in r.aiter_bytes():
        buf += chunk
        if len(buf) > MAX_BODY:
            raise ApiError(f"{host} sent a response over {MAX_BODY // 1_000_000} MB; refused.", r.status_code)
    return bytes(buf)


def _retry_after(r: httpx.Response) -> float | None:
    """Seconds from a numeric Retry-After on a 429/503, else None (HTTP-date form is ignored)."""
    v = r.headers.get("retry-after", "")
    return float(v) if r.status_code in (429, 503) and v.replace(".", "", 1).isdigit() else None


def _status_message(code: int, host: str) -> str:
    if code == 403:
        return f"{host} blocked the request (HTTP 403). Try another network or set CBI_MCP_PROXY."
    if code == 404:
        return f"{host} found nothing at that address (HTTP 404). Check the id or date."
    if code == 429:
        return f"{host} is rate limiting requests (HTTP 429). Wait a minute before retrying."
    if code >= 500:
        return f"{host} had a server error (HTTP {code}). Try again later."
    return f"{host} rejected the request (HTTP {code})."
