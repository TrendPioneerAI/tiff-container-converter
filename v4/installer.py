"""Self-contained, per-user folder installer for the V4 portable distribution."""
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
        if not (temporary / "TIFF_Converter_V4.exe").is_file():
            raise ValueError("安装包缺少主程序")
        # Rename a complete folder only; on Windows this fails if target exists.
        os.rename(temporary, destination)
        return destination / "TIFF_Converter_V4.exe"
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

    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("TIFF 双向转换工具 V4 安装")
    root.geometry("560x260")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="TIFF 双向转换工具 V4", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
    ttk.Label(frame, text="无需 Python、无需许可证。选择安装位置后安装。", wraplength=500).pack(anchor="w", pady=(8, 16))
    default = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / "TIFF_Converter_V4"
    location = tk.StringVar(value=str(default))
    line = ttk.Frame(frame)
    line.pack(fill="x")
    ttk.Entry(line, textvariable=location).pack(side="left", fill="x", expand=True)

    def browse():
        folder = filedialog.askdirectory(title="选择安装位置的上级文件夹")
        if folder:
            location.set(str(Path(folder) / "TIFF_Converter_V4"))

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
