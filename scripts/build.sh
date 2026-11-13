#!/usr/bin/env bash
# Build an ImmortalWrt image with the official ImageBuilder (no source compile).
#
#   VERSION=25.12.2 PROXY_PROFILE=passwall bash scripts/build.sh
#
# Environment:
#   VERSION          ImmortalWrt release, e.g. 25.12.2 or 24.10.6
#   TARGET           target/subtarget, default x86/64
#   PROFILE          device profile, default generic
#   PROXY_PROFILE    directory under configs/proxy/, default passwall
#   ROOTFS_PARTSIZE  root filesystem size in MiB, default 1024
#   MIRROR           download mirror, default https://downloads.immortalwrt.org
#
# Output: dist/images/* plus dist/SHA256SUMS and dist/BUILDINFO
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

VERSION="${VERSION:-25.12.2}"
TARGET="${TARGET:-x86/64}"
PROFILE="${PROFILE:-generic}"
PROXY_PROFILE="${PROXY_PROFILE:-passwall}"
ROOTFS_PARTSIZE="${ROOTFS_PARTSIZE:-1024}"
MIRROR="${MIRROR:-https://downloads.immortalwrt.org}"
WORKDIR="${WORKDIR:-$REPO_ROOT/.work}"
OUTDIR="${OUTDIR:-$REPO_ROOT/dist}"

TARGET_DASH="${TARGET//\//-}"
BASE_URL="${MIRROR}/releases/${VERSION}/targets/${TARGET}"
IB_NAME="immortalwrt-imagebuilder-${VERSION}-${TARGET_DASH}.Linux-x86_64.tar.zst"
IB_DIR="$WORKDIR/ib-${VERSION}-${TARGET_DASH}"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ -f "$REPO_ROOT/configs/proxy/${PROXY_PROFILE}/packages.txt" ]] \
  || die "unknown proxy profile '$PROXY_PROFILE' (see configs/proxy/)"

log "version=$VERSION target=$TARGET profile=$PROFILE proxy=$PROXY_PROFILE"
mkdir -p "$WORKDIR" "$OUTDIR/images"
cd "$WORKDIR"

log "fetching checksums and ImageBuilder"
curl -fsSL --retry 3 --retry-delay 3 -o sha256sums "$BASE_URL/sha256sums" \
  || die "cannot fetch $BASE_URL/sha256sums"
if [[ ! -s "$IB_NAME" ]]; then
  curl -fL --retry 3 --retry-delay 3 -o "$IB_NAME" "$BASE_URL/$IB_NAME" \
    || die "cannot download $IB_NAME"
fi
# sha256sums lists "<hash> *<file>" (binary marker) on ImmortalWrt downloads.
awk -v n="$IB_NAME" '$2 == n || $2 == "*" n { print $1 "  " n }' sha256sums > ib.sha256
[[ -s ib.sha256 ]] || die "no checksum entry for $IB_NAME"
sha256sum -c ib.sha256 || die "ImageBuilder checksum mismatch"

log "extracting ImageBuilder"
rm -rf "$IB_DIR"
mkdir -p "$IB_DIR"
tar --zstd -xf "$IB_NAME" -C "$IB_DIR" --strip-components=1 || die "extract failed"
[[ -x "$IB_DIR/Makefile" || -f "$IB_DIR/Makefile" ]] || die "unexpected ImageBuilder layout"

log "resolving package list"
mapfile -t PKGS < <(
  cat "$REPO_ROOT/configs/common/packages.txt" \
      "$REPO_ROOT/configs/proxy/${PROXY_PROFILE}/packages.txt" \
  | sed -e 's/#.*$//' -e 's/[[:space:]]//g' \
  | sed '/^$/d' \
  | awk '!seen[$0]++'
)
[[ ${#PKGS[@]} -gt 0 ]] || die "package list is empty"
PKG_STR="$(printf '%s ' "${PKGS[@]}")"
PKG_STR="${PKG_STR% }"

FILES_DIR="$REPO_ROOT/configs/proxy/${PROXY_PROFILE}/files"
[[ -d "$FILES_DIR" ]] || FILES_DIR="$REPO_ROOT/configs/common/files"
[[ -d "$FILES_DIR" ]] || FILES_DIR=""

log "packages: $PKG_STR"
cd "$IB_DIR"
make_args=(image "PROFILE=$PROFILE" "PACKAGES=$PKG_STR" "ROOTFS_PARTSIZE=$ROOTFS_PARTSIZE")
[[ -n "$FILES_DIR" ]] && make_args+=("FILES=$FILES_DIR")
make "${make_args[@]}" 2>&1 | tee "$WORKDIR/build.log"

BIN_DIR="$IB_DIR/bin/targets/$TARGET"
[[ -d "$BIN_DIR" ]] || die "no build output under $BIN_DIR"

log "collecting images"
shopt -s nullglob
copied=0
for f in "$BIN_DIR"/*.img.gz "$BIN_DIR"/*.qcow2 "$BIN_DIR"/*.vmdk \
         "$BIN_DIR"/*.vhdx "$BIN_DIR"/*.vdi "$BIN_DIR"/*.iso; do
  name="$(basename "$f")"
  case "$name" in *rootfs*|*kernel*) continue ;; esac
  rest="${name#immortalwrt-}"
  rest="${rest#"${VERSION}"-}"
  rest="${rest#"${TARGET_DASH}"-}"
  rest="${rest#"${PROFILE}"-}"
  cp -f "$f" "$OUTDIR/images/immortalwrt-${VERSION}-x86_64-${PROXY_PROFILE}-${rest}"
  copied=$((copied + 1))
done
(( copied > 0 )) || die "no comparable images produced"

MANIFEST="$BIN_DIR/immortalwrt-${VERSION}-${TARGET_DASH}.manifest"
if [[ -f "$MANIFEST" ]]; then
  cp -f "$MANIFEST" "$OUTDIR/images/immortalwrt-${VERSION}-x86_64-${PROXY_PROFILE}.manifest"
fi

log "writing metadata"
META_BASE="${VERSION}-x86_64-${PROXY_PROFILE}"
(
  cd "$OUTDIR/images"
  sha256sum -- * > "$OUTDIR/SHA256SUMS-${META_BASE}.txt"
)
GIT_SHA="${GITHUB_SHA:-$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)}"
{
  echo "version=$VERSION"
  echo "target=$TARGET"
  echo "profile=$PROFILE"
  echo "proxy_profile=$PROXY_PROFILE"
  echo "packages=$PKG_STR"
  echo "imagebuilder=$IB_NAME"
  echo "imagebuilder_sha256=$(awk '{print $1}' "$WORKDIR/ib.sha256")"
  echo "git_commit=$GIT_SHA"
  echo "git_ref=${GITHUB_REF_NAME:-local}"
  echo "run_id=${GITHUB_RUN_ID:-local}"
  echo "run_url=${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-local}/actions/runs/${GITHUB_RUN_ID:-0}"
  echo "built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$OUTDIR/BUILDINFO-${META_BASE}.txt"

log "done: $(find "$OUTDIR/images" -maxdepth 1 -type f | wc -l | tr -d ' ') files in $OUTDIR/images"
cat "$OUTDIR/BUILDINFO-${META_BASE}.txt"
