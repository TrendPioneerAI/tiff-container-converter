# Build after setting TIFF_INSTALLER_PAYLOAD to the verified payload ZIP.
import os
import hashlib
from pathlib import Path

source = Path(SPECPATH)
payload = os.environ.get("TIFF_INSTALLER_PAYLOAD")
if not payload or not Path(payload).is_file() or Path(payload).name != "payload.zip":
    raise ValueError("TIFF_INSTALLER_PAYLOAD must point to an existing payload.zip")
icon = os.environ.get("TIFF_UI_ICON")
if icon and (not Path(icon).is_file() or hashlib.sha256(Path(icon).read_bytes()).hexdigest() != "0a1a2c4e0bfade62bea3b97086b683be10d9ab203553b2cac5a19ac16db76cdf"):
    raise ValueError("TIFF_UI_ICON must be the approved decorative icon")
resources = [(payload, ".")]
if icon:
    resources.append((icon, "branding"))
a = Analysis(
    [str(source / "installer.py")],
    pathex=[], binaries=[], datas=resources, hiddenimports=[],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
forbidden = {".tif", ".tiff", ".btf", ".tf8", ".png", ".jpg", ".jpeg",
             ".gif", ".bmp", ".webp", ".jp2", ".img", ".ntf", ".vrt"}
a.datas = [item for item in a.datas if Path(item[0]).suffix.lower() not in forbidden]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [], name="TIFF_Converter_V5_Setup",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
    upx_exclude=[], runtime_tmpdir=None, console=False, icon=icon,
)
