"""Platform-side direct client for development Engine research operations.

No Gateway, MCP, universal Engine admin token, dynamic URL, or public route.
Only a trusted Platform task controller may supply an audited ResearchGrant.
"""
from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Self
from urllib.parse import urlsplit

import httpx

from .grants import ResearchGrant

ENGINE_HEADER = "X-SeeAlpha-Engine-Contract-Version"
RESEARCH_HEADER = "X-SeeAlpha-Research-Contract-Version"
_MAX_JSON = 1_048_576
_MAX_BODY = 131_072
_MAX_SNAPSHOT = 16 * 1_048_576
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


class DirectResearchError(RuntimeError):
    """Research operation failed without exposing response bodies or credentials."""


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) or ".." in value:
        raise ValueError("Invalid research resource identifier")
    return value


class DirectEngineResearchClient:
    """Small, fixed-operation HTTP client; never put it in an Agent/Shell process.

    Engine itself independently enforces tenant, development purpose, task,
    dataset/strategy scope, expiration and the requested operation.
    """

    def __init__(
        self,
        engine_origin: str,
        grant: ResearchGrant,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        url = urlsplit(engine_origin)
        if (
            url.scheme not in {"http", "https"} or not url.hostname
            or url.username or url.password or url.path not in {"", "/"}
            or url.query or url.fragment
        ):
            raise ValueError("Engine research must use a trusted fixed origin")
        if (
            not isinstance(grant, ResearchGrant)
            or not isinstance(grant.token, str)
            or not grant.token
        ):
            raise ValueError("An audited, scoped research grant is required")
        self.client = httpx.Client(
            base_url=engine_origin.rstrip("/"),
            headers={
                "Authorization": f"Bearer {grant.token}",
                ENGINE_HEADER: "2",
                RESEARCH_HEADER: "1",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(30),
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _check(response: httpx.Response) -> None:
        if response.is_redirect:
            raise DirectResearchError("Research service redirected unexpectedly")
        if response.status_code >= 400:
            raise DirectResearchError(f"Research service rejected request: {response.status_code}")
        if (
            response.headers.get(ENGINE_HEADER) != "2"
            or response.headers.get(RESEARCH_HEADER) != "1"
        ):
            raise DirectResearchError("Research service contract mismatch")

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if payload is not None:
            try:
                raw_body = json.dumps(payload, allow_nan=False).encode("utf-8")
            except (TypeError, ValueError):
                raise ValueError("Invalid research request body") from None
            if len(raw_body) > _MAX_BODY:
                raise ValueError("Research request exceeds size limit")
        try:
            with self.client.stream(method, path, json=payload) as response:
                self._check(response)
                data = bytearray()
                for part in response.iter_bytes():
                    if len(data) + len(part) > _MAX_JSON:
                        raise DirectResearchError("Research response exceeds size limit")
                    data.extend(part)
        except httpx.RequestError:
            raise DirectResearchError("Research service unavailable") from None
        try:
            result = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            raise DirectResearchError("Invalid research response") from None
        if not isinstance(result, dict):
            raise DirectResearchError("Research response must be an object")
        return result

    def discover_datasets(self) -> dict:
        return self._request("GET", "/research/v1/datasets")

    def create_snapshot(self, dataset_id: str, start: str, end: str) -> dict:
        return self._request("POST", "/research/v1/snapshots", {
            "dataset_id": _identifier(dataset_id),
            "start": start,
            "end": end,
            "format": "parquet",
        })

    def get_snapshot(self, snapshot_id: str) -> dict:
        return self._request("GET", f"/research/v1/snapshots/{_identifier(snapshot_id)}")

    def fetch_snapshot(self, snapshot_id: str) -> bytes:
        """Bounded, verified snapshot bytes for a trusted one-way transfer.

        Do not return these bytes to the Web browser; the future task controller
        must transfer only the approved dataset into the research workspace.
        """
        snapshot_id = _identifier(snapshot_id)
        meta = self.get_snapshot(snapshot_id)
        size = meta.get("size_bytes")
        digest = meta.get("sha256")
        if (
            meta.get("snapshot_id") != snapshot_id or meta.get("status") != "ready"
            or meta.get("format") != "parquet"
            or type(size) is not int or not 0 <= size <= _MAX_SNAPSHOT
            or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise DirectResearchError("Snapshot metadata or size invalid")
        try:
            with self.client.stream(
                "GET", f"/research/v1/snapshots/{snapshot_id}/content",
                headers={"Accept": "application/octet-stream"},
            ) as response:
                self._check(response)
                result = bytearray()
                for part in response.iter_bytes():
                    if len(result) + len(part) > size:
                        raise DirectResearchError("Snapshot exceeds declared size")
                    result.extend(part)
        except httpx.RequestError:
            raise DirectResearchError("Snapshot transfer unavailable") from None
        if len(result) != size or sha256(result).hexdigest() != digest:
            raise DirectResearchError("Snapshot integrity mismatch")
        return bytes(result)

    def submit_backtest(self, request: dict) -> dict:
        """Existing Engine accepts pre-issued development strategies only."""
        allowed = {
            "strategy_ref", "dataset_refs", "start", "end", "starting_balance",
            "quote_currency", "parameters", "idempotency_key",
        }
        if (
            not isinstance(request, dict) or set(request) != allowed
            or request.get("parameters") != {}
            or not isinstance(request.get("dataset_refs"), list)
            or len(request["dataset_refs"]) != 1
        ):
            raise ValueError("Only existing development backtest contract is allowed")
        data = dict(request)
        data["purpose"] = "development"
        data["execution"] = "simulation"
        return self._request("POST", "/research/v1/backtests", data)

    def submit_candidate(self, request: dict) -> dict:
        """Forward a fixed, independently revalidated candidate package to Engine."""
        fields = {
            "candidate_id", "package_hash", "archive", "strategy_ref",
            "dataset_refs", "start", "end", "starting_balance", "quote_currency",
            "idempotency_key",
        }
        if (
            not isinstance(request, dict) or set(request) != fields
            or not isinstance(request.get("dataset_refs"), list)
            or len(request["dataset_refs"]) != 1
            or not isinstance(request.get("archive"), str)
            or len(request["archive"]) > 90_000
        ):
            raise ValueError("Invalid restricted candidate request")
        return self._request("POST", "/research/v1/candidate-backtests", {
            **request, "purpose": "development", "execution": "simulation",
        })

    def get_backtest(self, backtest_id: str) -> dict:
        return self._request("GET", f"/research/v1/backtests/{_identifier(backtest_id)}")

    def cancel_backtest(self, backtest_id: str) -> dict:
        return self._request("POST", f"/research/v1/backtests/{_identifier(backtest_id)}/cancel")
