"""Create a small, credential-free repair bundle for an existing router."""
from pathlib import Path
import io
import tarfile

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "proxy-homeproxy": ["usr/sbin/homeproxy-config-patch", "usr/share/homeproxy/compat-1.14.uc"],
    "proxy-guard": ["usr/sbin/proxy-switch", "usr/sbin/proxy-guard-reconcile",
                    "usr/sbin/proxy-guard-watchdog", "usr/sbin/homeproxy-boot-retry", "etc/init.d/proxy-guard",
                    "etc/hotplug.d/iface/99-proxy-guard"],
}


def add(archive, source, name, executable):
    # Git on Windows can check out executable scripts with CRLF. Archive the
    # actual Linux bytes, not the working tree's platform-dependent newlines:
    # '#!/bin/sh\r\n' makes exec report ENOENT for the interpreter /bin/sh\r.
    data = source.read_bytes().replace(b"\r\n", b"\n")
    info = archive.gettarinfo(str(source), name)
    info.size = len(data)
    info.mode = 0o755 if executable else 0o644
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    archive.addfile(info, io.BytesIO(data))


def build(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        add(archive, ROOT / "scripts/install-homeproxy-fix.sh", "homeproxy-fix/install.sh", True)
        for module, paths in FILES.items():
            for path in paths:
                add(archive, ROOT / "modules" / module / "files" / path,
                    "homeproxy-fix/rootfs/" + path, not path.endswith(".uc"))
    return output


def build_fast_start(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        add(archive, ROOT / "scripts/install-homeproxy-fast-start.sh",
            "homeproxy-fast-start/install.sh", True)
        for path in ("etc/init.d/proxy-guard", "usr/sbin/homeproxy-boot-retry", "usr/sbin/proxy-switch"):
            add(archive, ROOT / "modules/proxy-guard/files" / path,
                "homeproxy-fast-start/rootfs/" + path, True)
    return output


if __name__ == "__main__":
    print(build(ROOT / "dist/homeproxy-fix.tar.gz"))
    print(build_fast_start(ROOT / "dist/homeproxy-fast-start.tar.gz"))
