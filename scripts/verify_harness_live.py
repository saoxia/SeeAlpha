"""Opt-in real Harness acceptance through the SeeAlpha Platform API.

Run only on your isolated development machine with a configured resident
Harness and test-only DeepSeek key. No fake Agent or Engine results are used.
"""
from __future__ import annotations

import argparse
import sys
import time
from getpass import getpass
from hashlib import sha256
from urllib.parse import urlsplit

import httpx

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def run(origin: str, username: str, deadline_seconds: int) -> None:
    url = urlsplit(origin)
    if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost"}
            or url.path not in {"", "/"} or url.username or url.password
            or url.query or url.fragment):
        raise ValueError("Only local Platform HTTP origins are accepted")

    password = getpass("SeeAlpha 管理员密码（不回显）：")
    with httpx.Client(
        base_url=origin.rstrip("/"), timeout=20, follow_redirects=False,
        trust_env=False,
    ) as client:
        response = client.post(
            "/api/auth/login", json={"username": username, "password": password},
        )
        password = ""
        response.raise_for_status()
        status = client.get("/api/harness/status")
        status.raise_for_status()
        details = status.json()
        if not (details.get("enabled") and details.get("available")
                and details.get("runtime_configured")):
            raise RuntimeError("Harness 未连接或模型凭据未配置：" + str(details))
        print("Platform 鉴权及 Harness 服务就绪")

        def await_task(task: dict) -> dict:
            end = time.monotonic() + deadline_seconds
            identity = task["id"]
            while time.monotonic() < end:
                response = client.get(f"/api/harness/tasks/{identity}")
                response.raise_for_status()
                latest = response.json()
                print("任务状态：", latest["status"])
                if latest["status"] in TERMINAL:
                    if latest["status"] != "completed" or not latest.get("final_response"):
                        raise RuntimeError("实际 Agent 研究任务未成功完成：" + str(latest["status"]))
                    return latest
                time.sleep(3)
            raise RuntimeError("任务状态仍未终结：请到 RESEARCH 界面核实，不要自动重发")

        task = client.post("/api/harness/tasks", json={
            "prompt": "请用 Python 分析 1 到 30 的平方数，计算均值和标准差，解释计算方法，并给出简短结论。",
            "queue_if_busy": True,
        })
        task.raise_for_status()
        initial = task.json()
        identity = initial["id"]
        print("启动真实模型任务：", identity)
        first = await_task(initial)
        assert first.get("turn") == 1

        history = client.get("/api/harness/tasks")
        history.raise_for_status()
        assert any(item["id"] == identity for item in history.json()["tasks"])
        timeline = client.get(f"/api/harness/tasks/{identity}/timeline")
        timeline.raise_for_status()
        assert timeline.json()["events"]

        def check_summary(n: int) -> None:
            index = client.get(f"/api/harness/tasks/{identity}/artifacts")
            index.raise_for_status()
            item = next(
                (item for item in index.json()["artifacts"]
                 if item["artifact_id"] == f"summary-{n}"),
                None,
            )
            if item is None:
                raise RuntimeError(f"第 {n} 轮摘要没有进入 Artifact 索引")
            result = client.get(
                f"/api/harness/tasks/{identity}/artifacts/summary-{n}",
            )
            result.raise_for_status()
            if (not result.headers["content-disposition"].startswith("attachment;")
                    or result.headers.get("x-content-type-options") != "nosniff"
                    or sha256(result.content).hexdigest() != item["sha256"]):
                raise RuntimeError("研究成果附件校验失败")
            print(f"第 {n} 轮摘要验证通过：", item["size"], "bytes")

        check_summary(1)
        continued = client.post(f"/api/harness/tasks/{identity}/continue", json={
            "prompt": "请总结上一轮计算的方法和主要结论，说明为什么这属于开发研究而不是实盘交易。",
            "queue_if_busy": True,
        })
        continued.raise_for_status()
        second = await_task(continued.json())
        assert second["turn"] == 2
        check_summary(2)
        print("通过：真实 Agent 任务、Platform、Harness、历史、进度、Session 续接与成果校验")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    try:
        if not 60 <= args.timeout_seconds <= 3600:
            raise ValueError("timeout-seconds must be 60–3600")
        run(args.origin, args.username, args.timeout_seconds)
    except (httpx.HTTPError, ValueError, RuntimeError, AssertionError, KeyError) as exc:
        # Do not print response bodies: an upstream error may contain secrets.
        if isinstance(exc, httpx.HTTPError):
            print("HARNESS_VERIFY_FAILED: HTTP request failed", file=sys.stderr)
        else:
            print(f"HARNESS_VERIFY_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
