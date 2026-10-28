"""Generate a small synthetic BigTIFF and test each historic converter."""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make_bigtiff(path: Path) -> None:
    # A 2x2 uncompressed grayscale image, encoded without external fixtures.
    entries = [
        (256, 4, 1, 2), (257, 4, 1, 2), (258, 3, 1, 8),
        (259, 3, 1, 1), (262, 3, 1, 1), (273, 16, 1, 240),
        (277, 3, 1, 1), (278, 4, 1, 2), (279, 4, 1, 4),
        (284, 3, 1, 1),
    ]
    data = bytearray(b"II" + struct.pack("<HHHQ", 43, 8, 0, 16))
    data += struct.pack("<Q", len(entries))
    for tag, kind, count, value in entries:
        data += struct.pack("<HHQ", tag, kind, count)
        data += struct.pack("<Q", value) if kind in (4, 16) else struct.pack("<H", value) + b"\0" * 6
    data += struct.pack("<Q", 0)
    assert len(data) == 232
    data += b"\0" * 8 + b"\x00\x40\x80\xff"
    path.write_bytes(data)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        source = folder / "synthetic-big.tif"
        make_bigtiff(source)
        for version in ("v1", "v2", "v3"):
            module = "BigTIFF_to_ClassicTIFF" if version == "v1" else "bigtiff_to_classic"
            dest = folder / f"{version}-classic.tif"
            script = (
                "import sys; from pathlib import Path; "
                f"import {module} as app; "
                "r=app.convert(Path(sys.argv[1]),Path(sys.argv[2]),None); "
                "assert r['status']=='PASS'; "
                "assert r['validation']['raw_image_payload_identical']; "
                "assert r['validation']['non_structural_metadata_identical']"
            )
            subprocess.run([sys.executable, "-c", script, str(source), str(dest)],
                           cwd=ROOT / version, check=True, env={**os.environ, "PYTHONUTF8": "1"})
            assert dest.read_bytes()[:4] == b"II*\0"
            print(f"{version}: PASS")


if __name__ == "__main__":
    main()
