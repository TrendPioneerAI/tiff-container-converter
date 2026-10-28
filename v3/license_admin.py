from __future__ import annotations

import argparse
import datetime as dt
import json
import queue
import sys
import uuid
from pathlib import Path

from license_core import (
    LicenseError,
    create_license,
    generate_keypair,
    public_key_b64_from_pem,
    save_license,
    verify_license,
)


APP_NAME = "BigTIFF转换工具许可证管理器"
APP_VERSION = "1.0.0"


def run_gui(default_private_key: Path | None = None) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title(f"{APP_NAME} v{APP_VERSION}")
    root.geometry("720x590")
    root.minsize(660, 540)
    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)

    private_var = tk.StringVar(value=str(default_private_key or ""))
    customer_var = tk.StringVar()
    expiry_var = tk.StringVar(value=(dt.date.today() + dt.timedelta(days=365)).isoformat())
    machine_var = tk.StringVar(value="*")
    batch_var = tk.StringVar(value="0")
    note_var = tk.StringVar()
    output_var = tk.StringVar()
    status_var = tk.StringVar(value="授权私钥必须离线保管，绝不能交给客户。")

    ttk.Label(frame, text=APP_NAME, font=("Microsoft YaHei UI", 16, "bold")).grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
    )
    ttk.Label(
        frame,
        text="生成Ed25519离线签名许可证。客户端只包含公钥，无法自行伪造授权。",
    ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 14))

    labels = [
        ("授权私钥：", private_var),
        ("客户/单位：", customer_var),
        ("到期日期：", expiry_var),
        ("绑定设备码：", machine_var),
        ("单批文件上限：", batch_var),
        ("备注：", note_var),
        ("许可证输出：", output_var),
    ]
    for row, (label, variable) in enumerate(labels, start=2):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=8)

    def choose_private() -> None:
        filename = filedialog.askopenfilename(
            title="选择Ed25519授权私钥", filetypes=[("PEM私钥", "*.pem"), ("所有文件", "*.*")]
        )
        if filename:
            private_var.set(filename)

    def choose_output() -> None:
        initial = customer_var.get().strip() or "customer"
        filename = filedialog.asksaveasfilename(
            title="保存许可证",
            defaultextension=".lic",
            initialfile=f"{initial}.lic",
            filetypes=[("许可证", "*.lic"), ("所有文件", "*.*")],
        )
        if filename:
            output_var.set(filename)

    ttk.Button(frame, text="选择…", command=choose_private).grid(row=2, column=2, sticky="ew")
    ttk.Button(frame, text="选择…", command=choose_output).grid(row=8, column=2, sticky="ew")
    ttk.Label(frame, text="使用*表示不绑定设备；0表示单批数量不限。", foreground="#555555").grid(
        row=9, column=0, columnspan=3, sticky="w", pady=(6, 12)
    )

    details = tk.Text(frame, height=9, wrap="word", state="disabled", font=("Consolas", 9))
    details.grid(row=10, column=0, columnspan=3, sticky="nsew")

    def show(text: str) -> None:
        details.configure(state="normal")
        details.delete("1.0", "end")
        details.insert("end", text)
        details.configure(state="disabled")

    def issue() -> None:
        try:
            output_text = output_var.get().strip()
            if not output_text:
                raise LicenseError("请选择许可证输出路径")
            output = Path(output_text)
            envelope = create_license(
                Path(private_var.get().strip()),
                customer_var.get(),
                expiry_var.get().strip(),
                machine_var.get().strip() or "*",
                int(batch_var.get().strip()),
                note_var.get(),
            )
            save_license(envelope, output)
            info = verify_license(output, expected_machine=envelope["payload"]["machine_id"] if envelope["payload"]["machine_id"] != "*" else None)
            show(json.dumps(envelope["payload"], ensure_ascii=False, indent=2))
            status_var.set(f"许可证已生成：{output}")
            messagebox.showinfo(APP_NAME, f"许可证生成成功\n\n客户：{info.customer}\n到期：{info.expires_at}")
        except (LicenseError, ValueError, OSError) as exc:
            messagebox.showerror(APP_NAME, str(exc))

    ttk.Button(frame, text="生成签名许可证", command=issue).grid(row=11, column=2, sticky="e", pady=(12, 0))
    ttk.Label(frame, textvariable=status_var, foreground="#8a4500").grid(
        row=11, column=0, columnspan=2, sticky="w", pady=(12, 0)
    )
    frame.columnconfigure(1, weight=1)
    frame.rowconfigure(10, weight=1)
    root.mainloop()
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--generate-keys", action="store_true", help="生成一对新的Ed25519密钥")
    parser.add_argument("--private-key", help="授权私钥PEM")
    parser.add_argument("--public-key", help="公钥PEM（仅生成密钥时使用）")
    parser.add_argument("--public-key-b64", action="store_true", help="输出客户端内嵌公钥Base64")
    parser.add_argument("--customer", help="客户名称")
    parser.add_argument("--expires", help="到期日期YYYY-MM-DD")
    parser.add_argument("--machine", default="*", help="绑定设备码，*表示不绑定")
    parser.add_argument("--max-files", type=int, default=0, help="单批最大文件数，0表示不限")
    parser.add_argument("--note", default="", help="许可证备注")
    parser.add_argument("-o", "--output", help="许可证输出路径")
    parser.add_argument("--overwrite", action="store_true", help="生成密钥时允许覆盖")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    return parser


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    try:
        if args.generate_keys:
            if not args.private_key or not args.public_key:
                parser.error("--generate-keys需要--private-key和--public-key")
            generate_keypair(Path(args.private_key), Path(args.public_key), args.overwrite)
            print(f"私钥：{Path(args.private_key).resolve()}")
            print(f"公钥：{Path(args.public_key).resolve()}")
            return 0
        if args.public_key_b64:
            if not args.public_key:
                parser.error("--public-key-b64需要--public-key")
            print(public_key_b64_from_pem(Path(args.public_key)))
            return 0
        if args.customer or args.output:
            if not all([args.private_key, args.customer, args.expires, args.output]):
                parser.error("签发许可证需要--private-key、--customer、--expires和--output")
            envelope = create_license(
                Path(args.private_key),
                args.customer,
                args.expires,
                args.machine,
                args.max_files,
                args.note,
            )
            save_license(envelope, Path(args.output))
            print(json.dumps(envelope["payload"], ensure_ascii=False, indent=2))
            return 0
        default_key = Path(sys.executable).resolve().parent / "license_admin_private_key.pem" if getattr(sys, "frozen", False) else None
        return run_gui(default_key if default_key and default_key.exists() else None)
    except (LicenseError, OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
