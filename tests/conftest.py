from pathlib import Path

import aiohttp
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixturesDir() -> Path:
    return FIXTURES


class _NoWriter:
    output_size = 0


class _CompatClientResponse(aiohttp.ClientResponse):
    """aioresponses 0.7.9 builds ClientResponse without the stream_writer argument that
    aiohttp 3.14 requires; supply a stub so mocked responses can be constructed. It also
    lowercases the method, which aiohttp_client_cache then refuses to cache, so restore it."""

    def __init__(self, method, url, *, stream_writer=None, **kwargs):
        super().__init__(method.upper(), url, stream_writer=stream_writer or _NoWriter(), **kwargs)


@pytest.fixture(autouse=True)
def _aioresponsesCompat(monkeypatch):
    monkeypatch.setattr("aioresponses.core.ClientResponse", _CompatClientResponse)
