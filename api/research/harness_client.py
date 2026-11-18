"""Fixed-origin client for the resident single-user Harness HTTP v1.

Only Platform's authenticated server process holds this token; browsers never
connect to Harness directly. No Engine/Gateway secrets are forwarded.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import httpx

HARNESS_VERSION = "1"
HARNESS_HEADER = "X-SeeAlpha-Harness-Contract-Version"
TASK_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ARTIFACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class HarnessUnavailable(RuntimeError):
    pass


class HarnessRejected(RuntimeError):
    def __init__(self, status: int):
        self.status = status
        super().__init__("Harness rejected the request")


def validate_task_id(value: str) -> str:
    if not TASK_ID.fullmatch(value):
        raise ValueError("Invalid task identifier")
    return value


def validate_artifact_id(value: str) -> str:
    if not ARTIFACT_ID.fullmatch(value) or ".." in value:
        raise ValueError("Invalid artifact identifier")
    return value


class HarnessClient:
    def __init__(self, origin: str, token: str, *, transport=None):
        url = urlsplit(origin)
        if (url.scheme not in {"http", "https"} or not url.hostname or
                url.username or url.password or url.path not in {"", "/"} or
                url.query or url.fragment):
            raise ValueError("HARNESS_URL must be a fixed HTTP(S) origin")
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("HARNESS_API_TOKEN must be a strong secret")
        self.client = httpx.Client(
            base_url=origin.rstrip("/"), timeout=httpx.Timeout(15),
            follow_redirects=False, trust_env=False, transport=transport,
            headers={"Authorization": f"Bearer {token}", HARNESS_HEADER: HARNESS_VERSION,
                     "Accept": "application/json"},
        )

    def close(self) -> None:
        self.client.close()

    def _request(self, method: str, path: str, *, payload: dict | None = None) -> dict:
        try:
            with self.client.stream(method, path, json=payload) as response:
                status = response.status_code
                if response.is_redirect:
                    raise HarnessUnavailable("Harness refused an unexpected redirect")
                if response.headers.get(HARNESS_HEADER) != HARNESS_VERSION:
                    raise HarnessUnavailable("Harness contract version mismatch")
                if status >= 300:
                    if status in {404, 409, 429}:
                        raise HarnessRejected(status)
                    raise HarnessUnavailable("Harness is unavailable or refused authentication")
                body = bytearray()
                for block in response.iter_bytes():
                    if len(body) + len(block) > 125_000:
                        raise HarnessUnavailable("Harness response too large")
                    body.extend(block)
        except (httpx.TimeoutException, httpx.RequestError):
            raise HarnessUnavailable("Harness is unreachable") from None
        try:
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (ValueError, UnicodeDecodeError):
            raise HarnessUnavailable("Harness response invalid") from None

    def health(self) -> dict:
        result = self._request("GET", "/health")
        if result.get("service") != "seealpha-harness" or result.get("contract_version") != 1:
            raise HarnessUnavailable("Unexpected Harness service")
        return {"runtime_configured": result.get("runtime_configured") is True,
                "runtime_layout": result.get("runtime_layout")}

    @staticmethod
    def public_task(result: dict) -> dict:
        if (not isinstance(result.get("id"), str) or
                not TASK_ID.fullmatch(result["id"]) or
                result.get("status") not in {
                    "queued", "starting", "running", "cancelling",
                    "completed", "failed", "cancelled", "interrupted",
                }):
            raise HarnessUnavailable("Harness task response invalid")
        return {key: result.get(key) for key in (
            "id", "status", "turn", "created_at", "updated_at",
            "final_response", "error",
        )}

    def create(self, prompt: str, *, queue_if_busy: bool) -> dict:
        return self.public_task(self._request("POST", "/v1/tasks", payload={
            "prompt": prompt, "queue_if_busy": queue_if_busy,
        }))

    def get(self, task_id: str) -> dict:
        return self.public_task(self._request("GET", f"/v1/tasks/{validate_task_id(task_id)}"))

    def continue_task(self, task_id: str, prompt: str, *, queue_if_busy: bool) -> dict:
        return self.public_task(self._request(
            "POST", f"/v1/tasks/{validate_task_id(task_id)}/continue",
            payload={"prompt": prompt, "queue_if_busy": queue_if_busy},
        ))

    def cancel(self, task_id: str) -> dict:
        return self.public_task(self._request(
            "POST", f"/v1/tasks/{validate_task_id(task_id)}/cancel",
        ))

    def artifacts(self, task_id: str) -> list[dict]:
        result = self._request("GET", f"/v1/tasks/{validate_task_id(task_id)}/artifacts")
        items = result.get("artifacts")
        if not isinstance(items, list) or len(items) > 100:
            raise HarnessUnavailable("Harness artifact response invalid")
        safe = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("artifact_id"), str):
                raise HarnessUnavailable("Harness artifact response invalid")
            validate_artifact_id(item["artifact_id"])
            safe.append({key: item.get(key) for key in
                         ("artifact_id", "filename", "kind", "size", "sha256", "review_status")})
        return safe

    def download_artifact(self, task_id: str, artifact_id: str) -> bytes:
        path = (f"/v1/tasks/{validate_task_id(task_id)}/artifacts/"
                f"{validate_artifact_id(artifact_id)}")
        try:
            with self.client.stream("GET", path) as response:
                if (response.is_redirect or
                        response.headers.get(HARNESS_HEADER) != HARNESS_VERSION):
                    raise HarnessUnavailable("Harness artifact contract mismatch")
                if response.status_code == 404:
                    raise HarnessRejected(404)
                if response.status_code != 200:
                    raise HarnessUnavailable("Harness artifact unavailable")
                data = bytearray()
                for block in response.iter_bytes():
                    if len(data) + len(block) > 16 * 1024 * 1024:
                        raise HarnessUnavailable("Harness artifact exceeds transfer limit")
                    data.extend(block)
                return bytes(data)
        except (httpx.RequestError, httpx.TimeoutException):
            raise HarnessUnavailable("Harness artifact transfer failed") from None
