#!/usr/bin/env bash
set -euo pipefail
umask 077

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if [[ $# -ne 0 ]]; then
  echo "安装命令不接受额外参数。" >&2
  exit 2
fi

# Never act on inherited production deployment configuration.
for key in DATABASE_URL DATABASE_URL_FILE ENGINE_DEPLOYMENT ENGINE_URL ENGINE_TOKEN; do
  if [[ -n "${!key:-}" ]]; then
    echo "检测到 $key：本机安装拒绝接管已有部署。请在隔离的终端环境执行。" >&2
    exit 2
  fi
done
if [[ -n "${SEEALPHA_API_URL:-}" && "$SEEALPHA_API_URL" != "http://127.0.0.1:8000" ]]; then
  echo "SEEALPHA_API_URL 指向非本机 API，拒绝安装。" >&2
  exit 2
fi

for tool in git uv node npm; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "缺少 $tool。请先安装该工具，再运行 ./seealpha install。" >&2
    exit 2
  fi
done
if ! node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 13) ? 0 : 1)' ; then
  echo "需要 Node.js >= 22.13.0。" >&2
  exit 2
fi

# Keep the exact Engine SHA used by scripts/build_external_engine.sh.
engine_sha="b33769d432c17fff2b743c31c7efdd6bf3cb450d"
engine_dir="$root/.local/engine-src"
db_file="$root/.local/state/api/seealpha.sqlite"
db_url_file="$root/.local/config/api/database_url"
token_file="$root/.local/config/api/engine_token"
db_url="sqlite:///$db_file"

if [[ -L "$db_url_file" || -L "$token_file" || -L "$engine_dir" || -L "$db_file" ]]; then
  echo "检测到符号链接形式的安装配置或状态路径，拒绝自动接管。" >&2
  exit 2
fi
if [[ -e "$db_url_file" ]]; then
  if [[ ! -f "$db_url_file" || "$(<"$db_url_file")" != "$db_url" ]]; then
    echo "已有非本安装器的数据库配置，拒绝覆盖。" >&2
    exit 2
  fi
elif [[ -e "$db_file" ]]; then
  echo "检测到没有安装配置的现有业务数据库，拒绝接管。" >&2
  exit 2
fi
if [[ -e "$root/web/.env.local" ]]; then
  if [[ -L "$root/web/.env.local" ]] || ! grep -qx 'SEEALPHA_API_URL=http://127.0.0.1:8000' "$root/web/.env.local"; then
    echo "现有 web/.env.local 未指向本机 API，拒绝覆盖或部署。" >&2
    exit 2
  fi
fi

mkdir -p "$root/.local/config/api" "$root/.local/state/api" "$root/.local/run/seealpha" "$root/.local/logs/seealpha"

echo "[1/5] 准备固定版本的独立 Engine"
if [[ ! -e "$engine_dir" ]]; then
  git clone --quiet --no-checkout https://github.com/saoxia/seealpha-engine.git "$engine_dir"
  git -C "$engine_dir" checkout --quiet --detach "$engine_sha"
fi
if [[ ! -d "$engine_dir/.git" || "$(git -C "$engine_dir" rev-parse HEAD)" != "$engine_sha" ]]; then
  echo "本地 Engine 来源与审查版本不一致；不会自动覆盖。" >&2
  exit 1
fi
for contract in core/engine_contracts.py core/trading_contracts.py; do
  if ! cmp -s "$root/$contract" "$engine_dir/$contract"; then
    echo "Engine / Platform 契约不一致：$contract；拒绝启动不兼容版本。" >&2
    exit 1
  fi
done

echo "[2/5] 安装隔离的 Platform 与 Engine Python 依赖"
uv sync --project "$root/platform" --locked --no-default-groups
uv sync --project "$engine_dir" --locked --no-default-groups

echo "[3/5] 安装并构建 Web 前端"
if [[ ! -e "$root/web/.env.local" ]]; then
  cp "$root/web/.env.example" "$root/web/.env.local"
  chmod 600 "$root/web/.env.local"
fi
(
  cd "$root/web"
  npm ci
  npm run build
)

echo "[4/5] 初始化独立本机数据库与服务令牌"
if [[ ! -e "$db_url_file" ]]; then
  printf '%s\n' "$db_url" > "$db_url_file"
  chmod 600 "$db_url_file"
fi
if [[ ! -e "$token_file" ]]; then
  "$root/platform/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))' > "$token_file"
  chmod 600 "$token_file"
fi
"$root/platform/.venv/bin/python" "$root/scripts/local_runtime.py" check-config
new_database=0
if [[ ! -e "$db_file" ]]; then
  new_database=1
  "$root/platform/.venv/bin/alembic" upgrade head
fi
"$root/platform/.venv/bin/python" "$root/scripts/local_runtime.py" check-database

echo "[5/5] 完成"
if [[ "$new_database" -eq 1 && -t 0 ]]; then
  "$root/platform/.venv/bin/python" "$root/scripts/local_runtime.py" admin create admin || {
    echo "管理员未创建。可稍后执行：./seealpha admin create admin" >&2
  }
elif [[ "$new_database" -eq 1 ]]; then
  echo "非交互安装：请执行 ./seealpha admin create admin，交互设置管理员密码。"
fi
echo "运行 ./seealpha start 后在本机打开 http://127.0.0.1:3000"
echo "注意：此安装模式不自动启用实盘，也不支持既有生产数据库原地升级。"
