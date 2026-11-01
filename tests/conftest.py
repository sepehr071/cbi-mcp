import json
from pathlib import Path

import httpx
import pytest
from mcp import Client

from cbi_mcp import http
from cbi_mcp.server import mcp

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_http_client(monkeypatch):
    """The shared httpx client is bound to one event loop; each test gets a new loop."""
    monkeypatch.setattr(http, "RETRY_DELAY", 0)
    monkeypatch.setattr(http, "JITTER", (0, 0))
    http.set_transport(None)
    yield
    http.set_transport(None)


@pytest.fixture
def site():
    """Route table for a fake cbi.ir: site["/path"] = page text, or a callable(request) -> httpx.Response.

    Keys match the URL path only. Unknown paths return 404. Every request is appended to site.calls.
    """

    class Routes(dict):
        calls: list[httpx.Request]

    routes = Routes()
    routes.calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        routes.calls.append(request)
        target = routes.get(request.url.path)
        if target is None:
            return httpx.Response(404, text="no fake route")
        return target(request) if callable(target) else httpx.Response(200, text=target)

    http.set_transport(httpx.MockTransport(handler))
    return routes


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        call_tool = c.call_tool

        async def parsed(*args, **kwargs):
            result = await call_tool(*args, **kwargs)
            if not result.is_error:
                result.structured_content = json.loads(result.content[0].text)
            return result

        c.call_tool = parsed
        yield c
