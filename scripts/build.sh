#!/usr/bin/env bash
# Build one ImmortalWrt image from a target + preset (or an explicit module list).
#
#   TARGET_NAME=x86-64 PRESET=standard bash scripts/build.sh
#   TARGET_NAME=gl-mt2500 PRESET=lite     bash scripts/build.sh
#   TARGET_NAME=x86-64 PRESET=custom MODULES=base,nat,proxy-homeproxy bash scripts/build.sh
#
# Output: dist/images/* plus dist/SHA256SUMS-<ver>-<target>-<preset>.txt and
#         dist/BUILDINFO-<ver>-<target>-<preset>.txt
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

VERSION="${VERSION:-25.12.2}"
TARGET_NAME="${TARGET_NAME:-x86-64}"
PRESET="${PRESET:-standard}"
MODULES="${MODULES:-}"
ROOTFS_OVERRIDE="${ROOTFS_PARTSIZE:-}"
MIRROR="${MIRROR:-https://downloads.immortalwrt.org}"
WORKDIR="${WORKDIR:-$REPO_ROOT/.work}"
OUTDIR="${OUTDIR:-$REPO_ROOT/dist}"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

mkdir -p "$WORKDIR" "$OUTDIR/images"
PLAN_ENV="$WORKDIR/plan-${TARGET_NAME}-${PRESET}.env"
OVERLAY="$WORKDIR/overlay-${TARGET_NAME}-${PRESET}"

log "resolving target=$TARGET_NAME preset=$PRESET"
resolve_args=(build --target "$TARGET_NAME" --preset "$PRESET" --format env --out "$PLAN_ENV" --overlay "$OVERLAY")
[ -n "$MODULES" ] && resolve_args+=(--modules "$MODULES")
python3 "$REPO_ROOT/scripts/resolve.py" "${resolve_args[@]}"

# shellcheck disable=SC1090
source "$PLAN_ENV"
[ -n "$ROOTFS_OVERRIDE" ] && ROOTFS_PARTSIZE="$ROOTFS_OVERRIDE"

# Fail in seconds, not after a 20-minute build, if the resolver/build.sh contract
# drifts (run 43551668831 died at the very end because PRESET_KIND was missing).
for required in TARGET_NAME TARGET SUBTARGET PROFILE ARCH TARGET_KIND \
                ROOTFS_PARTSIZE PRESET_NAME PRESET_KIND PRESET_PROXY \
                MODULE_LIST PACKAGES; do
  if [[ -z "${!required+x}" ]]; then
    die "resolve.py did not emit $required in $PLAN_ENV"
  fi
done

TARGET_DASH="${TARGET}-${SUBTARGET}"
BASE_URL="${MIRROR}/releases/${VERSION}/targets/${TARGET}/${SUBTARGET}"
IB_NAME="immortalwrt-imagebuilder-${VERSION}-${TARGET_DASH}.Linux-x86_64.tar.zst"
IB_DIR="$WORKDIR/ib-${VERSION}-${TARGET_DASH}"

log "version=$VERSION target=$TARGET/$SUBTARGET profile=$PROFILE preset=$PRESET_NAME"
log "modules: $MODULE_LIST"

cd "$WORKDIR"
log "fetching checksums and ImageBuilder"
curl -fsSL --retry 3 --retry-delay 3 -o "sha256sums-${TARGET_DASH}" "$BASE_URL/sha256sums" \
  || die "cannot fetch $BASE_URL/sha256sums"
if [[ ! -s "$IB_NAME" ]]; then
  curl -fL --retry 3 --retry-delay 3 -o "$IB_NAME" "$BASE_URL/$IB_NAME" \
    || die "cannot download $IB_NAME"
fi
# downloads use "<sha> *<file>" (binary marker)
awk -v n="$IB_NAME" '$2 == n || $2 == "*" n { print $1 "  " n }' "sha256sums-${TARGET_DASH}" > "ib-${TARGET_DASH}.sha256"
[[ -s "ib-${TARGET_DASH}.sha256" ]] || die "no checksum entry for $IB_NAME"
sha256sum -c "ib-${TARGET_DASH}.sha256" || die "ImageBuilder checksum mismatch"
IB_SHA="$(awk '{print $1}' "ib-${TARGET_DASH}.sha256")"

log "extracting ImageBuilder"
rm -rf "$IB_DIR"
mkdir -p "$IB_DIR"
tar --zstd -xf "$IB_NAME" -C "$IB_DIR" --strip-components=1 || die "extract failed"
[[ -f "$IB_DIR/Makefile" ]] || die "unexpected ImageBuilder layout"

log "fetching third-party assets"
TP_WORK="$WORKDIR/thirdparty-${TARGET_NAME}-${PRESET}"
TP_ENV="$WORKDIR/thirdparty-${TARGET_NAME}-${PRESET}.env"
rm -rf "$TP_WORK"
python3 "$REPO_ROOT/scripts/thirdparty.py" \
  --plan "$PLAN_ENV.json" --workdir "$TP_WORK" --overlay "$OVERLAY" --out-env "$TP_ENV"
# shellcheck disable=SC1090
source "$TP_ENV"

PKGS="$PACKAGES"
if [[ -n "${TP_REPOS_DIRS:-}" ]]; then
  # A local apk index rebuilt from third-party apks is unsigned and the
  # ImageBuilder verifies package indexes, so apk rejects it. The old workaround
  # disabled signature verification for the *whole* build, which is not an
  # acceptable trade-off. Refuse before touching the index until the local
  # repository can be signed (AGENTS.md §7.14). Third-party *binaries* (overlay)
  # are unaffected.
  die "third-party apk repositories need a signed package index (see AGENTS.md §7.14); refusing to disable apk signature checks"
fi
if [[ -n "${TP_PACKAGES:-}" ]]; then
  PKGS="$PKGS $TP_PACKAGES"
fi

if [[ -n "${PRESET_PROXY:-}" && -f "$OVERLAY/etc/config/proxy-guard" ]]; then
  sed -i "s/option default '.*'/option default '${PRESET_PROXY}'/" "$OVERLAY/etc/config/proxy-guard"
  log "default proxy for this target: $PRESET_PROXY"
fi

log "building image"
cd "$IB_DIR"
make_args=(image "PROFILE=$PROFILE" "PACKAGES=$PKGS" "ROOTFS_PARTSIZE=$ROOTFS_PARTSIZE")
if [[ -n "$(find "$OVERLAY" -type f -print -quit 2>/dev/null)" ]]; then
  make_args+=("FILES=$OVERLAY")
fi
make "${make_args[@]}" 2>&1 | tee "$WORKDIR/build-${TARGET_NAME}-${PRESET}.log"

BIN_DIR="$IB_DIR/bin/targets/$TARGET/$SUBTARGET"
[[ -d "$BIN_DIR" ]] || die "no build output under $BIN_DIR"

log "collecting images"
OUT_PREFIX="immortalwrt-${VERSION}-${TARGET_NAME}-${PRESET_NAME}"
IB_PREFIX="immortalwrt-${VERSION}-${TARGET_DASH}-"
# `dist/` is shared by every local build. Drop only this target/preset's
# previous outputs so a stale image can never end up in the new checksums or
# be mistaken for a fresh artifact (CI starts from a clean checkout, but local
# rebuilds do not).
find "$OUTDIR/images" -maxdepth 1 -type f \
  \( -name "${OUT_PREFIX}-*" -o -name "${OUT_PREFIX}.manifest" \) -delete
shopt -s nullglob
copied=0
# x86 produces disk images/ISOs, rockchip produces .img.gz, mediatek/filogic
# glinet devices produce .bin (sysupgrade/factory) and some targets use .itb.
# Bootloader/initramfs artefacts (preloader, u-boot/fip, *-kernel) are not
# installable firmware and are skipped.
for f in "$BIN_DIR"/*.img.gz "$BIN_DIR"/*.qcow2 "$BIN_DIR"/*.vmdk \
         "$BIN_DIR"/*.vhdx "$BIN_DIR"/*.vdi "$BIN_DIR"/*.iso \
         "$BIN_DIR"/*.itb "$BIN_DIR"/*.bin "$BIN_DIR"/*.ubi; do
  name="$(basename "$f")"
  case "$name" in
    *rootfs*|*kernel*|*initramfs*|*preloader*|*u-boot*|*uboot*|*.fip) continue ;;
  esac
  rest="${name#"$IB_PREFIX"}"
  rest="${rest#"${PROFILE}"-}"
  cp -f "$f" "$OUTDIR/images/${OUT_PREFIX}-${rest}"
  copied=$((copied + 1))
done
(( copied > 0 )) || die "no comparable images produced under $BIN_DIR"

# `make image` does not emit a manifest; read the installed-package database
# from the assembled rootfs instead of rebuilding it with `make manifest`.
MANIFEST_OUT="$OUTDIR/images/${OUT_PREFIX}.manifest"
ROOTFS_DIR="$(find "$IB_DIR/build_dir" -maxdepth 2 -type d -name 'root-*' 2>/dev/null | head -1)"
if [[ -n "$ROOTFS_DIR" && -f "$ROOTFS_DIR/lib/apk/db/installed" ]]; then
  awk -F: '/^P:/{n=substr($0,3)} /^V:/{if (n!="") {print n " - " substr($0,3); n=""}}' \
    "$ROOTFS_DIR/lib/apk/db/installed" | sort > "$MANIFEST_OUT"
elif [[ -n "$ROOTFS_DIR" && -f "$ROOTFS_DIR/usr/lib/opkg/status" ]]; then
  awk -F': ' '/^Package:/{p=$2} /^Version:/{if (p!="") {print p " - " $2; p=""}}' \
    "$ROOTFS_DIR/usr/lib/opkg/status" | sort > "$MANIFEST_OUT"
else
  echo "WARN: no package manifest could be produced" >&2
fi

log "writing metadata"
META="${VERSION}-${TARGET_NAME}-${PRESET_NAME}"
# Checksum only this build's files. Summing `dist/images/*` used to pick up
# images from other targets when several builds shared one dist/ directory.
mapfile -t product_files < <(find "$OUTDIR/images" -maxdepth 1 -type f \
  \( -name "${OUT_PREFIX}-*" -o -name "${OUT_PREFIX}.manifest" \) -printf '%f\n' | sort)
(( ${#product_files[@]} > 0 )) || die "no product files to checksum"
(
  cd "$OUTDIR/images"
  sha256sum -- "${product_files[@]}" > "$OUTDIR/SHA256SUMS-${META}.txt"
)
TP_INFO="$(python3 - "$PLAN_ENV.json" <<'PY'
import json, sys
plan = json.load(open(sys.argv[1]))
items = []
for e in plan.get("thirdparty_repos", []):
    items.append("%s@repo:%s" % (e["name"], e["sha256"][:12]))
for e in plan.get("thirdparty_binaries", []):
    items.append("%s@bin:%s" % (e["name"], e["sha256"][:12]))
print(",".join(items))
PY
)"
GIT_SHA="${GITHUB_SHA:-$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)}"
{
  echo "version=$VERSION"
  echo "target_name=$TARGET_NAME"
  echo "target=$TARGET/$SUBTARGET"
  echo "profile=$PROFILE"
  echo "arch=$ARCH"
  echo "preset=$PRESET_NAME"
  echo "preset_kind=$PRESET_KIND"
  echo "default_proxy=$PRESET_PROXY"
  echo "modules=$MODULE_LIST"
  echo "packages=$PKGS"
  echo "rootfs_partsize=$ROOTFS_PARTSIZE"
  echo "thirdparty=$TP_INFO"
  echo "imagebuilder=$IB_NAME"
  echo "imagebuilder_sha256=$IB_SHA"
  echo "git_commit=$GIT_SHA"
  echo "git_ref=${GITHUB_REF_NAME:-local}"
  echo "run_id=${GITHUB_RUN_ID:-local}"
  echo "run_url=${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-local}/actions/runs/${GITHUB_RUN_ID:-0}"
  echo "built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$OUTDIR/BUILDINFO-${META}.txt"

log "done: $(find "$OUTDIR/images" -maxdepth 1 -type f | wc -l | tr -d ' ') files in $OUTDIR/images"
cat "$OUTDIR/BUILDINFO-${META}.txt"
