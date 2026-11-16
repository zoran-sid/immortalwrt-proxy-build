"""Package the HomeProxy mainland policy importer as LF text only."""
import io
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def build(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for name in ("install.sh", "configure.uc", "cn-mainland.json"):
            source = ROOT / "scripts/routemint" / name
            data = source.read_bytes().replace(b"\r\n", b"\n")
            info = tarfile.TarInfo("routemint-homeproxy/" + name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            archive.addfile(info, io.BytesIO(data))
    return output


if __name__ == "__main__":
    print(build(ROOT / "dist/routemint-homeproxy-cn.tar.gz"))
