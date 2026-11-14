#!/usr/bin/env python3
"""Fetch, verify and unpack third-party assets declared in thirdparty/lock.yml.

Only URLs + sha256 from the lock file are trusted. Anything whose digest does
not match is a hard failure: a supply-chain mismatch must never reach an image.
"""

import argparse
import gzip
import hashlib
import json
import os
import shlex
import shutil
import sys
import tarfile
import time
import urllib.request

UA = {"User-Agent": "immortalwrt-proxy-build/1.0"}


def log(msg):
    sys.stderr.write("[thirdparty] %s\n" % msg)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, dest, want_sha):
    if os.path.isfile(dest) and sha256_file(dest) == want_sha:
        log("cached %s" % os.path.basename(dest))
        return
    tmp = dest + ".part"
    for attempt in range(1, 4):
        try:
            log("download %s" % url)
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
                shutil.copyfileobj(resp, out)
            break
        except Exception as exc:  # noqa: BLE001 - retry any transport error
            log("attempt %d failed: %s" % (attempt, exc))
            if attempt == 3:
                raise SystemExit("error: cannot download %s" % url)
            time.sleep(2 * attempt)
    got = sha256_file(tmp)
    if got != want_sha:
        os.unlink(tmp)
        raise SystemExit(
            "error: sha256 mismatch for %s\n  want %s\n  got  %s" % (url, want_sha, got)
        )
    os.replace(tmp, dest)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--overlay", required=True)
    ap.add_argument("--out-env", required=True)
    args = ap.parse_args()

    with open(args.plan, encoding="utf-8") as fh:
        plan = json.load(fh)

    os.makedirs(args.workdir, exist_ok=True)

    repo_dirs = []
    packages = []
    for entry in plan.get("thirdparty_repos") or []:
        name = entry["name"]
        dest_dir = os.path.join(args.workdir, name)
        tarball = os.path.join(args.workdir, name + ".tar.gz")
        download(entry["url"], tarball, entry["sha256"])
        if os.path.isdir(dest_dir):
            shutil.rmtree(dest_dir)
        os.makedirs(dest_dir)
        with tarfile.open(tarball, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name.startswith(("/", "..")):
                    raise SystemExit("error: unsafe path in %s" % tarball)
                tar.extract(member, dest_dir)
        # archives may nest their content in a single top directory
        entries = os.listdir(dest_dir)
        if "packages.adb" not in entries:
            subdirs = [e for e in entries if os.path.isdir(os.path.join(dest_dir, e))]
            if len(subdirs) == 1 and os.path.isfile(
                os.path.join(dest_dir, subdirs[0], "packages.adb")
            ):
                dest_dir = os.path.join(dest_dir, subdirs[0])
            else:
                raise SystemExit("error: %s has no packages.adb" % tarball)
        log("%s -> %s" % (name, dest_dir))
        repo_dirs.append(dest_dir)
        for pkg in entry.get("packages") or []:
            if pkg not in packages:
                packages.append(pkg)

    for entry in plan.get("thirdparty_binaries") or []:
        name = entry["name"]
        dest_rel = entry["dest"].lstrip("/")
        target = os.path.join(args.overlay, dest_rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        src = os.path.join(args.workdir, name + ".bin")
        download(entry["url"], src, entry["sha256"])
        if entry.get("format") == "gz":
            with gzip.open(src, "rb") as fin, open(target, "wb") as fout:
                shutil.copyfileobj(fin, fout)
        else:
            shutil.copyfile(src, target)
        os.chmod(target, 0o755)
        log("binary %s -> /%s" % (name, dest_rel))

    with open(args.out_env, "w", encoding="utf-8") as fh:
        fh.write("TP_REPOS_DIRS=%s\n" % shlex.quote(" ".join(repo_dirs)))
        fh.write("TP_PACKAGES=%s\n" % shlex.quote(" ".join(packages)))


if __name__ == "__main__":
    main()
