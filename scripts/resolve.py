#!/usr/bin/env python3
"""Resolve targets, presets and modules into a concrete build plan.

Used by scripts/build.sh, the GitHub workflows and tests/test_resolve.py.
Exit code is non-zero with a one-line ``error: ...`` message on any invalid
combination, so CI fails before spending 10 minutes on a build.

Usage:
  resolve.py build  --target x86-64 --preset standard [--modules a,b] [--overlay DIR]
  resolve.py matrix --event tag|dispatch [--target T --preset P --modules M]
  resolve.py check  --target x86-64 --preset custom --modules base,nat
"""

import argparse
import hashlib
import json
import os
import shutil
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fail(msg):
    sys.stderr.write("error: %s\n" % msg)
    raise SystemExit(2)


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        fail("missing file: %s" % os.path.relpath(path, ROOT))
    except yaml.YAMLError as exc:
        fail("invalid YAML in %s: %s" % (os.path.relpath(path, ROOT), exc))


def load_targets():
    out = {}
    for name in sorted(os.listdir(os.path.join(ROOT, "targets"))):
        if not name.endswith((".yml", ".yaml")):
            continue
        spec = load(os.path.join(ROOT, "targets", name))
        spec["name"] = spec.get("name") or name.rsplit(".", 1)[0]
        out[spec["name"]] = spec
    return out


def load_modules():
    out = {}
    base = os.path.join(ROOT, "modules")
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name, "module.yml")
        if not os.path.isfile(path):
            continue
        spec = load(path)
        spec["name"] = name
        spec.setdefault("packages", os.path.join(base, name, "packages.txt"))
        spec.setdefault("files", os.path.join(base, name, "files"))
        out[name] = spec
    return out


def load_presets():
    out = {}
    base = os.path.join(ROOT, "presets")
    for name in sorted(os.listdir(base)):
        if not name.endswith((".yml", ".yaml")):
            continue
        spec = load(os.path.join(base, name))
        spec["name"] = spec.get("name") or name.rsplit(".", 1)[0]
        out[spec["name"]] = spec
    return out


def compatible(mod, target):
    """Return None when compatible, else a reason string."""
    if mod.get("status") == "blocked":
        return "blocked: %s" % mod.get("blocked_reason", "unsupported")
    allowed_targets = mod.get("targets") or []
    if allowed_targets and target["name"] not in allowed_targets:
        return "not available for target %s" % target["name"]
    allowed_arch = mod.get("arch") or []
    if allowed_arch and target["arch"] not in allowed_arch:
        return "not available for arch %s" % target["arch"]
    return None


def read_packages(mod):
    path = mod["packages"]
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if line:
                out.append(line)
    return out


def merge_overlay(modules, mods_by_name, dest):
    """Copy every module's files/ into dest; identical duplicates collapse."""
    seen = {}
    for name in modules:
        src = mods_by_name[name].get("files")
        if not src or not os.path.isdir(src):
            continue
        for dirpath, _dirnames, filenames in os.walk(src):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, src)
                if rel in seen:
                    if _sha(full) == _sha(seen[rel]):
                        continue
                    fail(
                        "files/ conflict on %s between modules %s and %s"
                        % (rel, seen[rel].split(os.sep)[-3], name)
                    )
                seen[rel] = full
                target = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(full, target)
                exec_bit = os.stat(full).st_mode & 0o111
                os.chmod(target, 0o755 if exec_bit else 0o644)
    return len(seen)


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve(target_name, preset_name, modules_arg):
    targets = load_targets()
    mods = load_modules()
    presets = load_presets()

    if target_name not in targets:
        fail("unknown target %r (known: %s)" % (target_name, ", ".join(sorted(targets))))
    target = targets[target_name]
    if target.get("status", "ready") != "ready":
        fail(
            "target %s is %s: %s"
            % (target_name, target.get("status"), target.get("note", "not buildable"))
        )

    if preset_name == "custom" or preset_name.startswith("custom-"):
        if not modules_arg:
            fail("preset 'custom' requires --modules")
        requested = [m.strip() for m in modules_arg.split(",") if m.strip()]
        for name in requested:
            if name not in mods:
                fail("unknown module %r" % name)
        preset = {
            "name": "custom",
            "modules": requested,
            "proxy_default": "",
            "rootfs_partsize": None,
            "targets": [target_name],
        }
        preset_name = "custom-" + hashlib.sha256(
            ",".join(sorted(requested)).encode()
        ).hexdigest()[:8]
    else:
        if preset_name not in presets:
            fail("unknown preset %r (known: %s)" % (preset_name, ", ".join(sorted(presets))))
        preset = presets[preset_name]
        allowed = preset.get("targets") or []
        if allowed and "all" not in allowed and target_name not in allowed:
            fail("preset %s does not apply to target %s" % (preset_name, target_name))
        requested = list(preset.get("modules") or [])

    selected = []
    for name in requested:
        if name not in mods:
            fail("preset %s references unknown module %r" % (preset_name, name))
        reason = compatible(mods[name], target)
        if reason:
            fail("module %s cannot be used on %s (%s)" % (name, target_name, reason))
        if name not in selected:
            selected.append(name)

    # implicit modules: arch/target bound, never listed in presets
    for name, mod in mods.items():
        if not mod.get("implicit"):
            continue
        if compatible(mod, target) is None and name not in selected:
            selected.append(name)

    # one proxy frontend at a time needs the guard; add it automatically
    proxies = [m for m in selected if m.startswith("proxy-") and m != "proxy-guard"]
    if proxies and "proxy-guard" not in selected:
        reason = compatible(mods["proxy-guard"], target)
        if reason:
            fail("proxy-guard cannot be used on %s (%s)" % (target_name, reason))
        selected.append("proxy-guard")

    # pull in `requires` transitively, keeping dependencies first
    ordered = []

    def visit(name, chain):
        if name in ordered:
            return
        if name in chain:
            fail("circular requires: %s" % " -> ".join(chain + [name]))
        mod = mods[name]
        reason = compatible(mod, target)
        if reason:
            fail("module %s cannot be used on %s (%s)" % (name, target_name, reason))
        for dep in mod.get("requires") or []:
            if dep not in mods:
                fail("module %s requires unknown module %r" % (name, dep))
            visit(dep, chain + [name])
        ordered.append(name)

    for name in selected:
        visit(name, [])

    # conflicts
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if a in (mods[b].get("conflicts") or []) or b in (mods[a].get("conflicts") or []):
                fail("modules %s and %s conflict" % (a, b))

    proxy_default = preset.get("proxy_default") or ""
    if isinstance(proxy_default, dict):
        proxy_default = (
            proxy_default.get(target.get("kind", "baremetal"))
            or proxy_default.get("default", "")
            or ""
        )

    rootfs = preset.get("rootfs_partsize") or target.get("rootfs_partsize") or 1024
    need = sum(int(mods[m].get("min_flash_mb") or 0) for m in ordered)
    if need and need > rootfs - 128:
        fail(
            "modules need %s MiB but rootfs_partsize is %s MiB; raise rootfs_partsize"
            % (need, rootfs)
        )

    packages = []
    for name in ordered:
        for pkg in read_packages(mods[name]):
            if pkg not in packages:
                packages.append(pkg)

    thirdparty_repos = []
    thirdparty_bin = []
    lock = load(os.path.join(ROOT, "thirdparty", "lock.yml"))

    def lock_entry(key, module):
        entry = lock.get(key)
        if not entry:
            fail("module %s needs thirdparty entry %r in thirdparty/lock.yml" % (module, key))
        arch_entry = (entry.get("arches") or {}).get(target["arch"])
        if not arch_entry:
            fail("thirdparty %s has no build for arch %s" % (key, target["arch"]))
        return entry, arch_entry

    for name in ordered:
        mod = mods[name]
        repo_key = mod.get("thirdparty") if mod.get("source") == "thirdparty" else None
        bin_key = mod.get("thirdparty_binary")

        if repo_key:
            entry, arch_entry = lock_entry(repo_key, name)
            thirdparty_repos.append(
                {
                    "name": repo_key,
                    "url": arch_entry["url"],
                    "sha256": arch_entry["sha256"],
                    "packages": list(entry.get("packages") or []),
                }
            )
        if bin_key:
            entry, arch_entry = lock_entry(bin_key, name)
            thirdparty_bin.append(
                {
                    "name": bin_key,
                    "dest": entry.get("dest"),
                    "url": arch_entry["url"],
                    "sha256": arch_entry["sha256"],
                    "format": arch_entry.get("format", "raw"),
                }
            )

    return {
        "target": target,
        "target_name": target_name,
        "preset_name": preset_name,
        "preset_kind": "custom" if preset_name.startswith("custom-") else "preset",
        "modules": ordered,
        "packages": packages,
        "rootfs_partsize": rootfs,
        "proxy_default": proxy_default,
        "thirdparty_repos": thirdparty_repos,
        "thirdparty_binaries": thirdparty_bin,
        "version": None,
    }


def env_quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


def emit_env(plan, out, overlay):
    lines = [
        "TARGET_NAME=%s" % env_quote(plan["target_name"]),
        "TARGET=%s" % env_quote(plan["target"]["target"]),
        "SUBTARGET=%s" % env_quote(plan["target"]["subtarget"]),
        "PROFILE=%s" % env_quote(plan["target"]["profile"]),
        "ARCH=%s" % env_quote(plan["target"]["arch"]),
        "TARGET_KIND=%s" % env_quote(plan["target"].get("kind", "baremetal")),
        "ROOTFS_PARTSIZE=%s" % env_quote(plan["rootfs_partsize"]),
        "PRESET_NAME=%s" % env_quote(plan["preset_name"]),
        "PRESET_KIND=%s" % env_quote(plan["preset_kind"]),
        "PRESET_PROXY=%s" % env_quote(plan["proxy_default"]),
        "MODULE_LIST=%s" % env_quote(" ".join(plan["modules"])),
        "PACKAGES=%s" % env_quote(" ".join(plan["packages"])),
        "OVERLAY_DIR=%s" % env_quote(overlay or ""),
        "THIRDPARTY_REPOS=%s"
        % env_quote(" ".join(json.dumps(r, sort_keys=True) for r in plan["thirdparty_repos"])),
        "THIRDPARTY_BINARIES=%s"
        % env_quote(
            " ".join(json.dumps(r, sort_keys=True) for r in plan["thirdparty_binaries"])
        ),
    ]
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    with open(out + ".json", "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2, sort_keys=True)


def matrix(event, target, preset, modules_arg):
    targets = load_targets()
    presets = load_presets()
    rows = []
    if event == "dispatch":
        preset_name = "custom" if preset == "custom" else preset
        plan = resolve(target, preset_name, modules_arg)
        rows.append({"target": target, "preset": plan["preset_name"], "modules": modules_arg or ""})
    else:
        for name, spec in sorted(targets.items()):
            if spec.get("status", "ready") != "ready":
                continue
            applicable = [
                p["name"]
                for p in presets.values()
                if not p.get("targets")
                or "all" in p["targets"]
                or name in p["targets"]
            ]
            kind = spec.get("kind", "baremetal")
            chosen = []
            if kind == "vm":
                # a VM image only needs the recommended preset; rescue is pointless
                chosen.append("standard")
            elif kind == "arm":
                chosen.append("lite" if "lite" in applicable else "standard")
                chosen.append("rescue")
            else:
                for candidate in ("standard", "full"):
                    if candidate in applicable:
                        chosen.append(candidate)
                chosen.append("rescue")
            for preset_name in chosen:
                rows.append({"target": name, "preset": preset_name, "modules": ""})
    return {"include": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["build", "check", "matrix"])
    ap.add_argument("--target", default="x86-64")
    ap.add_argument("--preset", default="standard")
    ap.add_argument("--modules", default="")
    ap.add_argument("--event", default="dispatch", choices=["tag", "dispatch"])
    ap.add_argument("--format", default="json", choices=["json", "env"])
    ap.add_argument("--out")
    ap.add_argument("--overlay")
    args = ap.parse_args()

    if args.mode == "matrix":
        print(json.dumps(matrix(args.event, args.target, args.preset, args.modules)))
        return

    plan = resolve(args.target, args.preset, args.modules)
    if args.mode == "check":
        print(
            "ok: %s / %s -> %d modules, %d packages, rootfs %s MiB"
            % (
                plan["target_name"],
                plan["preset_name"],
                len(plan["modules"]),
                len(plan["packages"]),
                plan["rootfs_partsize"],
            )
        )
        return

    overlay = args.overlay
    if overlay:
        if os.path.isdir(overlay):
            shutil.rmtree(overlay)
        os.makedirs(overlay, exist_ok=True)
        count = merge_overlay(plan["modules"], load_modules(), overlay)
        sys.stderr.write("overlay: %d files -> %s\n" % (count, overlay))
    if args.format == "env":
        if not args.out:
            fail("--format env requires --out")
        emit_env(plan, args.out, overlay)
    else:
        plan["version"] = None
        print(json.dumps(plan, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
