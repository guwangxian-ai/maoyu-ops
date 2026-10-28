"""Extract an audit IP without trusting forwarding headers from arbitrary clients."""
from __future__ import annotations

from ipaddress import ip_address
import os

from fastapi import Request


def _valid_ip(value: str) -> str | None:
    try:
        return str(ip_address(value.strip()))
    except ValueError:
        return None


def get_client_ip(request: Request) -> str:
    """Trust only explicitly configured proxy peers; never take the leftmost XFF.

    Nginx overwrites X-Real-IP and appends its caller to X-Forwarded-For.
    TRUSTED_PROXY_IPS lists exact peer IPs, defaulting to loopback. Uvicorn may
    already have resolved the peer using its own trusted proxy configuration.
    """
    peer = _valid_ip(request.client.host) if request.client else None
    trusted = {
        _valid_ip(value)
        for value in os.environ.get("TRUSTED_PROXY_IPS", "127.0.0.1,::1").split(",")
    } - {None}
    if peer and peer in trusted:
        real_ip = _valid_ip(request.headers.get("X-Real-IP", ""))
        if real_ip:
            return real_ip
        forwarded = _valid_ip(request.headers.get("X-Forwarded-For", "").split(",")[-1])
        if forwarded:
            return forwarded
    return peer or "-"
