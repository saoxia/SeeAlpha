#!/usr/bin/env bash
# Published images only; never builds code or touches an unrelated Docker stack.
set -euo pipefail
umask 077
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$root/.local/config/compose/local.env"
compose="$root/docker/compose.local.yml"
action="${1:-help}"
if [[ $# -gt 0 ]]; then shift; fi

if [[ "$action" == help || "$action" == -h || "$action" == --help ]]; then
  cat <<'HELP'
./seealpha container up <发布标签>    首次拉取并运行预构建镜像（如 v0.2.0）
./seealpha container start          启动已配置的容器
./seealpha container stop           停止容器，保留所有数据卷
./seealpha container status         查看容器状态
./seealpha container admin create [用户名]  交互式创建管理员
./seealpha container admin reset [用户名]   交互式重置管理员密码
HELP
  exit 0
fi
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "需要 Docker Engine/Desktop 及 Docker Compose v2。" >&2
  exit 2
fi
if [[ "$action" == up ]]; then
  requested="${1:-}"
  if [[ ! "$requested" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([-.][A-Za-z0-9.-]+)?$ && ! "$requested" =~ ^sha-[a-f0-9]{40}$ ]]; then
    echo "请指定已发布的完整版本标签：./seealpha container up vX.Y.Z" >&2
    exit 2
  fi
  if [[ ! -e "$env_file" ]]; then
    if ! command -v openssl >/dev/null 2>&1; then
      echo "首次启动需要 openssl 生成本机私有随机令牌。" >&2
      exit 2
    fi
    mkdir -p "$(dirname "$env_file")"
    token="$(openssl rand -hex 32)"
    printf 'SEEALPHA_VERSION=%s\nENGINE_TOKEN=%s\n' "$requested" "$token" > "$env_file"
    chmod 600 "$env_file"
  fi
elif [[ ! -f "$env_file" ]]; then
  echo "尚未初始化本地容器部署，请先运行 ./seealpha container up <发布标签>。" >&2
  exit 2
fi
if [[ -L "$env_file" || ! -f "$env_file" ]]; then
  echo "拒绝接管符号链接或非普通文件形式的 Compose 私有配置。" >&2
  exit 2
fi
version="$(sed -n 's/^SEEALPHA_VERSION=//p' "$env_file")"
token="$(sed -n 's/^ENGINE_TOKEN=//p' "$env_file")"
if [[ ! "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([-.][A-Za-z0-9.-]+)?$ && ! "$version" =~ ^sha-[a-f0-9]{40}$ ]] ||
   [[ ! "$token" =~ ^[a-f0-9]{64}$ ]]; then
  echo "Compose 本机版本或 Token 配置无效；拒绝使用。" >&2
  exit 2
fi
if [[ "$action" == up && "$version" != "$requested" ]]; then
  echo "当前已固定版本 $version；不能通过 up 隐式升级或切换有状态容器。" >&2
  exit 2
fi
dc() { docker compose --env-file "$env_file" -f "$compose" "$@"; }
case "$action" in
  up)
    [[ $# -eq 1 ]] || { echo "up 只接受一个版本标签。" >&2; exit 2; }
    dc pull
    dc up -d
    echo "SeeAlpha 本地容器已启动：http://127.0.0.1:3000"
    echo "首次安装请运行 ./seealpha container admin create admin"
    ;;
  start) [[ $# -eq 0 ]] || exit 2; dc up -d ;;
  stop) [[ $# -eq 0 ]] || exit 2; dc stop ;;
  status) [[ $# -eq 0 ]] || exit 2; dc ps ;;
  admin)
    verb="${1:-}"; username="${2:-admin}"
    if [[ $# -gt 2 || ! "$verb" =~ ^(create|reset)$ || ! "$username" =~ ^[a-zA-Z0-9_.-]{3,64}$ ]]; then
      echo "仅支持 admin create|reset [用户名]；密码通过交互输入。" >&2
      exit 2
    fi
    operation=create-user
    if [[ "$verb" == reset ]]; then operation=reset-password; fi
    dc exec platform /app/platform/.venv/bin/python -m api.auth.manage "$operation" "$username"
    ;;
  *) echo "未知的容器命令：$action" >&2; exit 2 ;;
esac
