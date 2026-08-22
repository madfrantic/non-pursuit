"""Structured data-broker scanning facade.

The existing :mod:`broker_probe` owns the broker-specific rules and verified
contact registry. This module exposes the common finding contract used by
the recon engine while keeping broker searches separate from social-site
checks. A broker result is tri-state: ``exists`` is ``None`` when the broker
was blocked or requires a manual check.
"""
from __future__ import annotations

from typing import Awaitable, Callable

import broker_probe


def _exists(verdict: str):
    if verdict == broker_probe.RECORD_FOUND:
        return True
    if verdict == broker_probe.NO_RECORD:
        return False
    return None


def normalize_result(row: dict) -> dict:
    """Convert a broker-probe row to the shared exposure finding contract."""
    record_urls = row.get("record_urls") or []
    url = record_urls[0] if record_urls else row.get("search_url", "")
    return {
        "platform": row.get("broker", ""),
        "url": url,
        "exists": _exists(row.get("verdict")),
        "http_status": row.get("http_status"),
        "response_time_ms": row.get("response_time_ms"),
        "metadata": {
            "kind": "data_broker_record",
            "verdict": row.get("verdict", ""),
            "reason": row.get("reason", ""),
            "search_url": row.get("search_url", ""),
            "record_urls": record_urls,
            "compliance_email": row.get("compliance_email", ""),
            "optout_url": row.get("optout_url", ""),
            "contact_verified": bool(row.get("contact_verified")),
            "last_verified": row.get("last_verified", ""),
            "notes": row.get("notes", ""),
        },
    }


class BrokerScanner:
    """Protocol-based broker scanner with an injectable probe function."""

    def __init__(self, probe: Callable[..., Awaitable[list]] | None = None):
        self._probe = probe or broker_probe.probe_brokers

    async def scan(self, subject: dict, brokers: list | None = None, **kwargs) -> list:
        rows = await self._probe(subject, brokers, **kwargs)
        return [normalize_result(row) for row in rows]


async def scan_brokers(subject: dict, brokers: list | None = None, **kwargs) -> list:
    """Scan configured brokers and return shared-contract findings."""
    return await BrokerScanner().scan(subject, brokers, **kwargs)


def scan_brokers_sync(subject: dict, brokers: list | None = None, **kwargs) -> list:
    """Synchronous adapter for CLI and batch callers."""
    rows = broker_probe.probe_brokers_sync(subject, brokers, **kwargs)
    return [normalize_result(row) for row in rows]
