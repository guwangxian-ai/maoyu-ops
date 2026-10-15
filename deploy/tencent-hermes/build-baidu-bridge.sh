#!/bin/sh
# Cross-compile the audited vendor with the existing project-private toolchain.
set -eu
deploy_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
deploy_project_root=$(CDPATH= cd -- "$deploy_directory/../.." && pwd)
deploy_runtime="$deploy_project_root/data/local-dev/runtime"
deploy_go="$deploy_runtime/go/bin/go"
if [ ! -x "$deploy_go" ]; then
    printf '%s\n' 'Project-private Go toolchain is missing.' >&2
    exit 1
fi
if [ "$#" -gt 1 ]; then
    printf '%s\n' 'Usage: sh deploy/tencent-hermes/build-baidu-bridge.sh [amd64|arm64]' >&2
    exit 2
fi
deploy_architecture=${1:-amd64}
case "$deploy_architecture" in
    amd64|arm64) ;;
    *) printf '%s\n' 'Unsupported Linux architecture.' >&2; exit 2 ;;
esac
export GOENV=off GOTOOLCHAIN=local CGO_ENABLED=0 GOOS=linux GOARCH="$deploy_architecture"
export GOPATH="$deploy_runtime/go-work"
export GOMODCACHE="$deploy_runtime/go-mod-cache"
export GOCACHE="$deploy_runtime/go-build-cache"
export TEST_TELEMETRY_DIR="$deploy_runtime/go-telemetry"
cd "$deploy_project_root/backend-web/vendor/baidupcs-go"
mkdir -p "$deploy_directory/bin"
"$deploy_go" build -mod=readonly -trimpath -o "$deploy_directory/bin/xianyu-baidu-pcs-bridge" ./cmd/xianyu-bridge
chmod 755 "$deploy_directory/bin/xianyu-baidu-pcs-bridge"
printf 'Built project-private BaiduPCS-Go bridge for linux/%s\n' "$deploy_architecture"
