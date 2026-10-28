"""Create an application payload without any raster image files."""
import argparse
import zipfile
from pathlib import Path

FORBIDDEN = {".tif", ".tiff", ".btf", ".tf8", ".png", ".jpg", ".jpeg",
             ".gif", ".bmp", ".webp", ".jp2", ".img", ".ntf", ".vrt"}

parser = argparse.ArgumentParser()
parser.add_argument("dist", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
dist = args.dist.resolve()
output = args.output.resolve()
if not (dist / "TIFF_Converter_V4.exe").is_file():
    parser.error("distribution must contain TIFF_Converter_V4.exe")
if output.exists():
    parser.error("refusing to replace an existing payload")
if output.is_relative_to(dist):
    parser.error("payload must be outside the distribution")
output.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for file in sorted(dist.rglob("*")):
        if file.is_file() and file.suffix.lower() not in FORBIDDEN:
            archive.write(file, file.relative_to(dist).as_posix())
    archive.write(Path(__file__).with_name("使用说明.md"), "使用说明.md")
with zipfile.ZipFile(output) as archive:
    if archive.testzip():
        raise RuntimeError("payload CRC check failed")
    if any(Path(name).suffix.lower() in FORBIDDEN for name in archive.namelist()):
        raise RuntimeError("payload contains an image file")
print(f"payload={output}, files={len(archive.namelist())}")
