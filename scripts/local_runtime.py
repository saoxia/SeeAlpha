"""Single-machine SeeAlpha launcher. Only manages processes created by this tool."""
from __future__ import annotations

import os
import re
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_SHA = "6dcbcc7416cbbf8bd36fdb303bce7dcd27eb7a33"
API_URL = "http://127.0.0.1:8000"
ENGINE_URL = "http://127.0.0.1:8766"
WEB_URL = "http://127.0.0.1:3000"
PORTS = (8766, 8000, 3000)
SERVICE_ORDER = ("engine", "api", "web")


class LocalRuntime:
    def __init__(self, root: Path = ROOT):
        self.root = Path(root).resolve()
        self.engine = self.root / ".local/engine-src"
        self.platform_python = self.root / "platform/.venv/bin/python"
        self.db = self.root / ".local/state/api/seealpha.sqlite"
        self.db_url_file = self.root / ".local/config/api/database_url"
        self.token_file = self.root / ".local/config/api/engine_token"
        self.pid_dir = self.root / ".local/run/seealpha"
        self.log_dir = self.root / ".local/logs/seealpha"

    @staticmethod
    def _private_file(path: Path) -> str:
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            raise RuntimeError(f"缺少安装配置：{path.name}；请运行 ./seealpha install") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise RuntimeError(f"{path.name} 必须是权限 600 的普通文件，不能是符号链接。")
        return path.read_text(encoding="utf-8").strip()

    def check_config(self) -> str:
        expected = f"sqlite:///{self.db}"
        if self._private_file(self.db_url_file) != expected:
            raise RuntimeError("数据库配置不是本机隔离 SQLite；拒绝接管。")
        token = self._private_file(self.token_file)
        if not re.fullmatch(r"[a-zA-Z0-9_-]{48,}", token):
            raise RuntimeError("Engine 令牌格式无效。")
        if not self.engine.is_dir() or self.engine.is_symlink():
            raise RuntimeError("缺少隔离的 Engine 安装目录。")
        try:
            revision = subprocess.run(
                ["git", "-C", str(self.engine), "rev-parse", "HEAD"],
                check=True, text=True, capture_output=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError("无法验证本地 Engine 版本。") from exc
        if revision != ENGINE_SHA:
            raise RuntimeError("Engine 版本不匹配；拒绝自动切换。")
        for name in ("engine_contracts.py", "trading_contracts.py"):
            if (self.root / "core" / name).read_bytes() != (self.engine / "core" / name).read_bytes():
                raise RuntimeError(f"Engine / Platform 契约不匹配：{name}")
        return token

    @staticmethod
    def _clean_env() -> dict[str, str]:
        env = os.environ.copy()
        for key in (
            "DATABASE_URL", "DATABASE_URL_FILE", "AUTH_DB_PATH", "DATABASE_SCHEMA",
            "ENGINE_TOKEN", "ENGINE_AUTH_MODE", "ENGINE_URL", "ENGINE_DEPLOYMENT", "ENGINE_DOCKER_IMAGE",
            "ENGINE_STATE", "ENGINE_PROJECT", "ENGINE_HOST", "ENGINE_PORT",
            "ENGINE_MARKET_ACCOUNT_ID",
            "ENGINE_TENANT_ROOT", "ENGINE_TENANT_ID", "ENGINE_TENANT_PURPOSE",
            "COOKIE_SECURE", "ALLOWED_HOSTS", "ALLOWED_ORIGINS",
            "HARNESS_ENABLED", "HARNESS_URL", "HARNESS_API_TOKEN",
            "SEEALPHA_API_URL", "SYSTEM_CREDENTIAL_KEY", "SYSTEM_CREDENTIAL_KEY_FILE",
            "ETL_CREDENTIAL_KEY",
        ):
            env.pop(key, None)
        return env

    def check_database(self) -> None:
        if not self.db.is_file() or self.db.is_symlink():
            raise RuntimeError("本机数据库尚未初始化，安装器不会自动接管已有数据库。")
        # Reuse the ORM's Alembic revision guard. Never run migrations on start.
        sys.path.insert(0, str(self.root))
        from api.config import Settings
        from api.db.database import create_database

        clean = self._clean_env()
        old = os.environ.copy()
        try:
            os.environ.clear()
            os.environ.update(clean)
            settings = Settings.from_environment()
            db = create_database(settings)
            try:
                db.initialize()
            finally:
                db.close()
        finally:
            os.environ.clear()
            os.environ.update(old)

    def _command(self, service: str) -> list[str]:
        if service == "engine":
            return [str(self.engine / ".venv/bin/python"), "-m", "engine"]
        if service == "api":
            return [str(self.platform_python), "-m", "uvicorn", "api.main:app",
                    "--host", "127.0.0.1", "--port", "8000"]
        if service == "web":
            return ["node", str(self.root / "web/.next/standalone/server.js")]
        raise ValueError(service)

    def _pid_file(self, service: str) -> Path:
        return self.pid_dir / f"{service}.pid"

    def _pid(self, service: str) -> int | None:
        try:
            value = int(self._pid_file(service).read_text().splitlines()[0])
            return value if value > 1 else None
        except (OSError, ValueError, IndexError):
            return None

    def _owned(self, service: str, pid: int | None) -> bool:
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            details = subprocess.run(
                ["ps", "-ww", "-p", str(pid), "-o", "command="],
                check=False, capture_output=True, text=True,
            ).stdout
        except (OSError, ProcessLookupError):
            return False
        try:
            lines = self._pid_file(service).read_text(encoding="ascii").splitlines()
            if not lines or lines[0] != str(pid):
                return False
            if len(lines) == 2:
                # Immutable process identity survives macOS Python and Next.js title
                # rewrites. Recycled PIDs have a different birth timestamp.
                started = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "lstart="],
                    check=False, capture_output=True, text=True,
                ).stdout.strip()
                return bool(started) and started == lines[1]
            if len(lines) != 1:
                return False
        except OSError:
            return False

        # Legacy one-line PID files: never fall back to a generic Python process
        # name, which could belong to another app.
        command = self._command(service)
        if service == "web":
            return command[1] in details
        return command[0] in details and (
            "-m engine" in details if service == "engine" else "-m uvicorn" in details
        )

    @staticmethod
    def _port_available(port: int) -> bool:
        with socket.socket() as check:
            # macOS retains TCP TIME_WAIT after stop; SO_REUSEADDR permits a
            # genuine restart, but not binding over another active listener.
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                check.bind(("127.0.0.1", port))
                return True
            except OSError:
                return False

    @staticmethod
    def _healthy(url: str, token: str | None = None) -> bool:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=1.5) as response:
                return 200 <= response.status < 400
        except (urllib.error.URLError, OSError):
            return False

    def _start_one(self, service: str, token: str) -> None:
        env = self._clean_env()
        if service == "engine":
            env.update(ENGINE_AUTH_MODE="token", ENGINE_TOKEN=token,
                       ENGINE_HOST="127.0.0.1", ENGINE_PORT="8766",
                       ENGINE_PROJECT=str(self.engine),
                       ENGINE_STATE=str(self.engine / ".local/state/engine"))
            cwd = self.engine
        elif service == "api":
            env.update(ENGINE_TOKEN=token, ENGINE_DEPLOYMENT="single",
                       ENGINE_URL=ENGINE_URL, COOKIE_SECURE="false",
                       ALLOWED_ORIGINS=f"{WEB_URL},http://localhost:3000",
                       ALLOWED_HOSTS="localhost,127.0.0.1", HARNESS_ENABLED="false")
            cwd = self.root
        else:
            env.update(SEEALPHA_API_URL=API_URL, NODE_ENV="production",
                       HOSTNAME="127.0.0.1", PORT="3000")
            cwd = self.root / "web"
        log = self.log_dir / f"{service}.log"
        if log.is_symlink():
            raise RuntimeError("日志路径不能是符号链接。")
        log.touch(mode=0o600, exist_ok=True)
        log.chmod(0o600)
        with log.open("a", encoding="utf-8") as handle:
            process = subprocess.Popen(
                self._command(service), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=handle, stderr=subprocess.STDOUT, start_new_session=True,
            )
        started = subprocess.run(
            ["ps", "-p", str(process.pid), "-o", "lstart="],
            check=False, capture_output=True, text=True,
        ).stdout.strip()
        if not started:
            process.terminate()
            raise RuntimeError(f"{service} 无法记录进程启动身份；拒绝接管。")
        self._pid_file(service).write_text(
            f"{process.pid}\\n{started}\\n".replace("\\n", "\n"), encoding="ascii",
        )
        self._pid_file(service).chmod(0o600)
        health_url = {"engine": f"{ENGINE_URL}/health", "api": f"{API_URL}/api/health",
                      "web": WEB_URL}[service]
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            if self._healthy(health_url, token if service == "engine" else None):
                print(f"{service}: 已就绪")
                return
            time.sleep(0.5)
        raise RuntimeError(f"{service} 启动失败；查看 {log}")

    def start(self) -> None:
        token = self.check_config()
        self.check_database()
        for required in (
            self.platform_python, self.engine / ".venv/bin/python",
            self.root / "web/.next/BUILD_ID", self.root / "web/.next/standalone/server.js",
        ):
            if not required.is_file():
                raise RuntimeError(f"安装不完整：{required}；请重新运行 ./seealpha install")
        for service in SERVICE_ORDER:
            if self._owned(service, self._pid(service)):
                raise RuntimeError(f"{service} 已由本启动器运行；先执行 ./seealpha status")
        for port in PORTS:
            if not self._port_available(port):
                raise RuntimeError(f"127.0.0.1:{port} 已被占用；拒绝接管其它进程。")
        self.pid_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for service in SERVICE_ORDER:
            self._pid_file(service).unlink(missing_ok=True)
        started: list[str] = []
        try:
            for service in SERVICE_ORDER:
                started.append(service)
                self._start_one(service, token)
        except Exception:
            for service in reversed(started):
                self._stop_one(service)
            raise
        print(f"SeeAlpha 已启动：{WEB_URL}")

    def up(self) -> None:
        running = [service for service in SERVICE_ORDER if self._owned(service, self._pid(service))]
        if len(running) == len(SERVICE_ORDER):
            self.status()
            return
        if running:
            raise RuntimeError(
                "发现仅部分服务由本启动器运行；请先 ./seealpha status，"
                "再检查未决任务后执行 ./seealpha stop，不会隐式重启。"
            )
        self.start()

    def _stop_one(self, service: str) -> bool:
        file = self._pid_file(service)
        pid = self._pid(service)
        if not self._owned(service, pid):
            file.unlink(missing_ok=True)
            print(f"{service}: 未运行")
            return True
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            file.unlink(missing_ok=True)
            return True
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not self._owned(service, pid):
                file.unlink(missing_ok=True)
                print(f"{service}: 已停止")
                return True
            time.sleep(0.2)
        print(f"{service}: 停止超时，未强制终止（请检查未决订单与日志）")
        return False

    def stop(self) -> None:
        outcomes = [self._stop_one(service) for service in reversed(SERVICE_ORDER)]
        if not all(outcomes):
            raise RuntimeError("部分服务未停止；未执行强制关闭。")

    def status(self) -> None:
        try:
            token = self.check_config()
        except RuntimeError as exc:
            print(f"安装配置：不可用（{exc}）")
            return
        for service, url in (
            ("engine", f"{ENGINE_URL}/health"), ("api", f"{API_URL}/api/health"),
            ("web", WEB_URL),
        ):
            if not self._owned(service, self._pid(service)):
                print(f"{service}: 未由本启动器运行")
            else:
                good = self._healthy(url, token if service == "engine" else None)
                print(f"{service}: {'健康' if good else '运行中，但健康检查失败'}")

    def doctor(self) -> None:
        for name in ("git", "uv", "node", "npm"):
            from shutil import which
            print(f"{name}: {'已安装' if which(name) else '缺少'}")
        try:
            self.check_config()
            self.check_database()
            print("本地配置 / 数据库 / Engine 契约：正常")
        except (RuntimeError, OSError) as exc:
            print(f"本地配置 / 数据库 / Engine 契约：异常（{exc}）")
        self.status()

    def admin(self, action: str, username: str) -> None:
        if action not in ("create", "reset"):
            raise RuntimeError("admin 只支持 create 或 reset")
        self.check_config()
        self.check_database()
        subprocess.run(
            [str(self.platform_python), "-m", "api.auth.manage",
             "create-user" if action == "create" else "reset-password", username],
            cwd=self.root, env=self._clean_env(), check=True,
        )


def main(argv: list[str]) -> int:
    os.umask(0o077)
    runtime = LocalRuntime()
    try:
        if argv == ["check-config"]:
            runtime.check_config()
        elif argv == ["check-database"]:
            runtime.check_database()
        elif argv == ["start"]:
            runtime.start()
        elif argv == ["up"]:
            runtime.up()
        elif argv == ["stop"]:
            runtime.stop()
        elif argv == ["status"]:
            runtime.status()
        elif argv == ["doctor"]:
            runtime.doctor()
        elif len(argv) in (2, 3) and argv[0] == "admin":
            runtime.admin(argv[1], argv[2] if len(argv) == 3 else "admin")
        else:
            raise RuntimeError("参数错误；执行 ./seealpha help 查看用法。")
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"SeeAlpha: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
