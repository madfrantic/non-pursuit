import asyncio
import logging
import time

import pytest

import recon_engine


class FakeContent:
    def __init__(self, body=b"profile"):
        self.body = body

    async def iter_chunked(self, size):
        yield self.body


class FakeResponse:
    def __init__(self, status, headers=None, body=b"profile"):
        self.status = status
        self.headers = headers or {}
        self.url = "https://example.test/profile"
        self.charset = "utf-8"
        self.content = FakeContent(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class RecordingSession:
    """A ClientSession stand-in that records whether it was closed."""

    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def _fetch(session, **overrides):
    kwargs = {
        "rate_limiter": recon_engine.TokenBucketRateLimiter(0),
        "proxy_rotator": recon_engine.ProxyRotator(),
        "socks_sessions": {}, "timeout": 1, "max_retries": 2, "backoff_base": 0,
    }
    kwargs.update(overrides)
    return recon_engine._request_with_retries(
        session,
        {"method": "GET", "url": "https://example.test/profile", "body": None},
        {}, **kwargs)


def test_rate_limiter_queues_concurrent_requests_for_one_domain():
    async def exercise():
        limiter = recon_engine.TokenBucketRateLimiter(100, {"example.test"})
        started = []

        async def request():
            await limiter.acquire("https://example.test/profile")
            started.append(time.monotonic())

        await asyncio.gather(request(), request())
        return started

    started = asyncio.run(exercise())
    assert len(started) == 2
    assert started[1] - started[0] >= 0.008


def test_proxy_rotator_falls_back_to_direct_requests_without_configuration():
    rotator = recon_engine.ProxyRotator(recon_engine.load_proxy_urls(""))

    assert rotator.proxies == ()
    assert rotator.next() is None


def test_retry_backoff_honors_retry_after_for_429_and_503(monkeypatch):
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    async def exercise():
        session = FakeSession([
            FakeResponse(429, {"Retry-After": "0"}),
            FakeResponse(503),
            FakeResponse(200),
        ])
        return await _fetch(session), session

    monkeypatch.setattr(recon_engine.asyncio, "sleep", fake_sleep)
    result, session = asyncio.run(exercise())

    assert result["status"] == 200
    assert result["attempts"] == 3
    assert len(session.calls) == 3
    assert delays == [0, 0]


def test_retryable_response_body_is_not_read(monkeypatch):
    """A 429 body is about to be discarded; reading it is pure latency."""
    async def fake_sleep(delay):
        return None

    reads = []

    class CountingContent(FakeContent):
        async def iter_chunked(self, size):
            reads.append(size)
            yield self.body

    async def exercise():
        throttled = FakeResponse(429, {"Retry-After": "0"})
        throttled.content = CountingContent()
        served = FakeResponse(200)
        served.content = CountingContent()
        return await _fetch(FakeSession([throttled, served]))

    monkeypatch.setattr(recon_engine.asyncio, "sleep", fake_sleep)
    result = asyncio.run(exercise())

    assert result["status"] == 200
    assert len(reads) == 1


def test_last_attempt_returns_the_throttled_response_rather_than_raising(monkeypatch):
    """Retries exhausted is a result the caller classifies, not an exception."""
    async def fake_sleep(delay):
        return None

    monkeypatch.setattr(recon_engine.asyncio, "sleep", fake_sleep)
    result = asyncio.run(_fetch(
        FakeSession([FakeResponse(429), FakeResponse(429)]), max_retries=1))

    assert result["status"] == 429
    assert result["attempts"] == 2


def test_socks_sessions_close_when_a_scan_is_cancelled(monkeypatch):
    """A cancelled scan must not leak its pooled proxy sessions."""
    pooled = RecordingSession()

    class ExplodingRotator(recon_engine.ProxyRotator):
        async def open_socks_sessions(self):
            return {"socks5://proxy.test:1080": pooled}

    async def boom(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(recon_engine, "_check_site", boom)

    sites = [{"name": "ExampleSocial", "uri_check": "https://example.test/{account}"}]
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(recon_engine.scan(
            "testsubject", sites, proxy_rotator=ExplodingRotator(),
            rate_limiter=recon_engine.TokenBucketRateLimiter(0)))

    assert pooled.closed is True


def test_every_row_carries_a_retry_count(monkeypatch):
    """Rows have one shape: consumers must not have to guess whether the key is there."""
    monkeypatch.setattr(recon_engine, "validate_target_url", lambda url: "blocked host")

    sites = [{"name": "ExampleSocial", "uri_check": "https://example.test/{account}"}]
    rows = asyncio.run(recon_engine.scan(
        "testsubject", sites, rate_limiter=recon_engine.TokenBucketRateLimiter(0)))

    assert [row["retries"] for row in rows] == [0]


def test_socks_proxies_without_the_connector_are_reported(monkeypatch, caplog):
    """Falling back to direct requests silently would defeat the point."""
    monkeypatch.setattr(recon_engine, "ProxyConnector", None)
    rotator = recon_engine.ProxyRotator(
        recon_engine.load_proxy_urls("socks5://proxy.test:1080"))

    with caplog.at_level(logging.WARNING):
        sessions = asyncio.run(rotator.open_socks_sessions())

    assert sessions == {}
    assert "DIRECT" in caplog.text