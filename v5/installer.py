"""Self-contained, per-user folder installer for the V5 portable distribution."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


def payload_path():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "payload.zip"


def install_to(destination: Path):
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"安装文件夹已存在：{destination}；请选择其他位置")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"上级文件夹不存在：{destination.parent}")
    payload = payload_path()
    if not payload.is_file():
        raise FileNotFoundError("安装包缺少程序文件")
    temporary = Path(tempfile.mkdtemp(prefix=".tiff-installer-", dir=destination.parent))
    try:
        with zipfile.ZipFile(payload) as archive:
            for item in archive.infolist():
                pure = PurePosixPath(item.filename.replace("\\", "/"))
                if pure.is_absolute() or ".." in pure.parts or ":" in item.filename:
                    raise ValueError("安装包包含不安全的文件路径")
            archive.extractall(temporary)
        if not (temporary / "TIFF_Converter_V5.exe").is_file():
            raise ValueError("安装包缺少主程序")
        # Rename a complete folder only; on Windows this fails if target exists.
        os.rename(temporary, destination)
        return destination / "TIFF_Converter_V5.exe"
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-to")
    args = parser.parse_args()
    if args.install_to:
        install_to(Path(args.install_to))
        return 0

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    icon_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "branding" / "app.ico"
    try:
        if not icon_path.is_file():
            raise FileNotFoundError("无可选图标")
        root.iconbitmap(default=str(icon_path))
    except (OSError, tk.TclError):
        icon = tk.PhotoImage(width=32, height=32)
        icon.put("#173D37", to=(0, 0, 32, 32))
        icon.put("#A9D5BD", to=(6, 8, 26, 12))
        icon.put("#78B89D", to=(8, 14, 24, 18))
        icon.put("#E0EAD9", to=(10, 20, 22, 24))
        root.iconphoto(True, icon)
        root.brand_icon = icon
    root.title("影像格式转换 安装")
    dpi = 96
    if sys.platform == "win32":
        try:
            dpi = ctypes.windll.user32.GetDpiForWindow(root.winfo_id()) or 96
        except (AttributeError, OSError):
            pass
    scale = max(1.0, min(dpi / 96.0, 2.5))
    root.tk.call("tk", "scaling", dpi / 72.0)
    root.geometry(f"{round(560*scale)}x{round(260*scale)}")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="影像格式转换", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
    ttk.Label(frame, text="无需 Python、无需许可证。选择安装位置后安装。", wraplength=500).pack(anchor="w", pady=(8, 16))
    default = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / "TIFF_Converter_V5"
    location = tk.StringVar(value=str(default))
    line = ttk.Frame(frame)
    line.pack(fill="x")
    ttk.Entry(line, textvariable=location).pack(side="left", fill="x", expand=True)

    def browse():
        folder = filedialog.askdirectory(title="选择安装位置的上级文件夹")
        if folder:
            location.set(str(Path(folder) / "TIFF_Converter_V5"))

    def install():
        try:
            executable = install_to(Path(location.get().strip()))
        except Exception as exc:
            messagebox.showerror("安装失败", str(exc), parent=root)
            return
        if messagebox.askyesno("安装完成", f"安装完成：\n{executable}\n\n现在打开程序？", parent=root):
            os.startfile(str(executable))
        root.destroy()

    ttk.Button(line, text="选择", command=browse).pack(side="left", padx=(8, 0))
    ttk.Button(frame, text="安装", command=install).pack(anchor="e", pady=(20, 0))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
