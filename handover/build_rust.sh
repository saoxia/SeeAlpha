#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source ./activate.sh
if [[ ! -x "$CARGO_HOME/bin/rustup" ]]; then
    curl --fail --location \
        https://static.rust-lang.org/rustup/dist/aarch64-apple-darwin/rustup-init \
        -o .local/tmp/rustup-init
    chmod +x .local/tmp/rustup-init
    .local/tmp/rustup-init -y --no-modify-path --profile minimal --default-toolchain none
fi
rustup toolchain install 1.99.1 --profile minimal
cargo build --release --locked --manifest-path pipeline/rust_converter/Cargo.toml
cargo test --release --locked --manifest-path pipeline/rust_converter/Cargo.toml
"$CARGO_TARGET_DIR/release/nautilus-data-converter" --help
