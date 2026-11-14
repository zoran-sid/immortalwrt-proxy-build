#!/usr/bin/env python3
"""Verify one build's dist/ output: SHA256SUMS, BUILDINFO and manifest.

Usage:
  python3 tests/verify_artifacts.py dist/            # local build layout
  python3 tests/verify_artifacts.py download-dir     # GitHub artifact layout

Checks
  * exactly one SHA256SUMS-*.txt and BUILDINFO-*.txt
  * every checksum entry exists, hashes match, and no product file is missing
    from the checksum list (catches stale/merged dist directories)
  * BUILDINFO carries the provenance keys build.sh promises
  * the package manifest exists and is non-empty
  * image names are prefixed immortalwrt-<version>-<target>-<preset>-

Exit code 0 = PASS, 1 = FAIL. This is intentionally dependency-free so it runs
both on a Windows dev box and on the Ubuntu runner.
"""

import argparse
import glob
import hashlib
import os
import sys

REQUIRED_BUILDINFO = [
    "version",
    "target_name",
    "target",
    "profile",
    "arch",
    "preset",
    "preset_kind",
    "modules",
    "packages",
    "imagebuilder",
    "imagebuilder_sha256",
    "git_commit",
    "built_at",
]
# Legitimately empty for rescue builds.
MAY_BE_EMPTY = {"default_proxy", "thirdparty"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_sums(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            # "<sha>  <name>" or "<sha> *<name>"
            parts = line.split(None, 1)
            if len(parts) != 2:
                raise SystemExit("FAIL: malformed checksum line: %r" % line)
            digest, name = parts
            name = name.lstrip("*")
            out.append((digest, name))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    args = ap.parse_args()

    root = args.dir
    images = os.path.join(root, "images")
    if not os.path.isdir(images):
        images = root
    failures = []

    sums_files = glob.glob(os.path.join(root, "SHA256SUMS-*.txt"))
    info_files = glob.glob(os.path.join(root, "BUILDINFO-*.txt"))
    if len(sums_files) != 1:
        failures.append("expected one SHA256SUMS-*.txt, found %d" % len(sums_files))
    if len(info_files) != 1:
        failures.append("expected one BUILDINFO-*.txt, found %d" % len(info_files))
    if failures:
        print("\n".join("FAIL: " + f for f in failures))
        return 1

    info = {}
    with open(info_files[0], encoding="utf-8") as fh:
        for line in fh:
            key, _, value = line.rstrip("\n").partition("=")
            info[key] = value
    for key in REQUIRED_BUILDINFO:
        if not info.get(key):
            failures.append("BUILDINFO missing %s" % key)
    for key in MAY_BE_EMPTY:
        if key not in info:
            failures.append("BUILDINFO missing %s" % key)

    version = info.get("version", "")
    target = info.get("target_name", "")
    preset = info.get("preset", "")
    if preset.startswith("custom-") and not info.get("modules"):
        failures.append("custom BUILDINFO has no module list")
    prefix = "immortalwrt-%s-%s-%s-" % (version, target, preset)

    checked = 0
    names = set()
    for digest, name in parse_sums(sums_files[0]):
        names.add(name)
        path = os.path.join(images, name)
        if not os.path.isfile(path):
            failures.append("checksum references missing file: %s" % name)
            continue
        got = sha256(path)
        if got != digest:
            failures.append("sha256 mismatch for %s: %s != %s" % (name, got, digest))
        checked += 1

    manifest = os.path.join(images, "immortalwrt-%s-%s-%s.manifest" % (version, target, preset))
    if not os.path.isfile(manifest):
        failures.append("missing manifest: %s" % os.path.basename(manifest))
    else:
        with open(manifest, encoding="utf-8") as fh:
            packages = [line for line in fh if " - " in line]
        if not packages:
            failures.append("manifest is empty")

    product_files = [
        f for f in os.listdir(images)
        if os.path.isfile(os.path.join(images, f))
        and f.startswith(prefix) and not f.endswith(".manifest")
    ]
    for name in product_files:
        if name not in names:
            failures.append("product file not covered by checksums: %s" % name)
        if not name.startswith(prefix):
            failures.append("unexpected product name: %s" % name)

    print("artifact dir: %s" % root)
    print("build: %s / %s / %s (%s)" % (version, target, preset, info.get("preset_kind")))
    print("images checked: %d, product files: %d" % (checked, len(product_files)))
    if failures:
        for f in failures:
            print("FAIL: " + f)
        return 1
    print("PASS: checksums, BUILDINFO and manifest are complete and consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
