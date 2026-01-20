import httpx

from app.fetch_redirect import fetch_resource_httpx_redirect_safe
from app.discovery import is_allowed


def _patch_client(monkeypatch, transport):
    original_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs.setdefault("transport", transport)
        return original_client(*args, **kwargs)

    monkeypatch.setattr("app.fetch_redirect.httpx.Client", client_factory)


def test_redirect_allowed(monkeypatch):
    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(301, headers={"Location": "/next"})
        return httpx.Response(200, content=b"ok", headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    _patch_client(monkeypatch, transport)

    allow_block_cfg = {"allowed_domains": ["example.com"]}
    result = fetch_resource_httpx_redirect_safe(
        "https://example.com/start",
        headers={},
        timeout=5.0,
        allow_block_cfg=allow_block_cfg,
        is_allowed_fn=is_allowed,
    )
    assert result.ok is True
    assert result.final_url == "https://example.com/next"
    assert result.status_code == 200


def test_redirect_blocked(monkeypatch):
    def handler(request):
        return httpx.Response(302, headers={"Location": "https://cas.byu.edu/login"})

    transport = httpx.MockTransport(handler)
    _patch_client(monkeypatch, transport)

    allow_block_cfg = {"allowed_domains": ["example.com"], "blocked_domains": ["cas.byu.edu"]}
    result = fetch_resource_httpx_redirect_safe(
        "https://example.com/start",
        headers={},
        timeout=5.0,
        allow_block_cfg=allow_block_cfg,
        is_allowed_fn=is_allowed,
    )
    assert result.ok is False
    assert result.status == "blocked_redirect"
    assert result.blocked_to == "https://cas.byu.edu/login"
