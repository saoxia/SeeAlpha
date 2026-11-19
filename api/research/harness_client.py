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
                    if len(body) + len(block) > 512_000:
                        raise HarnessUnavailable("Harness response too large")
                    body.extend(block)
        except (httpx.TimeoutException, httpx.RequestError):
            raise HarnessUnavailable("Harness is unreachable") from None
        try:
            result = json.loads(body)
            if not isinstance(result, dict):
                raise TypeError
            return result
        except (TypeError, ValueError, UnicodeDecodeError):
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

    def create(self, prompt: str, *, queue_if_busy: bool, task_id=None,
               research_access=None) -> dict:
        payload = {"prompt": prompt, "queue_if_busy": queue_if_busy}
        if task_id is not None:
            payload["task_id"] = validate_task_id(task_id)
            payload["research_access"] = research_access
        return self.public_task(self._request("POST", "/v1/tasks", payload=payload))

    def recent_tasks(self, *, limit: int = 50) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid history limit")
        result = self._request("GET", f"/v1/tasks?limit={limit}")
        tasks = result.get("tasks")
        if not isinstance(tasks, list) or len(tasks) > limit:
            raise HarnessUnavailable("Harness history response invalid")
        # Only allow task metadata to be returned, even if the upstream
        # regresses and begins including saved user prompts.
        if not all(isinstance(item, dict) for item in tasks):
            raise HarnessUnavailable("Harness history response invalid")
        return [{key: self.public_task(item).get(key) for key in (
            "id", "status", "turn", "created_at", "updated_at", "error",
        )} for item in tasks]

    def timeline(self, task_id: str, *, after: int = 0) -> dict:
        if type(after) is not int or not 0 <= after <= 2_000_000_000:
            raise ValueError("Invalid event cursor")
        result = self._request(
            "GET", f"/v1/tasks/{validate_task_id(task_id)}/timeline?after={after}",
        )
        events = result.get("events")
        cursor = result.get("next_cursor")
        if (not isinstance(events, list) or len(events) > 200 or
                type(cursor) is not int or cursor < after or
                type(result.get("has_more")) is not bool):
            raise HarnessUnavailable("Harness timeline response invalid")
        safe = []
        for event in events:
            if (not isinstance(event, dict) or
                    type(event.get("sequence")) is not int or
                    event["sequence"] <= after or event["sequence"] > cursor or
                    not isinstance(event.get("type"), str) or
                    event["type"] not in {"progress", "terminal", "artifact_ready"}):
                raise HarnessUnavailable("Harness timeline event invalid")
            # Pass only bounded, non-secret display metadata, not raw SDK
            # output, arguments, tool parameters or environment details.
            payload = event.get("payload")
            if not isinstance(payload, dict):
                raise HarnessUnavailable("Harness timeline payload invalid")
            safe.append({
                "sequence": event["sequence"], "type": event["type"],
                "timestamp": event.get("timestamp"),
                "stage": payload.get("stage") if isinstance(payload.get("stage"), str) else None,
                "notifications": (payload.get("notifications")
                                  if type(payload.get("notifications")) is int else None),
                "status": (payload.get("status")
                           if isinstance(payload.get("status"), str) and
                           payload["status"] in {
                               "completed", "failed", "cancelled", "interrupted",
                           } else None),
            })
        return {"events": safe, "next_cursor": cursor, "has_more": result["has_more"]}

    def get(self, task_id: str) -> dict:
        return self.public_task(self._request("GET", f"/v1/tasks/{validate_task_id(task_id)}"))

    def continue_task(self, task_id: str, prompt: str, *, queue_if_busy: bool,
                      research_access=None) -> dict:
        payload = {"prompt": prompt, "queue_if_busy": queue_if_busy}
        if research_access is not None:
            payload["research_access"] = research_access
        return self.public_task(self._request(
            "POST", f"/v1/tasks/{validate_task_id(task_id)}/continue", payload=payload,
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
            if (type(item.get("size")) is not int or
                    not 0 <= item["size"] <= 16 * 1024 * 1024 or
                    not isinstance(item.get("sha256"), str) or
                    not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])):
                raise HarnessUnavailable("Harness artifact metadata invalid")
            safe.append({key: item.get(key) for key in
                         ("artifact_id", "filename", "kind", "size", "sha256", "review_status")})
        return safe

    def import_artifact(self, task_id: str, source_file: str) -> dict:
        # A user may explicitly register one completed task's exports/ item.
        # Harness enforces safe filename, workspace ownership and file type.
        if (not isinstance(source_file, str) or
                not re.fullmatch(r"exports/[A-Za-z0-9][A-Za-z0-9._-]{0,120}", source_file)
                or ".." in source_file):
            raise ValueError("Invalid export filename")
        result = self._request(
            "POST", f"/v1/tasks/{validate_task_id(task_id)}/artifacts/import",
            payload={"source_file": source_file},
        )
        if not isinstance(result.get("artifact_id"), str):
            raise HarnessUnavailable("Harness artifact import response invalid")
        validate_artifact_id(result["artifact_id"])
        return {key: result.get(key) for key in
                ("artifact_id", "filename", "kind", "size", "sha256")}

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
