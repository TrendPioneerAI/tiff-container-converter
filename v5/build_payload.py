"""Create a payload with an optional, verified decorative illustration."""
import argparse
import hashlib
import zipfile
from pathlib import Path

FORBIDDEN = {".tif", ".tiff", ".btf", ".tf8", ".png", ".jpg", ".jpeg",
             ".gif", ".bmp", ".webp", ".jp2", ".img", ".ntf", ".vrt", ".ico"}

parser = argparse.ArgumentParser()
parser.add_argument("dist", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--art", type=Path, help="Optional verified abstract illustration")
parser.add_argument("--icon", type=Path, help="Optional verified application icon")
args = parser.parse_args()
dist = args.dist.resolve()
output = args.output.resolve()
art = args.art.resolve() if args.art else None
icon = args.icon.resolve() if args.icon else None
art_name = "branding/layers-art.png"
icon_name = "branding/app.ico"
art_sha256 = "91ea2639ab9a03bbd25b3502f913636bedd240ca87041aa1cce685beede8555a"
icon_sha256 = "0a1a2c4e0bfade62bea3b97086b683be10d9ab203553b2cac5a19ac16db76cdf"
if art and (not art.is_file() or hashlib.sha256(art.read_bytes()).hexdigest() != art_sha256):
    parser.error("illustration does not match the approved decorative asset")
if icon and (not icon.is_file() or hashlib.sha256(icon.read_bytes()).hexdigest() != icon_sha256):
    parser.error("icon does not match the approved decorative asset")
if not (dist / "TIFF_Converter_V5.exe").is_file():
    parser.error("distribution must contain TIFF_Converter_V5.exe")
if output.exists():
    parser.error("refusing to replace an existing payload")
if output.is_relative_to(dist):
    parser.error("payload must be outside the distribution")
output.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for file in sorted(dist.rglob("*")):
        if file.is_file() and file.suffix.lower() not in FORBIDDEN:
            archive.write(file, file.relative_to(dist).as_posix())
    if art:
        archive.write(art, art_name)
    if icon:
        archive.write(icon, icon_name)
    archive.write(Path(__file__).with_name("使用说明.md"), "使用说明.md")
with zipfile.ZipFile(output) as archive:
    if archive.testzip():
        raise RuntimeError("payload CRC check failed")
    if any(Path(name).suffix.lower() in FORBIDDEN and name not in (art_name, icon_name) for name in archive.namelist()):
        raise RuntimeError("payload contains an image file")
    if art and hashlib.sha256(archive.read(art_name)).hexdigest() != art_sha256:
        raise RuntimeError("decorative illustration hash mismatch")
    if icon and hashlib.sha256(archive.read(icon_name)).hexdigest() != icon_sha256:
        raise RuntimeError("decorative icon hash mismatch")
print(f"payload={output}, files={len(archive.namelist())}")
