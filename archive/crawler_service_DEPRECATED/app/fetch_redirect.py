from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx


@dataclass
class FetchResult:
    ok: bool
    status: str
    status_code: int
    final_url: str
    content_bytes: bytes
    content_type: str
    redirect_chain: List[Dict[str, str | int]] = field(default_factory=list)
    blocked_to: Optional[str] = None
    auth_info: Optional[Dict[str, str]] = None


def _normalize_content_type(value: str) -> str:
    return (value or "").split(";")[0].strip().lower()


def _match_auth_redirect(target: str) -> Optional[str]:
    parsed = urlparse(target)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    query = (parsed.query or "").lower()
    if host == "cas.byu.edu" and "/cas/login" in path:
        return "cas.byu.edu/cas/login"
    if host == "auth.brightspot.byu.edu" and "/authenticate/login" in path:
        return "auth.brightspot.byu.edu/authenticate/login"
    if "login.byu.edu" in host:
        return "login.byu.edu"
    if "/sso/login" in path:
        return "/SSO/login"
    if "service=" in query or "returnpath=" in query:
        return "sso_query"
    return None


def fetch_resource_httpx_redirect_safe(
    url: str,
    headers: Dict[str, str],
    timeout: float,
    allow_block_cfg: Dict,
    is_allowed_fn,
    max_hops: int = 10,
) -> FetchResult:
    redirect_chain: List[Dict[str, str | int]] = []
    current_url = url
    status_code = 0
    with httpx.Client(follow_redirects=False, headers=headers, timeout=timeout) as client:
        for _ in range(max_hops):
            try:
                response = client.get(current_url)
            except httpx.HTTPError:
                return FetchResult(
                    ok=False,
                    status="http_error",
                    status_code=0,
                    final_url=current_url,
                    content_bytes=b"",
                    content_type="",
                    redirect_chain=redirect_chain,
                )
            status_code = response.status_code
            if status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    return FetchResult(
                        ok=False,
                        status="http_error",
                        status_code=status_code,
                        final_url=str(response.url),
                        content_bytes=b"",
                        content_type="",
                        redirect_chain=redirect_chain,
                    )
                target = urljoin(str(response.url), location)
                redirect_chain.append(
                    {"from": str(response.url), "to": target, "status_code": status_code}
                )
                matched_auth = _match_auth_redirect(target)
                if matched_auth:
                    parsed_target = urlparse(target)
                    return FetchResult(
                        ok=False,
                        status="auth_required",
                        status_code=status_code,
                        final_url=str(response.url),
                        content_bytes=b"",
                        content_type="",
                        redirect_chain=redirect_chain,
                        auth_info={
                            "original_url": current_url,
                            "redirect_location": target,
                            "redirect_host": (parsed_target.hostname or ""),
                            "matched_auth_pattern": matched_auth,
                        },
                    )
                if not is_allowed_fn(target, allow_block_cfg):
                    return FetchResult(
                        ok=False,
                        status="blocked_redirect",
                        status_code=status_code,
                        final_url=str(response.url),
                        content_bytes=b"",
                        content_type="",
                        redirect_chain=redirect_chain,
                        blocked_to=target,
                    )
                current_url = target
                continue
            if 200 <= status_code < 300:
                content_type = _normalize_content_type(
                    response.headers.get("content-type", "application/octet-stream")
                )
                return FetchResult(
                    ok=True,
                    status="ok",
                    status_code=status_code,
                    final_url=str(response.url),
                    content_bytes=response.content,
                    content_type=content_type,
                    redirect_chain=redirect_chain,
                )
            if status_code == 404:
                return FetchResult(
                    ok=False,
                    status="not_found",
                    status_code=status_code,
                    final_url=str(response.url),
                    content_bytes=b"",
                    content_type="",
                    redirect_chain=redirect_chain,
                )
            if 400 <= status_code < 600:
                return FetchResult(
                    ok=False,
                    status="http_error",
                    status_code=status_code,
                    final_url=str(response.url),
                    content_bytes=b"",
                    content_type="",
                    redirect_chain=redirect_chain,
                )
            return FetchResult(
                ok=False,
                status="http_error",
                status_code=status_code,
                final_url=str(response.url),
                content_bytes=b"",
                content_type="",
                redirect_chain=redirect_chain,
            )
    return FetchResult(
        ok=False,
        status="http_error",
        status_code=status_code,
        final_url=current_url,
        content_bytes=b"",
        content_type="",
        redirect_chain=redirect_chain,
    )
