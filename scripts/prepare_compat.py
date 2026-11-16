#!/usr/bin/env python3
"""Bake verified upstream compatibility fixes into ImageBuilder's FILES overlay."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[1]


def verified_source(spec, cache):
    if cache.exists() and hashlib.sha256(cache.read_bytes()).hexdigest() == spec["sha256"]:
        return cache.read_text(encoding="utf-8")
    with urllib.request.urlopen(spec["url"], timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != spec["sha256"]:
        raise ValueError("HomeProxy source checksum changed: " + spec["url"])
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(data)
    return data.decode("utf-8")


def patch_generator(source, snippet):
    anchor = "writefile(RUN_DIR + '/sing-box-c.json',"
    if source.count(anchor) != 1:
        raise ValueError("HomeProxy generator layout changed")
    old = ("\t\t\telse if (node === 'urltest')\n"
           "\t\t\t\treturn 'cfg-' + cfg + '-out';\n"
           "\t\t\telse\n\t\t\t\treturn 'cfg-' + node + '-out';")
    if source.count(old) != 1:
        raise ValueError("HomeProxy outbound reference layout changed")
    source = source.replace(old, "\t\t\telse\n\t\t\t\treturn 'cfg-' + cfg + '-out';", 1)
    marker = "} else if (!isEmpty(default_outbound)) {\n\tlet urltest_nodes = [],"
    if source.count(marker) != 1:
        raise ValueError("HomeProxy custom outbound layout changed")
    start = source.index(marker)
    end = source.index("\nif (isEmpty(config.endpoints))", start)
    custom = source[start:end]
    for kind in ("endpoints", "outbounds"):
        line = f"\t\t\t\tconfig.{kind}[length(config.{kind})-1].bind_interface = cfg.bind_interface;"
        if custom.count(line) != 1:
            raise ValueError("HomeProxy custom node layout changed")
        custom = custom.replace(line, f"\t\t\t\tconfig.{kind}[length(config.{kind})-1].tag = 'cfg-' + cfg['.name'] + '-out';\n" + line)
    loop = "for (let i in filter(urltest_nodes, (l) => !~index(routing_nodes, l)))"
    if custom.count(loop) != 1:
        raise ValueError("HomeProxy URLTest member layout changed")
    custom = custom.replace(loop, "for (let i in urltest_nodes)")
    source = source[:start] + custom + source[end:]
    source = source.replace(anchor, snippet.rstrip() + "\n" + anchor, 1)
    return source.replace("\n", "\n// proxy-guard: unique tags per routing node\n", 1)


def patch_init(source):
    if source.count("\nlog() {\n") != 1:
        raise ValueError("HomeProxy log function changed")
    source = source.replace("\nlog() {\n", '\n# proxy-guard: ensure RUN_DIR before logging\nlog() {\n'
                            '\tmkdir -p "$RUN_DIR"\n\tchown sing-box:sing-box "$RUN_DIR"\n', 1)
    source, count = re.subn(r"(^service_stopped\(\) \{.*?)(^\})", r"\1\treturn 0\n\2",
                            source, count=1, flags=re.M | re.S)
    if count != 1:
        raise ValueError("HomeProxy stop hook changed")
    # The verified generator already has a WAN-DNS fallback. Do not bake the
    # old IPv4/wan-name gate which blocks LAN-only and custom WAN setups.
    return source


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def prepare(plan, overlay, cache, version):
    if "proxy-homeproxy" not in plan["modules"]:
        return
    lock = yaml.safe_load((ROOT / "thirdparty/lock.yml").read_text(encoding="utf-8"))["homeproxy-sources"]
    if version != lock["release"]:
        raise ValueError("HomeProxy compatibility must be reviewed for release " + version)
    sources = {name: verified_source(spec, cache / name) for name, spec in lock["files"].items()}
    snippet = (overlay / "usr/share/homeproxy/compat-1.14.uc").read_text(encoding="utf-8")
    write(overlay / "etc/homeproxy/scripts/generate_client.uc", patch_generator(sources["generator"], snippet))
    write(overlay / "etc/init.d/homeproxy", patch_init(sources["init"]))
    provenance = dict(package=lock["package"], package_version=lock["package_version"],
                      sing_box_version=lock["sing_box_version"],
                      sources={k: v["sha256"] for k, v in lock["files"].items()})
    path = overlay / "usr/share/firmware/homeproxy-compat.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o644)
    print("HomeProxy compatibility baked into FILES (verified c84adb9 sources)")


def check_manifest(path):
    lock = yaml.safe_load((ROOT / "thirdparty/lock.yml").read_text(encoding="utf-8"))["homeproxy-sources"]
    packages = dict(line.strip().split(" - ", 1) for line in path.read_text().splitlines() if " - " in line)
    if lock["package"] not in packages:
        return
    if packages[lock["package"]] != lock["package_version"]:
        raise ValueError("HomeProxy package changed; review baked source patch before publishing")
    if packages.get("sing-box", "").split("-r", 1)[0] != lock["sing_box_version"]:
        raise ValueError("sing-box changed; rerun HomeProxy compatibility validation before publishing")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--overlay", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--version", default="25.12.2")
    parser.add_argument("--check-manifest", type=Path)
    args = parser.parse_args()
    if args.check_manifest:
        check_manifest(args.check_manifest)
    else:
        prepare(json.loads(args.plan.read_text()), args.overlay, args.cache, args.version)
