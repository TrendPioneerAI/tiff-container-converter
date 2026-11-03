from __future__ import annotations

import os
import queue
import sys
import threading
from pathlib import Path
from typing import Iterable


def run_gui_app(engine) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    BG = "#F4F6F2"
    CARD = "#FFFFFF"
    SIDEBAR = "#173D37"
    SIDEBAR_MUTED = "#B8CCC2"
    TEXT = "#20352D"
    MUTED = "#718178"
    BORDER = "#DFE7DF"
    ACCENT = "#246C56"
    ACCENT_HOVER = "#185542"
    DANGER = "#B42318"
    WARNING_BG = "#FFF7ED"

    smoke_test = os.environ.get("BTFC_UI_SMOKE_TEST") == "1"
    root = tk.Tk()
    dpi = 96
    if sys.platform == "win32":
        try:
            import ctypes
            dpi = ctypes.windll.user32.GetDpiForWindow(root.winfo_id()) or 96
        except (AttributeError, OSError):
            pass
    # Keep vector-rendered text sharp while fitting the interface on smaller screens.
    native_scale = min(dpi / 96.0, 2.5)
    dpi_scale = min(native_scale, (root.winfo_screenwidth() - 20) / 1120,
                    (root.winfo_screenheight() - 100) / 780)
    dpi_scale = max(0.75, dpi_scale)
    root.tk.call("tk", "scaling", dpi_scale * 96.0 / 72.0)
    if smoke_test:
        root.withdraw()
    root.title(f"{engine.APP_NAME} v{engine.APP_VERSION}")
    window_width = min(round(1120*native_scale), root.winfo_screenwidth() - 20)
    window_height = min(round(780*native_scale), root.winfo_screenheight() - 70)
    root.geometry(f"{window_width}x{window_height}")
    root.minsize(min(round(980*dpi_scale), window_width), min(round(680*dpi_scale), window_height))
    root.configure(bg=BG)
    branding = Path(sys.executable).resolve().parent / "branding" if getattr(sys, "frozen", False) else None
    icon_path = branding / "app.ico" if branding else Path(os.environ.get("TIFF_UI_ICON", ""))
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
    style.configure("Mode.TRadiobutton", background="#F0F5F0", foreground=TEXT,
                    font=("Microsoft YaHei UI", 9, "bold"), padding=(16, 10),
                    relief="flat", indicatoron=False)
    style.map("Mode.TRadiobutton", background=[("selected", "#D9EAE0"), ("active", "#E7F1E9"), ("disabled", "#F2F3F1")],
              foreground=[("selected", "#185542"), ("disabled", "#A5AEA8")])
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
        rowheight=round(30*dpi_scale),
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
    direction_var = tk.StringVar(value="to_classic_tiled")
    direction_hint_var = tk.StringVar(value="选择 YS 参考图，转换 TM 影像。")
    nodata_zero_var = tk.BooleanVar(value=True)
    reference_var = tk.StringVar()
    option_hint_var = tk.StringVar(value="转换结果保存到指定文件夹，源文件保持不变。")

    shell = ttk.Frame(root, style="App.TFrame")
    shell.pack(fill="both", expand=True)
    shell.columnconfigure(1, weight=1)
    shell.rowconfigure(0, weight=1)

    sidebar = ttk.Frame(shell, style="Sidebar.TFrame", width=round(225*dpi_scale))
    sidebar.grid(row=0, column=0, sticky="nsew")
    sidebar.grid_propagate(False)
    sidebar.columnconfigure(0, weight=1)
    sidebar.rowconfigure(8, weight=1)

    tk.Label(
        sidebar,
        text="T",
        bg=ACCENT,
        fg="#FFFFFF",
        font=("Segoe UI", 13, "bold"),
        width=3,
        height=1,
    ).grid(row=0, column=0, sticky="w", padx=22, pady=(24, 14))
    tk.Label(
        sidebar,
        text="影像格式转换",
        bg=SIDEBAR,
        fg="#FFFFFF",
        font=("Microsoft YaHei UI", 12, "bold"),
        anchor="w",
    ).grid(row=1, column=0, sticky="ew", padx=22)
    tk.Label(
        sidebar,
        text=f"TIFF  /  BIGTIFF    ·    {engine.APP_VERSION}",
        bg=SIDEBAR,
        fg=SIDEBAR_MUTED,
        font=("Microsoft YaHei UI", 8),
        anchor="w",
    ).grid(row=2, column=0, sticky="ew", padx=22, pady=(3, 24))

    tk.Label(
        sidebar,
        text="  转换任务",
        bg="#285448",
        fg="#FFFFFF",
        font=("Microsoft YaHei UI", 9, "bold"),
        anchor="w",
        padx=12,
        pady=10,
    ).grid(row=3, column=0, sticky="ew", padx=12)
    tk.Label(
        sidebar,
        text="影像处理",
        bg=SIDEBAR,
        fg=SIDEBAR_MUTED,
        font=("Microsoft YaHei UI", 8),
        anchor="w",
    ).grid(row=4, column=0, sticky="ew", padx=22, pady=(24, 4))
    license_title = tk.Label(
        sidebar,
        text="本地处理 · 自动校验",
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
        text="无需联网",
        bg=SIDEBAR,
        fg=SIDEBAR_MUTED,
        font=("Microsoft YaHei UI", 7),
        anchor="w",
        justify="left",
        wraplength=180,
    ).grid(row=6, column=0, sticky="ew", padx=22, pady=(3, 10))

    art_size = (round(190*dpi_scale), round(200*dpi_scale))
    art_holder = tk.Frame(sidebar, bg=SIDEBAR, width=art_size[0], height=art_size[1])
    art_holder.grid(row=7, column=0, sticky="n", padx=12, pady=(12, 0))
    art_holder.grid_propagate(False)
    art_path = (Path(sys.executable).resolve().parent / "branding" / "layers-art.png"
                if getattr(sys, "frozen", False) else Path(os.environ.get("TIFF_UI_ART", "")))
    try:
        from PIL import Image, ImageOps, ImageTk
        if not art_path.is_file():
            raise FileNotFoundError("无可选插画")
        with Image.open(art_path) as source_art:
            fitted = ImageOps.fit(source_art.convert("RGB"), art_size, method=Image.Resampling.LANCZOS)
        art_holder.art_image = ImageTk.PhotoImage(fitted)
        tk.Label(art_holder, image=art_holder.art_image, bg=SIDEBAR, borderwidth=0).pack()
    except (ImportError, OSError, ValueError):
        canvas = tk.Canvas(art_holder, width=art_size[0], height=art_size[1], bg="#285448", highlightthickness=0)
        canvas.pack()
        for y, color in ((70, "#7EA99A"), (88, "#679384"), (106, "#D1DBC9")):
            points = [round(p*dpi_scale) for p in (28,y,94,y-31,162,y,94,y+31)]
            canvas.create_polygon(*points, outline=color, fill="", width=max(1, round(2*dpi_scale)))

    main = ttk.Frame(shell, style="App.TFrame", padding=(28, 14, 28, 10))
    main.grid(row=0, column=1, sticky="nsew")
    main.columnconfigure(0, weight=1)
    main.rowconfigure(2, weight=3, minsize=80)
    main.rowconfigure(5, weight=0)

    header = ttk.Frame(main, style="App.TFrame")
    header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
    header.columnconfigure(0, weight=1)
    tk.Label(
        header,
        text="开始转换",
        bg=BG,
        fg=TEXT,
        font=("Microsoft YaHei UI", 20, "bold"),
        anchor="w",
    ).grid(row=0, column=0, sticky="w")
    tk.Label(
        header,
        text="选择方式，添加影像。",
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

    toolbar_card = ttk.Frame(main, style="Card.TFrame", padding=10)
    toolbar_card.grid(row=1, column=0, sticky="ew", pady=(0, 10))
    toolbar_card.columnconfigure(0, weight=1)
    mode_frame = ttk.Frame(toolbar_card, style="Card.TFrame")
    mode_frame.grid(row=0, column=0, sticky="ew")
    for col in range(3):
        mode_frame.columnconfigure(col, weight=1, uniform="modes")
    ttk.Label(toolbar_card, textvariable=direction_hint_var, style="Muted.Card.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 7))
    file_actions = ttk.Frame(toolbar_card, style="Card.TFrame")
    file_actions.grid(row=2, column=0, sticky="ew")

    tree_card = ttk.Frame(main, style="Card.TFrame", padding=(1, 1, 1, 1))
    tree_card.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
    tree_card.columnconfigure(0, weight=1)
    tree_card.rowconfigure(0, weight=1)
    tree = ttk.Treeview(
        tree_card,
        columns=("name", "folder", "status"),
        show="headings",
        selectmode="extended",
        height=4,
    )
    tree.heading("name", text="文件名")
    tree.heading("folder", text="来源位置")
    tree.heading("status", text="状态")
    tree.column("name", width=180, minwidth=110, anchor="w")
    tree.column("folder", width=320, minwidth=150, anchor="w")
    tree.column("status", width=75, minwidth=65, stretch=False, anchor="center")
    tree_scroll = ttk.Scrollbar(tree_card, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=tree_scroll.set)
    tree.grid(row=0, column=0, sticky="nsew")
    tree_scroll.grid(row=0, column=1, sticky="ns")

    options_card = ttk.Frame(main, style="Card.TFrame", padding=12)
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
        text="保存到",
        bg=CARD,
        fg=MUTED,
        font=("Microsoft YaHei UI", 8),
    )
    output_label.grid(row=2, column=0, sticky="w", pady=(6,0))
    output_entry = ttk.Entry(options_card, textvariable=output_var, style="Clean.TEntry")
    output_entry.grid(row=2, column=1, sticky="ew", padx=(12, 8), pady=(6,0))

    progress_card = ttk.Frame(main, style="Card.TFrame", padding=10)
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

    log_card = ttk.Frame(main, style="Card.TFrame", padding=8)
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
        height=2,
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
    log_card.grid_remove()

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
        needs_reference = direction_var.get() == "to_classic_tiled"
        enabled = bool(selected_files) and bool(output_var.get().strip()) and (not needs_reference or bool(reference_var.get().strip())) and not running
        start_button.configure(state="normal" if enabled else "disabled")
        for button in mode_buttons:
            button.configure(state="disabled" if running else "normal")
        report_button.configure(state="normal" if last_report and last_report.exists() else "disabled")

    def add_paths(paths: Iterable[str | Path]) -> int:
        added = 0
        for raw_path in paths:
            path = Path(raw_path).resolve()
            if (
                path.suffix.lower() not in engine.SUPPORTED_EXTENSIONS
                or not path.is_file()
                or engine.tiff_kind(path) != ("big" if direction_var.get() == "to_classic" else "classic")
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
            direction = selected_direction()
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

    def selected_direction():
        return direction_var.get()

    def direction_changed(_event):
        if selected_direction() in ("to_big_tiled", "to_classic_tiled"):
            replace_original_var.set(False)
            replace_check.configure(state="disabled")
            replace_check.grid_remove()
            toggle_replace_mode()
            option_hint_var.set("仅生成副本，支持部分无压缩 RGB TIFF。")
        else:
            replace_check.configure(state="normal")
            replace_check.grid()
            toggle_replace_mode()
        if selected_direction() == "to_classic_tiled":
            direction_hint_var.set("选择 YS 参考图，转换 TM 影像。")
            nodata_zero_var.set(True)
            nodata_check.configure(state="normal")
            reference_entry.configure(state="normal")
            reference_button.configure(state="normal")
            reference_label.grid()
            reference_entry.grid()
            reference_button.grid()
            nodata_check.grid()
            option_hint_var.set("去除编辑信息；带空间定位的影像不支持。")
        else:
            nodata_zero_var.set(False)
            nodata_check.configure(state="disabled")
            reference_entry.configure(state="disabled")
            reference_button.configure(state="disabled")
            reference_label.grid_remove()
            reference_entry.grid_remove()
            reference_button.grid_remove()
            nodata_check.grid_remove()
            if selected_direction() == "to_big_tiled":
                direction_hint_var.set("普通 TIFF → 256 瓦片 BigTIFF")
            else:
                direction_hint_var.set("BigTIFF → 普通 TIFF，保留图像块。")
        clear_all()

    def open_last_report() -> None:
        if last_report and last_report.exists():
            os.startfile(str(last_report))

    def show_log() -> None:
        detail = tk.Toplevel(root)
        detail.title("运行详情")
        detail.geometry("720x400")
        detail.configure(bg=BG)
        view = tk.Text(detail, wrap="word", padx=14, pady=12, relief="flat",
                       bg=CARD, fg=TEXT, font=("Microsoft YaHei UI", 9))
        view.pack(fill="both", expand=True, padx=16, pady=16)
        view.insert("1.0", log.get("1.0", "end-1c") or "暂无运行记录")
        view.configure(state="disabled")

    def worker(inputs, output_dir, overwrite, replace_original, direction, nodata_zero, reference) -> None:
        try:
            engine.convert_batch(
                inputs,
                output_dir,
                direction=direction,
                overwrite=overwrite,
                replace_original=replace_original,
                input_scope="多文件/文件夹选择",
                event_callback=event_queue.put,
                nodata_zero=nodata_zero,
                reference=reference,
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
        if selected_direction() == "to_classic_tiled" and not Path(reference_var.get().strip()).is_file():
            messagebox.showerror(engine.APP_NAME, "请选择参考 TIFF 文件。")
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
                selected_direction(),
                nodata_zero_var.get(),
                reference_var.get().strip() if selected_direction() == "to_classic_tiled" else None,
            ),
            daemon=True,
        ).start()

    mode_buttons = []
    for i, (label, value) in enumerate((("TM → YS", "to_classic_tiled"),
                                        ("转 BigTIFF", "to_big_tiled"),
                                        ("转普通 TIFF", "to_classic"))):
        button = ttk.Radiobutton(mode_frame, text=label, value=value, variable=direction_var,
                                 command=lambda: direction_changed(None), style="Mode.TRadiobutton")
        button.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 5, 0 if i == 2 else 5))
        mode_buttons.append(button)
    ttk.Button(file_actions, text="添加文件", style="Secondary.TButton", command=choose_files).grid(row=0, column=0, padx=(0, 7))
    ttk.Button(file_actions, text="添加文件夹", style="Secondary.TButton", command=choose_folder).grid(row=0, column=1, padx=7)
    ttk.Button(file_actions, text="移除选中", style="Secondary.TButton", command=remove_selected).grid(row=0, column=2, padx=7)
    ttk.Button(file_actions, text="清空", style="Secondary.TButton", command=clear_all).grid(row=0, column=3, padx=7)
    ttk.Checkbutton(file_actions, text="包含子文件夹", variable=recursive_var).grid(row=0, column=4, padx=(18, 0))
    ttk.Button(options_card, text="选择目录", style="Secondary.TButton", command=choose_output).grid(row=2, column=2, padx=(0, 8), pady=(6,0))
    replace_check = ttk.Checkbutton(
        options_card,
        text="同名无损替换原文件",
        variable=replace_original_var,
        command=toggle_replace_mode,
    )
    replace_check.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 3))
    overwrite_check = ttk.Checkbutton(options_card, text="覆盖已存在的转换副本", variable=overwrite_var)
    overwrite_check.grid(row=3, column=2, columnspan=2, sticky="w", pady=(6, 3))
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
    reference_label = ttk.Label(options_card, text="YS 参考图", style="Muted.Card.TLabel")
    reference_label.grid(row=1, column=0, sticky="w")
    reference_entry = ttk.Entry(options_card, textvariable=reference_var, style="Clean.TEntry")
    reference_entry.grid(row=1, column=1, sticky="ew", padx=(12,8))
    def choose_reference():
        path = filedialog.askopenfilename(title="选择 YS 参考图", filetypes=[("TIFF", "*.tif *.tiff")])
        if path:
            reference_var.set(path)
    reference_button = ttk.Button(options_card, text="选择 YS 参考图", style="Secondary.TButton", command=choose_reference)
    reference_button.grid(row=1, column=2, padx=(0,8))
    nodata_check = ttk.Checkbutton(options_card, text="将像素值 0 标记为 NoData", variable=nodata_zero_var)
    nodata_check.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 3))
    hint_label.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(3, 0))

    tk.Label(
        sidebar,
        text="本地离线运行\n无联网 · 无遥测",
        bg=SIDEBAR,
        fg=SIDEBAR_MUTED,
        font=("Microsoft YaHei UI", 7),
        justify="left",
        anchor="sw",
    ).grid(row=9, column=0, sticky="sw", padx=22, pady=20)

    ttk.Button(footer, text="运行详情", style="Secondary.TButton", command=show_log).grid(row=0, column=1, padx=(12, 8))
    report_button = ttk.Button(footer, text="打开报告", style="Secondary.TButton", command=open_last_report, state="disabled")
    report_button.grid(row=0, column=2, padx=(0, 8))
    start_button = ttk.Button(footer, text="开始转换", style="Primary.TButton", command=start_conversion, state="disabled")
    start_button.grid(row=0, column=3)

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
    reference_var.trace_add("write", lambda *_: update_controls())
    root.protocol("WM_DELETE_WINDOW", on_close)
    direction_changed(None)
    status_var.set("请添加待转换的 TIFF 影像")
    update_controls()
    root.after(120, poll_events)
    if smoke_test:
        root.after(250, root.destroy)
    root.mainloop()
    return 0
