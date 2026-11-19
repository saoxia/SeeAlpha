#!/usr/bin/env bash
# Install only project-local build tools. Never invoke sudo or change system packages.
set -euo pipefail
umask 077
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bin="$root/.local/bin"
tools_dir="$root/.local/tools"
mkdir -p "$bin" "$tools_dir"
export PATH="$bin:$PATH"

for command in curl tar git; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "缺少 $command；请先通过操作系统的官方工具安装，再重新运行。" >&2
    exit 2
  fi
done
if command -v sha256sum >/dev/null 2>&1; then
  hash_file() { sha256sum "$1" | awk '{print $1}'; }
elif command -v shasum >/dev/null 2>&1; then
  hash_file() { shasum -a 256 "$1" | awk '{print $1}'; }
else
  echo "需要 sha256sum 或 shasum 以验证下载文件。" >&2
  exit 2
fi

case "$(uname -s):$(uname -m)" in
  Linux:x86_64) uv_target=x86_64-unknown-linux-gnu; node_target=linux-x64 ;;
  Linux:aarch64|Linux:arm64) uv_target=aarch64-unknown-linux-gnu; node_target=linux-arm64 ;;
  Darwin:x86_64) uv_target=x86_64-apple-darwin; node_target=darwin-x64 ;;
  Darwin:arm64|Darwin:aarch64) uv_target=aarch64-apple-darwin; node_target=darwin-arm64 ;;
  *) echo "尚未提供该操作系统/架构的预构建工具；请手动安装 uv 与 Node.js。" >&2; exit 2 ;;
esac

tmp="$(mktemp -d "$tools_dir/.download.XXXXXXXX")"
trap 'rm -rf "$tmp"' EXIT
download() {
  curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error --retry 3 "$1" -o "$2"
}
verify() {
  local artifact="$1" expected="$2" actual
  if [[ ! "$expected" =~ ^[a-fA-F0-9]{64}$ ]]; then
    echo "无法取得可信的 SHA256 校验值：$artifact" >&2; exit 1
  fi
  actual="$(hash_file "$artifact")"
  if [[ "$actual" != "$expected" ]]; then
    echo "SHA256 不匹配，拒绝解压：$artifact" >&2; exit 1
  fi
}
if ! command -v uv >/dev/null 2>&1; then
  uv_version=0.13.9
  uv_asset="uv-${uv_target}.tar.gz"
  uv_url="https://github.com/astral-sh/uv/releases/download/${uv_version}/${uv_asset}"
  echo "安装项目私有 uv $uv_version ($uv_target)"
  download "$uv_url" "$tmp/uv.tar.gz"
  download "$uv_url.sha256" "$tmp/uv.sha256"
  expected="$(awk 'NR==1 {print $1}' "$tmp/uv.sha256")"
  verify "$tmp/uv.tar.gz" "$expected"
  tar -xzf "$tmp/uv.tar.gz" -C "$tmp" "uv-${uv_target}/uv"
  install -m 700 "$tmp/uv-${uv_target}/uv" "$bin/uv"
fi
if ! command -v node >/dev/null 2>&1 || ! node -e 'const [major,minor]=process.versions.node.split(".").map(Number);process.exit(major>22||(major===22&&minor>=13)?0:1)' 2>/dev/null || ! command -v npm >/dev/null 2>&1; then
  node_version=v22.16.0
  node_asset="node-${node_version}-${node_target}.tar.xz"
  node_url="https://nodejs.org/dist/${node_version}"
  echo "安装项目私有 Node.js $node_version ($node_target)"
  download "$node_url/$node_asset" "$tmp/node.tar.xz"
  download "$node_url/SHASUMS256.txt" "$tmp/SHASUMS256.txt"
  expected="$(awk -v name="$node_asset" '$2 == name {print $1; exit}' "$tmp/SHASUMS256.txt")"
  verify "$tmp/node.tar.xz" "$expected"
  tar -xJf "$tmp/node.tar.xz" -C "$tmp"
  dest="$tools_dir/node-${node_version}-${node_target}"
  if [[ ! -e "$dest" ]]; then
    mv "$tmp/node-${node_version}-${node_target}" "$dest"
  fi
  for name in node npm npx; do
    if [[ -e "$bin/$name" || -L "$bin/$name" ]]; then
      echo "本机私有工具目标已存在：$bin/$name；拒绝覆盖。" >&2; exit 2
    fi
    ln -s "$dest/bin/$name" "$bin/$name"
  done
fi
echo "本机依赖检查完成（仅写入 .local/）。"
