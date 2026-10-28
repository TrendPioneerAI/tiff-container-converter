"""Bidirectional TIFF container conversion, using the V3 low-level parser."""
from __future__ import annotations

import hashlib
import os
import struct
import tempfile
import time
import uuid
from pathlib import Path

import tiff_core as base
from batch_pdf_report import generate_batch_pdf

APP_NAME = "TIFF 双向转换工具"
APP_VERSION = "4.0.0"
SUPPORTED_EXTENSIONS = base.SUPPORTED_EXTENSIONS
ConversionError = base.ConversionError


def tiff_kind(path):
    try:
        with Path(path).open("rb") as stream:
            head = stream.read(4)
        return {b"II*\0": "classic", b"MM\0*": "classic",
                b"II+\0": "big", b"MM\0+": "big"}.get(head)
    except OSError:
        return None


def scan_image_folder(folder, recursive, direction="to_big"):
    folder = Path(folder)
    wanted = "classic" if direction == "to_big" else "big"
    paths = folder.rglob("*") if recursive else folder.glob("*")
    return sorted((p for p in paths if p.is_file()
                   and p.suffix.lower() in SUPPORTED_EXTENSIONS
                   and tiff_kind(p) == wanted), key=lambda p: str(p).lower())


def _reverse(source_path, output_path, progress):
    source = base.parse_tiff(source_path, require_bigtiff=False)
    if source.variant != "ClassicTIFF":
        raise ConversionError("输入不是普通 TIFF（版本42）")
    endian = source.endian
    cursor = 16
    pages = []
    for ifd in source.ifds:
        cursor = base.align(cursor, 8)
        offset = cursor
        entries = []
        for entry in ifd.entries:
            if entry.tag == ifd.data_offsets_tag:
                entries.append(base.OutputEntry(entry.tag, 16, entry.count, b"\0" * (8 * entry.count)))
            else:
                entries.append(base.OutputEntry(entry.tag, entry.type_id, entry.count, entry.raw))
        pages.append(base.OutputIfd(ifd, offset, entries))
        cursor = offset + 8 + len(entries) * 20 + 8
    for i, page in enumerate(pages):
        page.next_offset = pages[i + 1].offset if i + 1 < len(pages) else 0
    for page in pages:
        for entry in page.entries:
            if len(entry.raw) > 8:
                cursor = base.align(cursor, 8)
                entry.external_offset = cursor
                cursor += len(entry.raw)
    cursor = base.align(cursor, 8)
    for page in pages:
        offsets = []
        for size in page.source.chunk_bytecounts:
            if size:
                cursor = base.align(cursor, 8)
                offsets.append(cursor)
                cursor += size
            else:
                offsets.append(0)
        page.destination_chunk_offsets = offsets
        offset_entry = next(e for e in page.entries if e.tag == page.source.data_offsets_tag)
        offset_entry.raw = struct.pack(endian + "Q" * len(offsets), *offsets)

    total = sum(sum(p.source.chunk_bytecounts) for p in pages)
    copied = 0
    digest = hashlib.sha256()
    started = time.time()
    progress("inspect", 1, 1, f"结构检查通过，预计输出 {cursor:,} 字节")
    with source.path.open("rb") as src, Path(output_path).open("xb") as dst:
        dst.write(source.byte_order + struct.pack(endian + "HHHQ", 43, 8, 0, pages[0].offset))
        for page in pages:
            base.pad_to(dst, page.offset)
            dst.write(struct.pack(endian + "Q", len(page.entries)))
            for e in page.entries:
                dst.write(struct.pack(endian + "HHQ", e.tag, e.type_id, e.count))
                dst.write(struct.pack(endian + "Q", e.external_offset)
                          if e.external_offset is not None else e.raw.ljust(8, b"\0"))
            dst.write(struct.pack(endian + "Q", page.next_offset))
        for page in pages:
            for e in page.entries:
                if e.external_offset is not None:
                    base.pad_to(dst, e.external_offset)
                    dst.write(e.raw)
        progress("copy", 0, total, "正在原样复制图像块")
        last_notice = 0
        for page in pages:
            for old, size, new in zip(page.source.source_chunk_offsets,
                                      page.source.chunk_bytecounts,
                                      page.destination_chunk_offsets):
                if not size:
                    continue
                base.pad_to(dst, new)
                src.seek(old)
                remaining = size
                while remaining:
                    data = base.read_exact(src, min(base.COPY_BUFFER, remaining), "图像块")
                    dst.write(data)
                    digest.update(data)
                    remaining -= len(data)
                    copied += len(data)
                    if copied - last_notice >= 32 * 1024 * 1024 or copied == total:
                        progress("copy", copied, total, f"已复制 {copied:,}/{total:,} 字节")
                        last_notice = copied
        dst.flush()
        os.fsync(dst.fileno())
    progress("verify", 0, total, "正在回读校验 BigTIFF")
    output = base.parse_tiff(output_path, require_bigtiff=True)
    errors = []
    if output.byte_order != source.byte_order or len(output.ifds) != len(source.ifds):
        errors.append("字节序或页面数量改变")
    for old, new in zip(source.ifds, output.ifds):
        if old.data_offsets_tag != new.data_offsets_tag or old.chunk_bytecounts != new.chunk_bytecounts:
            errors.append("图像块布局或长度改变")
        old_tags = {e.tag: (e.type_id, e.count, e.raw) for e in old.entries if e.tag != old.data_offsets_tag}
        new_tags = {e.tag: (e.type_id, e.count, e.raw) for e in new.entries if e.tag != new.data_offsets_tag}
        if old_tags != new_tags:
            errors.append("非偏移标签的类型、数量或值改变")
    source_hash = digest.hexdigest()
    dest_hash = base.payload_sha256(output.path, output.ifds)
    metadata_source = base.metadata_fingerprint(source)
    metadata_output = base.metadata_fingerprint(output)
    if source_hash != dest_hash or metadata_source != metadata_output:
        errors.append("图像块或非结构性元数据 SHA-256 不一致")
    if errors:
        raise ConversionError("；".join(errors))
    progress("verify", total, total, "回读验证通过")
    return {
        "status": "PASS",
        "input": {"path": str(source.path), "format": source.variant, "size_bytes": source.size},
        "output": {"path": str(output.path), "format": output.variant, "size_bytes": output.size},
        "validation": {
            "raw_image_payload_sha256_input": source_hash,
            "raw_image_payload_sha256_output": dest_hash,
            "raw_image_payload_identical": True,
            "non_structural_metadata_sha256_input": metadata_source,
            "non_structural_metadata_sha256_output": metadata_output,
            "non_structural_metadata_identical": True,
        },
        "elapsed_seconds": round(time.time() - started, 3),
    }


def _stat_signature(path):
    s = path.stat()
    return s.st_size, s.st_mtime_ns


def convert_batch(inputs, output_dir, direction="to_big", overwrite=False,
                  replace_original=False, input_scope="多文件/文件夹选择", event_callback=None):
    if direction not in ("to_big", "to_classic"):
        raise ConversionError("未知转换方向")
    inputs = list(dict.fromkeys(Path(p).resolve() for p in inputs))
    if not inputs:
        raise ConversionError("未选择输入文件")
    outdir = Path(output_dir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    wanted = "classic" if direction == "to_big" else "big"
    suffix = "-bigtiff" if direction == "to_big" else "-classic"
    results, used = [], set()
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    def emit(*event):
        if event_callback:
            event_callback(tuple(event))
    for i, source in enumerate(inputs):
        emit("file_start", str(source).lower(), i, len(inputs), source.name)
        def progress(phase, current, total, message):
            emit("progress", i, len(inputs), phase, current, total, message)
        temp = None
        try:
            if tiff_kind(source) != wanted:
                raise ConversionError("输入格式与所选转换方向不匹配")
            original_stat = source.stat()
            before = (original_stat.st_size, original_stat.st_mtime_ns)
            dest = source if replace_original else outdir / f"{source.stem}{suffix}.tif"
            if not replace_original and str(dest).lower() in used:
                dest = outdir / f"{source.stem}{suffix}-{hashlib.sha256(str(source).encode()).hexdigest()[:10]}.tif"
            used.add(str(dest).lower())
            if not replace_original and dest.resolve() in inputs:
                raise ConversionError("输出路径与批次输入重叠，已拒绝覆盖")
            if not replace_original and dest.exists() and not overwrite:
                raise ConversionError("输出文件已存在；请勾选覆盖副本或选择其他文件夹")
            temp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.pending")
            if direction == "to_big":
                report = _reverse(source, temp, progress)
            else:
                report = base.convert(source, temp, None, False, True, progress)
            if _stat_signature(source) != before:
                raise ConversionError("转换期间源文件发生变化，已取消替换")
            parsed = base.parse_tiff(source, require_bigtiff=False)
            if base.payload_sha256(source, parsed.ifds) != report["validation"]["raw_image_payload_sha256_input"]:
                raise ConversionError("源文件图像块再次校验不一致")
            if _stat_signature(source) != before:
                raise ConversionError("源文件复核期间发生变化，已取消替换")
            warnings = []
            if replace_original:
                try:
                    os.utime(temp, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
                except OSError as exc:
                    warnings.append(f"未能保留文件时间：{exc}")
                backup = source.with_name(f".{source.name}.{uuid.uuid4().hex}.original-backup")
                os.replace(source, backup)
                try:
                    os.replace(temp, source)
                    temp = None
                except OSError as exc:
                    try:
                        os.replace(backup, source)
                    except OSError as restore_exc:
                        raise ConversionError(
                            f"替换和自动恢复均失败；请从备份恢复：{backup}（{restore_exc}）"
                        ) from exc
                    raise ConversionError(f"替换失败，已恢复原文件：{exc}") from exc
                warnings.append(f"原文件备份保留：{backup}")
            else:
                if overwrite:
                    os.replace(temp, dest)
                elif os.name == "nt":
                    # Windows rename fails if another process created dest.
                    os.rename(temp, dest)
                else:
                    # A hard-link creation fails atomically when dest exists.
                    os.link(temp, dest)
                    temp.unlink()
                temp = None
            report["output"]["path"] = str(dest)
            report["output"]["size_bytes"] = dest.stat().st_size
            results.append({"input": str(source), "output": str(dest), "status": "PASS",
                            "replaced_original": replace_original, "warnings": warnings,
                            "validation": report["validation"], "report": report})
            emit("file_done", str(source).lower(), "PASS", str(dest))
        except Exception as exc:
            results.append({"input": str(source), "status": "FAIL", "error": str(exc),
                            "error_type": type(exc).__name__, "replaced_original": False})
            emit("file_done", str(source).lower(), "FAIL", str(exc))
        finally:
            if temp is not None and temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass
    summary = {
        "tool": {"name": APP_NAME, "version": APP_VERSION}, "license": {"customer": "无需授权"},
        "direction": direction, "started_at": started,
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "input_scope": input_scope, "output_scope": str(outdir),
        "replace_original": replace_original, "total": len(results),
        "passed": sum(x["status"] == "PASS" for x in results),
        "failed": sum(x["status"] != "PASS" for x in results), "results": results,
    }
    report_path = outdir / f"TIFF转换报告_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.pdf"
    pdf_info = generate_batch_pdf(summary, report_path)
    emit("batch_done", summary, report_path, pdf_info)
    return summary, report_path
