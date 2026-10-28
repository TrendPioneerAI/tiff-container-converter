"""Windows GUI and command line entry point for the unlicensed V4."""
import argparse
from pathlib import Path

import engine
from modern_gui import run_gui_app


def main():
    parser = argparse.ArgumentParser(description="TIFF 与 BigTIFF 双向转换")
    parser.add_argument("inputs", nargs="*", help="输入 TIFF 文件")
    parser.add_argument("--folder", help="扫描输入文件夹")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--direction", choices=("to_big", "to_classic"), default="to_big")
    parser.add_argument("--output-dir", help="输出与 PDF 报告文件夹")
    parser.add_argument("--replace-original", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--version", action="version", version=engine.APP_VERSION)
    args = parser.parse_args()
    if not args.inputs and not args.folder:
        return run_gui_app(engine)
    paths = [Path(x) for x in args.inputs]
    if args.folder:
        paths += engine.scan_image_folder(Path(args.folder), args.recursive, args.direction)
    if not args.output_dir:
        parser.error("命令行批量处理需要指定 --output-dir")
    summary, report = engine.convert_batch(
        paths, Path(args.output_dir), direction=args.direction, overwrite=args.overwrite,
        replace_original=args.replace_original, input_scope=args.folder or "命令行文件选择",
    )
    print(f"成功 {summary['passed']}，失败 {summary['failed']}，PDF：{report}")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
