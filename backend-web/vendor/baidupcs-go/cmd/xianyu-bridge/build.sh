#!/bin/sh
# Use the project's already-installed, verified Go and private module caches.
set -eu
bridge_module=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
bridge_project=$(CDPATH= cd -- "$bridge_module/../../.." && pwd)
bridge_runtime="$bridge_project/data/local-dev/runtime"
bridge_go="$bridge_runtime/go/bin/go"
if [ ! -x "$bridge_go" ]; then
    printf '%s\n' 'Project-private Go toolchain is missing.' >&2
    exit 1
fi
if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && [ "$1" != '--test' ]; }; then
    printf '%s\n' 'Usage: sh cmd/xianyu-bridge/build.sh [--test]' >&2
    exit 2
fi
export GOENV=off GOTOOLCHAIN=local CGO_ENABLED=0
export GOPATH="$bridge_runtime/go-work"
export GOMODCACHE="$bridge_runtime/go-mod-cache"
export GOCACHE="$bridge_runtime/go-build-cache"
export TEST_TELEMETRY_DIR="$bridge_runtime/go-telemetry"
cd "$bridge_module"
if [ "${1:-}" = '--test' ]; then
    "$bridge_go" test ./cmd/xianyu-bridge
fi
mkdir -p "$bridge_runtime/baidupcs-go"
"$bridge_go" build -trimpath -o "$bridge_runtime/baidupcs-go/xianyu-bridge" ./cmd/xianyu-bridge
