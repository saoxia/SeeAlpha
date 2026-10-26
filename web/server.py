"""Local web management for project checks and isolated backtest jobs."""

import argparse
import base64
import fcntl
import hmac
import ipaddress
import json
import math
import mimetypes
import os
import secrets
import signal
import stat
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from .artifacts import BACKTESTS, artifact_path
from .jobs import ACTIONS, STRATEGIES, BusyError, JobManager

PROJECT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).with_name("static")


def snapshot(project, relative, max_age):
    """Read small published snapshots; never access private databases."""
    try:
        path = project / relative
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("状态文件过大")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise TypeError("状态格式无效")
        timestamp = data.get("checked_ms")
        if timestamp is not None:
            timestamp = float(timestamp) / 1000
        else:
            value = data.get("checked_at") or data.get("created_at")
            timestamp = datetime.fromisoformat(value).timestamp() if value else None
        if timestamp is not None and not math.isfinite(timestamp):
            raise ValueError("状态时间无效")
        age = max(0, time.time() - timestamp) if timestamp is not None else None
        return {
            "data": data,
            "error": None,
            "age_seconds": age,
            "stale": age is None or age > max_age,
        }
    except (OSError, ValueError, TypeError, OverflowError):
        return {
            "data": None,
            "error": "状态文件尚未生成或暂时无法读取",
            "age_seconds": None,
            "stale": True,
        }


class Console:
    def __init__(self, project=PROJECT, jobs=None):
        self.project = Path(project).resolve()
        self.jobs = jobs if jobs is not None else JobManager(self.project)
        self.token = secrets.token_urlsafe(32)
        self._reports = []
        self._reports_at = 0
        self._report_lock = threading.Lock()

    def reports(self):
        with self._report_lock:
            if time.monotonic() - self._reports_at < 10:
                return self._reports
            items = []
            for path in (self.project / BACKTESTS).glob("**/report.html"):
                try:
                    relative = path.relative_to(self.project)
                    artifact_path(self.project, str(relative))
                    modified = path.stat().st_mtime
                    strategy_index = 4 if relative.parts[3] == "portfolio" else 3
                    strategy = relative.parts[strategy_index]
                    run_id = "/".join(relative.parts[strategy_index + 1 : -1])
                    items.append(
                        {
                            "name": strategy,
                            "strategy": strategy,
                            "run_id": run_id,
                            "modified_at": datetime.fromtimestamp(modified, UTC).isoformat(),
                            "url": "/artifacts/" + quote(relative.as_posix()),
                            "summary_url": "/artifacts/"
                            + quote(relative.with_name("summary.json").as_posix())
                            if path.with_name("summary.json").is_file()
                            else None,
                        }
                    )
                except (OSError, ValueError):
                    continue
            self._reports = sorted(items, key=lambda item: item["modified_at"], reverse=True)
            self._reports_at = time.monotonic()
            return self._reports

    def state(self):
        reports = self.reports()
        return {
            "server_time": datetime.now(UTC).isoformat(),
            "csrf_token": self.token,
            "checks": snapshot(self.project, Path("data/project_checks/latest.json"), 86400),
            "strategies": STRATEGIES,
            "actions": ACTIONS,
            "jobs": self.jobs.list_jobs(),
            "reports": reports,
            "report_count": len(reports),
        }


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, console, password=None):
        self.console = console
        super().__init__(address, ConsoleHandler)
        self.local_only = ipaddress.ip_address(self.server_address[0]).is_loopback
        if not self.local_only and not password:
            self.server_close()
            raise ValueError("网络访问必须配置登录口令")
        self.authorization = (
            b"Basic " + base64.b64encode(("admin:" + password).encode("utf-8"))
            if password
            else None
        )


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "SeeAlphaConsole"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def _headers(self, status, content_type, length, *, report=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        if status == 401:
            self.send_header("WWW-Authenticate", 'Basic realm="SeeAlpha", charset="UTF-8"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            (
                "sandbox allow-scripts allow-downloads; default-src 'none'; "
                "script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; "
                "font-src data:; connect-src 'none'"
            )
            if report
            else (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
                "frame-ancestors 'none'; form-action 'self'"
            ),
        )
        self.end_headers()

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        if self.command != "HEAD":
            self.wfile.write(body)

    def _valid_request(self):
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            hosts.update({"127.0.0.1", "localhost"})
        host = self.headers.get("Host", "")
        try:
            parsed = urlsplit("http://" + host)
            valid = (
                bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and not (parsed.path or parsed.query or parsed.fragment)
                and not any(c.isspace() or c == "\\" for c in host)
                and (parsed.port is None or 1 <= parsed.port <= 65535)
            )
        except ValueError:
            valid = False
        if len(self.headers.get_all("Host", [])) != 1 or not valid:
            self._json(403, {"error": "管理地址无效"})
            return False
        if self.server.local_only and host not in hosts:
            self._json(403, {"error": "当前服务仅监听本机地址"})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._json(403, {"error": "不允许跨站访问"})
            return False
        if self.server.authorization and not hmac.compare_digest(
            self.headers.get("Authorization", "").encode("utf-8"), self.server.authorization
        ):
            self._json(401, {"error": "请使用 admin 和管理口令登录"})
            return False
        return True

    def _file(self, path, *, report=False):
        # Refuse symlinks on the final component, even if swapped after validation.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise FileNotFoundError
            content_type = mimetypes.guess_type(path.name)[0] or "text/plain"
            if content_type.startswith("text/") or content_type == "application/json":
                content_type += "; charset=utf-8"
            self._headers(200, content_type, info.st_size, report=report)
            if self.command != "HEAD":
                remaining = info.st_size
                while remaining:
                    chunk = source.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._valid_request():
            return
        path = unquote(urlsplit(self.path).path)
        try:
            if path == "/api/state":
                self._json(200, self.server.console.state())
            elif path.startswith("/api/jobs/"):
                identity = path.removeprefix("/api/jobs/")
                self._json(200, self.server.console.jobs.get_job(identity))
            elif path.startswith("/artifacts/"):
                artifact = artifact_path(
                    self.server.console.project, path.removeprefix("/artifacts/")
                )
                self._file(artifact, report=True)
            elif path in {"/", "/index.html", "/app.css", "/app.js"}:
                self._file(STATIC / ("index.html" if path == "/" else path[1:]))
            else:
                self._json(404, {"error": "页面不存在"})
        except (FileNotFoundError, KeyError, ValueError):
            self._json(404, {"error": "文件或任务不存在"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError:
            self._json(503, {"error": "暂时无法读取，请稍后重试"})

    def do_POST(self):
        if not self._valid_request():
            return
        origins = {scheme + self.headers["Host"] for scheme in ("http://", "https://")}
        token = self.headers.get("X-CSRF-Token", "")
        if self.headers.get("Origin") not in origins or not hmac.compare_digest(
            token.encode("utf-8"), self.server.console.token.encode("utf-8")
        ):
            self._json(403, {"error": "页面凭证已失效，请刷新后重试"})
            return
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "请求必须为 JSON"})
            return
        try:
            if (
                self.headers.get("Transfer-Encoding")
                or len(self.headers.get_all("Content-Length", [])) != 1
            ):
                raise ValueError("请求长度无效")
            length = int(self.headers["Content-Length"])
            if not 0 < length <= 4096:
                raise ValueError("请求长度无效")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise TypeError("请求必须为对象")
            path = urlsplit(self.path).path
            jobs = self.server.console.jobs
            if path == "/api/jobs":
                if set(data) - {"action", "parameters"}:
                    raise ValueError("请求包含未知字段")
                job = jobs.start(data.get("action"), data.get("parameters", {}))
                self._json(202, {"job": job})
            elif path.startswith("/api/jobs/") and path.endswith("/cancel"):
                if data:
                    raise ValueError("取消请求不接受参数")
                job = jobs.cancel(path[len("/api/jobs/") : -len("/cancel")])
                self._json(200, {"job": job})
            else:
                self._json(404, {"error": "操作不存在"})
        except BusyError:
            self._json(409, {"error": "已有任务运行，请等待完成或取消后再试"})
        except KeyError:
            self._json(404, {"error": "任务不存在"})
        except (ValueError, TypeError, UnicodeError) as exc:
            self._json(400, {"error": str(exc) or "参数无效"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError:
            self._json(503, {"error": "暂时无法执行，请稍后重试"})


def main():
    parser = argparse.ArgumentParser(description="启动 SeeAlpha Web 管理端")
    parser.add_argument(
        "--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0（所有 IPv4 网卡）"
    )
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    parser.add_argument(
        "--password-file",
        type=Path,
        default=PROJECT / "data/web_console/access_password",
        help="网络访问的登录口令文件；不存在时自动生成",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口范围为 1–65535")
    runtime = PROJECT / "data/web_console"
    runtime.mkdir(parents=True, exist_ok=True)
    # A second instance must not mark the first instance's running jobs interrupted.
    with (runtime / "server.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, "Web 管理端已在运行。\n")
        console = Console()
        try:
            password = None
            if args.host not in {"127.0.0.1", "localhost"}:
                password_path = args.password_file
                try:
                    descriptor = os.open(password_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    if password_path.is_symlink():
                        raise ValueError("口令文件不能为符号链接") from None
                else:
                    with os.fdopen(descriptor, "w") as output:
                        output.write(secrets.token_urlsafe(24) + "\n")
                password = password_path.read_text().strip()
                if len(password) < 16:
                    raise ValueError("网络访问口令至少需要 16 个字符")
            server = ConsoleServer((args.host, args.port), console, password=password)
        except (OSError, ValueError) as exc:
            console.jobs.close()
            parser.exit(1, f"无法启动管理端：{exc}\n")

        def stop(_signum, _frame):
            threading.Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        print(f"Web 管理端监听 {args.host}:{args.port}（按 Ctrl+C 停止）", flush=True)
        if args.host == "0.0.0.0":
            print(f"其他设备访问：http://<本机网络 IP>:{args.port}", flush=True)
        if password:
            print(f"登录用户：admin；口令文件：{args.password_file}", flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            console.jobs.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
