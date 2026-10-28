# Build after setting TIFF_INSTALLER_PAYLOAD to the image-free payload ZIP.
import os
from pathlib import Path

source = Path(SPECPATH)
payload = os.environ.get("TIFF_INSTALLER_PAYLOAD")
if not payload or not Path(payload).is_file() or Path(payload).name != "payload.zip":
    raise ValueError("TIFF_INSTALLER_PAYLOAD must point to an existing payload.zip")
a = Analysis(
    [str(source / "installer.py")],
    pathex=[], binaries=[], datas=[(payload, ".")], hiddenimports=[],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
forbidden = {".tif", ".tiff", ".btf", ".tf8", ".png", ".jpg", ".jpeg",
             ".gif", ".bmp", ".webp", ".jp2", ".img", ".ntf", ".vrt"}
a.datas = [item for item in a.datas if Path(item[0]).suffix.lower() not in forbidden]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [], name="TIFF_Converter_V4_Setup",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
    upx_exclude=[], runtime_tmpdir=None, console=False,
)
