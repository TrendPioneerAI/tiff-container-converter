from __future__ import annotations

import os
import queue
import threading
from pathlib import Path
from typing import Iterable


def run_gui_app(engine) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    BG = "#F7F7F8"
    CARD = "#FFFFFF"
    SIDEBAR = "#202123"
    SIDEBAR_MUTED = "#AEB4BC"
    TEXT = "#171717"
    MUTED = "#6B7280"
    BORDER = "#E5E7EB"
    ACCENT = "#10A37F"
    ACCENT_HOVER = "#0D8C6D"
    DANGER = "#B42318"
    WARNING_BG = "#FFF7ED"

    smoke_test = os.environ.get("BTFC_UI_SMOKE_TEST") == "1"
    root = tk.Tk()
    if smoke_test:
        root.withdraw()
    root.title(f"{engine.APP_NAME} v{engine.APP_VERSION}")
    root.geometry("1120x780")
    root.minsize(980, 680)
    root.configure(bg=BG)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("App.TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("Sidebar.TFrame", background=SIDEBAR)
    style.configure("TLabel", background=BG, foreground=TEXT, font=("Microsoft YaHei UI", 9))
    style.configure("Card.TLabel", background=CARD, foreground=TEXT, font=("Microsoft YaHei UI", 9))
    style.configure("Muted.Card.TLabel", background=CARD, foreground=MUTED, font=("Microsoft YaHei UI", 8))
    style.configure(
        "Primary.TButton",
        background=ACCENT,
        foreground="#FFFFFF",
        borderwidth=0,
        focusthickness=0,
        padding=(18, 10),
        font=("Microsoft YaHei UI", 9, "bold"),
    )
    style.map("Primary.TButton", background=[("active", ACCENT_HOVER), ("disabled", "#B7DDD3")])
    style.configure(
        "Secondary.TButton",
        background=CARD,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        padding=(12, 8),
        font=("Microsoft YaHei UI", 8),
    )
    style.map("Secondary.TButton", background=[("active", "#F2F4F7")])
    style.configure(
        "Sidebar.TButton",
        background=SIDEBAR,
        foreground="#FFFFFF",
        borderwidth=0,
        padding=(10, 8),
        font=("Microsoft YaHei UI", 8),
    )
    style.map("Sidebar.TButton", background=[("active", "#343541")])
    style.configure("TCheckbutton", background=CARD, foreground=TEXT, font=("Microsoft YaHei UI", 8))
    style.map("TCheckbutton", background=[("active", CARD)])
    style.configure(
        "Treeview",
        background=CARD,
        fieldbackground=CARD,
        foreground=TEXT,
        rowheight=30,
        borderwidth=0,
        font=("Microsoft YaHei UI", 8),
    )
    style.configure(
        "Treeview.Heading",
        background="#F2F4F7",
        foreground=MUTED,
        borderwidth=0,
        padding=(8, 8),
        font=("Microsoft YaHei UI", 8, "bold"),
    )
    style.map("Treeview", background=[("selected", "#DDF4EC")], foreground=[("selected", TEXT)])
    style.configure("Clean.Horizontal.TProgressbar", background=ACCENT, troughcolor="#E9ECEF", borderwidth=0)
    style.configure("Clean.TEntry", fieldbackground="#FFFFFF", bordercolor=BORDER, padding=7)

    selected_files: dict[str, Path] = {}
    event_queue: queue.Queue[tuple] = queue.Queue()
    running = False
    last_report: Path | None = None
    recursive_var = tk.BooleanVar(value=True)
    replace_original_var = tk.BooleanVar(value=False)
    overwrite_var = tk.BooleanVar(value=False)
    output_var = tk.StringVar()
    file_count_var = tk.StringVar(value="0 个文件")
    status_var = tk.StringVar(value="添加普通 TIFF 文件或文件夹以开始")
    direction_var = tk.StringVar(value="普通 TIFF → BigTIFF")
    option_hint_var = tk.StringVar(value="转换结果保存到指定文件夹，源文件保持不变。")

    shell = ttk.Frame(root, style="App.TFrame")
    shell.pack(fill="both", expand=True)
    shell.columnconfigure(1, weight=1)
    shell.rowconfigure(0, weight=1)

    sidebar = ttk.Frame(shell, style="Sidebar.TFrame", width=225)
    sidebar.grid(row=0, column=0, sticky="nsew")
    sidebar.grid_propagate(False)
    sidebar.columnconfigure(0, weight=1)
    sidebar.rowconfigure(8, weight=1)

    tk.Label(
        sidebar,
        text="TIFF",
        bg=ACCENT,
        fg="#FFFFFF",
        font=("Segoe UI", 13, "bold"),
        width=5,
        height=2,
    ).grid(row=0, column=0, sticky="w", padx=22, pady=(24, 10))
    tk.Label(
        sidebar,
        text="TIFF Converter",
        bg=SIDEBAR,
        fg="#FFFFFF",
        font=("Microsoft YaHei UI", 12, "bold"),
        anchor="w",
    ).grid(row=1, column=0, sticky="ew", padx=22)
    tk.Label(
        sidebar,
        text=f"双向转换 · v{engine.APP_VERSION}",
        bg=SIDEBAR,
        fg=SIDEBAR_MUTED,
        font=("Microsoft YaHei UI", 8),
        anchor="w",
    ).grid(row=2, column=0, sticky="ew", padx=22, pady=(3, 24))

    tk.Label(
        sidebar,
        text="  转换任务",
        bg="#343541",
        fg="#FFFFFF",
        font=("Microsoft YaHei UI", 9, "bold"),
        anchor="w",
        padx=12,
        pady=10,
    ).grid(row=3, column=0, sticky="ew", padx=12)
    tk.Label(
        sidebar,
        text="使用方式",
        bg=SIDEBAR,
        fg=SIDEBAR_MUTED,
        font=("Microsoft YaHei UI", 8),
        anchor="w",
    ).grid(row=4, column=0, sticky="ew", padx=22, pady=(24, 4))
    license_title = tk.Label(
        sidebar,
        text="本地离线 · 免授权",
        bg=SIDEBAR,
        fg="#FFFFFF",
        font=("Microsoft YaHei UI", 8, "bold"),
        anchor="w",
        justify="left",
        wraplength=180,
    )
    license_title.grid(row=5, column=0, sticky="ew", padx=22)
    tk.Label(
        sidebar,
        text="图像块直接复制\n每次批量生成一份 PDF",
        bg=SIDEBAR,
        fg=SIDEBAR_MUTED,
        font=("Microsoft YaHei UI", 7),
        anchor="w",
        justify="left",
        wraplength=180,
    ).grid(row=6, column=0, sticky="ew", padx=22, pady=(3, 10))

    sidebar_buttons = ttk.Frame(sidebar, style="Sidebar.TFrame")
    sidebar_buttons.grid(row=7, column=0, sticky="ew", padx=12)
    sidebar_buttons.columnconfigure(0, weight=1)

    main = ttk.Frame(shell, style="App.TFrame", padding=(28, 22, 28, 18))
    main.grid(row=0, column=1, sticky="nsew")
    main.columnconfigure(0, weight=1)
    main.rowconfigure(2, weight=3)
    main.rowconfigure(5, weight=1)

    header = ttk.Frame(main, style="App.TFrame")
    header.grid(row=0, column=0, sticky="ew", pady=(0, 16))
    header.columnconfigure(0, weight=1)
    tk.Label(
        header,
        text="转换任务",
        bg=BG,
        fg=TEXT,
        font=("Microsoft YaHei UI", 20, "bold"),
        anchor="w",
    ).grid(row=0, column=0, sticky="w")
    tk.Label(
        header,
        text="复制图像块并校验，支持 TIFF 与 BigTIFF 双向转换",
        bg=BG,
        fg=MUTED,
        font=("Microsoft YaHei UI", 9),
        anchor="w",
    ).grid(row=1, column=0, sticky="w", pady=(3, 0))
    count_badge = tk.Label(
        header,
        textvariable=file_count_var,
        bg="#E7F6F1",
        fg="#067647",
        font=("Microsoft YaHei UI", 8, "bold"),
        padx=12,
        pady=6,
    )
    count_badge.grid(row=0, column=1, rowspan=2, sticky="e")

    toolbar_card = ttk.Frame(main, style="Card.TFrame", padding=14)
    toolbar_card.grid(row=1, column=0, sticky="ew", pady=(0, 10))
    toolbar_card.columnconfigure(5, weight=1)
    direction_box = ttk.Combobox(toolbar_card, textvariable=direction_var, state="readonly",
                                 values=("普通 TIFF → BigTIFF", "BigTIFF → 普通 TIFF"), width=24)
    direction_box.grid(row=1, column=0, columnspan=3, sticky="w", pady=(12, 0))

    tree_card = ttk.Frame(main, style="Card.TFrame", padding=(1, 1, 1, 1))
    tree_card.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
    tree_card.columnconfigure(0, weight=1)
    tree_card.rowconfigure(0, weight=1)
    tree = ttk.Treeview(
        tree_card,
        columns=("name", "folder", "status"),
        show="headings",
        selectmode="extended",
    )
    tree.heading("name", text="文件名")
    tree.heading("folder", text="来源位置")
    tree.heading("status", text="状态")
    tree.column("name", width=290, anchor="w")
    tree.column("folder", width=510, anchor="w")
    tree.column("status", width=95, anchor="center")
    tree_scroll = ttk.Scrollbar(tree_card, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=tree_scroll.set)
    tree.grid(row=0, column=0, sticky="nsew")
    tree_scroll.grid(row=0, column=1, sticky="ns")

    options_card = ttk.Frame(main, style="Card.TFrame", padding=16)
    options_card.grid(row=3, column=0, sticky="ew", pady=(0, 10))
    options_card.columnconfigure(1, weight=1)
    tk.Label(
        options_card,
        text="输出设置",
        bg=CARD,
        fg=TEXT,
        font=("Microsoft YaHei UI", 10, "bold"),
    ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))
    output_label = tk.Label(
        options_card,
        text="输出与PDF报告文件夹",
        bg=CARD,
        fg=MUTED,
        font=("Microsoft YaHei UI", 8),
    )
    output_label.grid(row=1, column=0, sticky="w")
    output_entry = ttk.Entry(options_card, textvariable=output_var, style="Clean.TEntry")
    output_entry.grid(row=1, column=1, sticky="ew", padx=(12, 8))

    progress_card = ttk.Frame(main, style="Card.TFrame", padding=16)
    progress_card.grid(row=4, column=0, sticky="ew", pady=(0, 10))
    progress_card.columnconfigure(0, weight=1)
    progress_card.columnconfigure(1, weight=0)
    tk.Label(
        progress_card,
        textvariable=status_var,
        bg=CARD,
        fg=TEXT,
        font=("Microsoft YaHei UI", 9, "bold"),
        anchor="w",
    ).grid(row=0, column=0, sticky="w")
    current_progress = ttk.Progressbar(
        progress_card, style="Clean.Horizontal.TProgressbar", maximum=100
    )
    overall_progress = ttk.Progressbar(
        progress_card, style="Clean.Horizontal.TProgressbar", maximum=100
    )
    current_progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 4))
    overall_progress.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(3, 8))

    log_card = ttk.Frame(main, style="Card.TFrame", padding=12)
    log_card.grid(row=5, column=0, sticky="nsew", pady=(0, 10))
    log_card.columnconfigure(0, weight=1)
    log_card.rowconfigure(1, weight=1)
    tk.Label(
        log_card,
        text="运行详情",
        bg=CARD,
        fg=MUTED,
        font=("Microsoft YaHei UI", 8, "bold"),
    ).grid(row=0, column=0, sticky="w", pady=(0, 6))
    log = tk.Text(
        log_card,
        height=4,
        wrap="word",
        relief="flat",
        borderwidth=0,
        bg=CARD,
        fg=MUTED,
        insertbackground=TEXT,
        font=("Microsoft YaHei UI", 8),
        state="disabled",
    )
    log.grid(row=1, column=0, sticky="nsew")

    footer = ttk.Frame(main, style="App.TFrame")
    footer.grid(row=6, column=0, sticky="ew")
    footer.columnconfigure(0, weight=1)
    tk.Label(
        footer,
        text="Classic TIFF上限4 GiB · 异常文件自动记录并继续下一项",
        bg=BG,
        fg=MUTED,
        font=("Microsoft YaHei UI", 8),
    ).grid(row=0, column=0, sticky="w")

    def append_log(text: str) -> None:
        log.configure(state="normal")
        log.insert("end", text + "\n")
        log.see("end")
        log.configure(state="disabled")

    def update_controls() -> None:
        file_count_var.set(f"{len(selected_files)} 个文件")
        enabled = bool(selected_files) and bool(output_var.get().strip()) and not running
        start_button.configure(state="normal" if enabled else "disabled")
        direction_box.configure(state="disabled" if running else "readonly")
        report_button.configure(state="normal" if last_report and last_report.exists() else "disabled")

    def add_paths(paths: Iterable[str | Path]) -> int:
        added = 0
        for raw_path in paths:
            path = Path(raw_path).resolve()
            if (
                path.suffix.lower() not in engine.SUPPORTED_EXTENSIONS
                or not path.is_file()
                or engine.tiff_kind(path) != ("classic" if direction_var.get().startswith("普通") else "big")
            ):
                continue
            key = str(path).lower()
            if key in selected_files:
                continue
            selected_files[key] = path
            tree.insert("", "end", iid=key, values=(path.name, str(path.parent), "等待"))
            added += 1
        if added and not output_var.get():
            first = next(iter(selected_files.values()))
            output_var.set(str(first.parent / "TIFF_Output"))
        status_var.set(f"已添加 {added} 个文件，共 {len(selected_files)} 个")
        update_controls()
        return added

    def choose_files() -> None:
        filenames = filedialog.askopenfilenames(
            title="选择与转换方向匹配的 TIFF 文件",
            filetypes=[("BigTIFF / TIFF", "*.tif *.tiff *.btf *.tf8"), ("所有文件", "*.*")],
        )
        if filenames:
            add_paths(filenames)

    def choose_folder() -> None:
        folder = filedialog.askdirectory(title="选择包含 TIFF 的文件夹")
        if not folder:
            return
        try:
            direction = "to_big" if direction_var.get().startswith("普通") else "to_classic"
            paths = engine.scan_image_folder(Path(folder), recursive_var.get(), direction)
            added = add_paths(paths)
            output_var.set(str(Path(folder).resolve() / "TIFF_Output"))
            status_var.set(f"文件夹扫描完成：发现 {len(paths)} 个符合方向的 TIFF，新增 {added} 个")
        except Exception as exc:
            messagebox.showerror(engine.APP_NAME, str(exc))

    def remove_selected() -> None:
        for iid in tree.selection():
            selected_files.pop(iid, None)
            tree.delete(iid)
        status_var.set(f"当前任务包含 {len(selected_files)} 个文件")
        update_controls()

    def clear_all() -> None:
        selected_files.clear()
        for iid in tree.get_children():
            tree.delete(iid)
        status_var.set("任务列表已清空")
        update_controls()

    def choose_output() -> None:
        folder = filedialog.askdirectory(title="选择转换结果与PDF报告保存文件夹")
        if folder:
            output_var.set(folder)

    def toggle_replace_mode() -> None:
        if replace_original_var.get():
            option_hint_var.set("验证后同名替换，并保留同目录原文件备份；PDF保存到上方文件夹。")
            hint_label.configure(bg=WARNING_BG, fg=DANGER)
            overwrite_check.configure(state="disabled")
        else:
            option_hint_var.set("转换结果保存到指定文件夹，源文件保持不变。")
            hint_label.configure(bg=CARD, fg=MUTED)
            overwrite_check.configure(state="normal")

    def open_last_report() -> None:
        if last_report and last_report.exists():
            os.startfile(str(last_report))

    def worker(inputs, output_dir, overwrite, replace_original, direction) -> None:
        try:
            engine.convert_batch(
                inputs,
                output_dir,
                direction=direction,
                overwrite=overwrite,
                replace_original=replace_original,
                input_scope="多文件/文件夹选择",
                event_callback=event_queue.put,
            )
        except Exception as exc:
            event_queue.put(("fatal_error", str(exc)))

    def start_conversion() -> None:
        nonlocal running
        if not selected_files:
            messagebox.showerror(engine.APP_NAME, "请至少添加一个符合方向的 TIFF 文件。")
            return
        if not output_var.get().strip():
            messagebox.showerror(engine.APP_NAME, "请选择PDF报告和输出保存文件夹。")
            return
        if replace_original_var.get():
            confirmed = messagebox.askyesno(
                engine.APP_NAME,
                "确认原位替换？\n\n验证通过后替换源文件，并在原目录保留原文件备份。"
                "操作完成后生成一份PDF报告。",
                icon="warning",
            )
            if not confirmed:
                return
        inputs = list(selected_files.values())
        for key in selected_files:
            if tree.exists(key):
                values = list(tree.item(key, "values"))
                values[2] = "等待"
                tree.item(key, values=values)
        running = True
        current_progress["value"] = 0
        overall_progress["value"] = 0
        status_var.set(f"批次开始，共 {len(inputs)} 个文件")
        append_log(f"开始：{'原位替换' if replace_original_var.get() else '生成副本'}")
        update_controls()
        threading.Thread(
            target=worker,
            args=(
                inputs,
                Path(output_var.get().strip()),
                overwrite_var.get(),
                replace_original_var.get(),
                "to_big" if direction_var.get().startswith("普通") else "to_classic",
            ),
            daemon=True,
        ).start()

    ttk.Button(toolbar_card, text="添加文件", style="Secondary.TButton", command=choose_files).grid(row=0, column=0, padx=(0, 7))
    ttk.Button(toolbar_card, text="添加文件夹", style="Secondary.TButton", command=choose_folder).grid(row=0, column=1, padx=7)
    ttk.Button(toolbar_card, text="移除选中", style="Secondary.TButton", command=remove_selected).grid(row=0, column=2, padx=7)
    ttk.Button(toolbar_card, text="清空", style="Secondary.TButton", command=clear_all).grid(row=0, column=3, padx=7)
    ttk.Checkbutton(toolbar_card, text="递归扫描子文件夹", variable=recursive_var).grid(row=0, column=4, padx=(18, 0))
    ttk.Button(options_card, text="选择", style="Secondary.TButton", command=choose_output).grid(row=1, column=2, padx=(0, 8))
    replace_check = ttk.Checkbutton(
        options_card,
        text="同名无损替换原文件",
        variable=replace_original_var,
        command=toggle_replace_mode,
    )
    replace_check.grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 4))
    overwrite_check = ttk.Checkbutton(options_card, text="覆盖已存在的转换副本", variable=overwrite_var)
    overwrite_check.grid(row=2, column=2, columnspan=2, sticky="w", pady=(12, 4))
    hint_label = tk.Label(
        options_card,
        textvariable=option_hint_var,
        bg=CARD,
        fg=MUTED,
        font=("Microsoft YaHei UI", 8),
        anchor="w",
        justify="left",
        padx=8,
        pady=7,
    )
    hint_label.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(6, 0))

    tk.Label(
        sidebar,
        text="本地离线运行\n无联网 · 无遥测",
        bg=SIDEBAR,
        fg=SIDEBAR_MUTED,
        font=("Microsoft YaHei UI", 7),
        justify="left",
        anchor="sw",
    ).grid(row=9, column=0, sticky="sw", padx=22, pady=20)

    report_button = ttk.Button(footer, text="打开PDF报告", style="Secondary.TButton", command=open_last_report, state="disabled")
    report_button.grid(row=0, column=1, padx=(12, 8))
    start_button = ttk.Button(footer, text="开始转换", style="Primary.TButton", command=start_conversion, state="disabled")
    start_button.grid(row=0, column=2)

    def poll_events() -> None:
        nonlocal running, last_report
        try:
            while True:
                event = event_queue.get_nowait()
                if event[0] == "file_start":
                    _, key, index, total_files, name = event
                    if tree.exists(key):
                        values = list(tree.item(key, "values"))
                        values[2] = "处理中"
                        tree.item(key, values=values)
                        tree.see(key)
                    status_var.set(f"正在处理 {index + 1}/{total_files} · {name}")
                elif event[0] == "progress":
                    _, index, file_count, phase, current, total, message = event
                    fraction = current / total if total else 0
                    current_progress["value"] = fraction * 100
                    overall_progress["value"] = ((index + fraction) / file_count) * 100
                    status_var.set(message)
                    if phase in ("inspect", "verify") or current == total:
                        append_log(message)
                elif event[0] == "file_done":
                    _, key, result, detail = event
                    if tree.exists(key):
                        values = list(tree.item(key, "values"))
                        values[2] = "成功" if result == "PASS" else "失败"
                        tree.item(key, values=values)
                    append_log(f"{'成功' if result == 'PASS' else '失败'} · {detail}")
                elif event[0] == "batch_done":
                    _, summary, summary_path, pdf_info = event
                    running = False
                    last_report = Path(summary_path)
                    current_progress["value"] = 100
                    overall_progress["value"] = 100
                    status_var.set(f"批次完成 · 成功 {summary['passed']} · 失败 {summary['failed']}")
                    append_log(f"PDF报告 · {summary_path} · {pdf_info['pages']}页")
                    update_controls()
                    messagebox.showinfo(
                        engine.APP_NAME,
                        f"批次完成\n\n成功：{summary['passed']}\n失败：{summary['failed']}\nPDF报告：{summary_path}",
                    )
                elif event[0] == "fatal_error":
                    running = False
                    status_var.set("任务发生异常，请查看运行详情")
                    append_log("任务异常 · " + event[1])
                    update_controls()
                    messagebox.showerror(engine.APP_NAME, event[1])
        except queue.Empty:
            pass
        root.after(120, poll_events)

    def on_close() -> None:
        if running and not messagebox.askyesno(
            engine.APP_NAME,
            "转换仍在进行。强制关闭不会替换尚未验证的原文件，但可能留下临时文件。确认关闭？",
            icon="warning",
        ):
            return
        root.destroy()

    output_var.trace_add("write", lambda *_: update_controls())
    direction_box.bind("<<ComboboxSelected>>", lambda event: clear_all())
    root.protocol("WM_DELETE_WINDOW", on_close)
    toggle_replace_mode()
    update_controls()
    root.after(120, poll_events)
    if smoke_test:
        root.after(250, root.destroy)
    root.mainloop()
    return 0
