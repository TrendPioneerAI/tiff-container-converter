from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import struct
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Callable, Iterable

from license_core import (
    LicenseError,
    LicenseInfo,
    find_valid_license,
    install_license,
    machine_code,
)


APP_NAME = "BigTIFF 转普通 TIFF 工具"
APP_VERSION = "2.0.0"
CLASSIC_MAX = 0xFFFFFFFF
COPY_BUFFER = 8 * 1024 * 1024
MAX_IFDS = 4096
MAX_IFD_ENTRIES = 65535
MAX_TAG_PAYLOAD = 256 * 1024 * 1024

TYPE_SIZES = {
    1: 1,   # BYTE
    2: 1,   # ASCII
    3: 2,   # SHORT
    4: 4,   # LONG
    5: 8,   # RATIONAL
    6: 1,   # SBYTE
    7: 1,   # UNDEFINED
    8: 2,   # SSHORT
    9: 4,   # SLONG
    10: 8,  # SRATIONAL
    11: 4,  # FLOAT
    12: 8,  # DOUBLE
    13: 4,  # IFD
    16: 8,  # LONG8 (BigTIFF only)
    17: 8,  # SLONG8 (BigTIFF only)
    18: 8,  # IFD8 (BigTIFF only)
}

TAG_NAMES = {
    254: "NewSubfileType",
    256: "ImageWidth",
    257: "ImageLength",
    258: "BitsPerSample",
    259: "Compression",
    262: "PhotometricInterpretation",
    270: "ImageDescription",
    271: "Make",
    272: "Model",
    273: "StripOffsets",
    274: "Orientation",
    277: "SamplesPerPixel",
    278: "RowsPerStrip",
    279: "StripByteCounts",
    282: "XResolution",
    283: "YResolution",
    284: "PlanarConfiguration",
    296: "ResolutionUnit",
    305: "Software",
    306: "DateTime",
    317: "Predictor",
    322: "TileWidth",
    323: "TileLength",
    324: "TileOffsets",
    325: "TileByteCounts",
    330: "SubIFDs",
    339: "SampleFormat",
    340: "SMinSampleValue",
    341: "SMaxSampleValue",
    34264: "ModelTransformationTag",
    34377: "PhotoshopImageResources",
    34665: "ExifIFDPointer",
    34675: "ICCProfile",
    34735: "GeoKeyDirectoryTag",
    34736: "GeoDoubleParamsTag",
    34737: "GeoAsciiParamsTag",
    34853: "GPSIFDPointer",
    40965: "InteroperabilityIFDPointer",
    42112: "GDAL_METADATA",
    42113: "GDAL_NODATA",
}

# These fields contain offsets to structures that must be relocated. Refusing them
# is safer than copying stale pointers into a seemingly valid Classic TIFF.
POINTER_TAGS = {
    288,    # FreeOffsets
    324 + 0,  # TileOffsets is handled separately below
    330,    # SubIFDs
    513,    # JPEGInterchangeFormat
    34665,  # ExifIFD
    34853,  # GPSIFD
    40965,  # InteroperabilityIFD
}

Progress = Callable[[str, int, int, str], None]


class ConversionError(RuntimeError):
    pass


@dataclass
class Entry:
    tag: int
    type_id: int
    count: int
    raw: bytes
    source_value_offset: int | None = None

    @property
    def name(self) -> str:
        return TAG_NAMES.get(self.tag, f"Tag_{self.tag}")


@dataclass
class Ifd:
    source_offset: int
    entries: list[Entry]
    next_source_offset: int
    data_offsets_tag: int
    data_bytecounts_tag: int
    source_chunk_offsets: list[int]
    chunk_bytecounts: list[int]


@dataclass
class ParsedTiff:
    path: Path
    size: int
    endian: str
    byte_order: bytes
    variant: str
    ifds: list[Ifd]


@dataclass
class OutputEntry:
    tag: int
    type_id: int
    count: int
    raw: bytes
    external_offset: int | None = None


@dataclass
class OutputIfd:
    source: Ifd
    offset: int
    entries: list[OutputEntry]
    next_offset: int = 0
    destination_chunk_offsets: list[int] = field(default_factory=list)


def align(value: int, boundary: int = 4) -> int:
    return (value + boundary - 1) // boundary * boundary


def read_exact(handle: BinaryIO, size: int, context: str) -> bytes:
    data = handle.read(size)
    if len(data) != size:
        raise ConversionError(f"文件意外结束：{context}，需要 {size} 字节，仅读到 {len(data)} 字节")
    return data


def unpack_unsigned(raw: bytes, type_id: int, endian: str) -> list[int]:
    codes = {1: "B", 3: "H", 4: "I", 16: "Q"}
    code = codes.get(type_id)
    if code is None:
        raise ConversionError(f"偏移/长度字段使用了不支持的数据类型 {type_id}")
    size = TYPE_SIZES[type_id]
    if len(raw) % size:
        raise ConversionError("偏移/长度字段字节数异常")
    return list(struct.unpack(endian + code * (len(raw) // size), raw))


def parse_tiff(path: Path, require_bigtiff: bool) -> ParsedTiff:
    path = path.resolve()
    if not path.is_file():
        raise ConversionError(f"输入文件不存在：{path}")
    size = path.stat().st_size
    with path.open("rb") as handle:
        header = read_exact(handle, 16, "TIFF文件头")
        if header[:2] == b"II":
            endian = "<"
        elif header[:2] == b"MM":
            endian = ">"
        else:
            raise ConversionError("不是有效的 TIFF：缺少 II/MM 字节序标记")
        magic = struct.unpack(endian + "H", header[2:4])[0]
        if magic == 43:
            offset_size, reserved = struct.unpack(endian + "HH", header[4:8])
            if offset_size != 8 or reserved != 0:
                raise ConversionError("BigTIFF文件头无效：偏移宽度或保留字段异常")
            first_ifd = struct.unpack(endian + "Q", header[8:16])[0]
            variant = "BigTIFF"
            count_size, entry_size, inline_size, next_size = 8, 20, 8, 8
            count_code, entry_code, next_code = "Q", "HHQQ", "Q"
        elif magic == 42:
            if require_bigtiff:
                raise ConversionError("输入已经是普通 TIFF（版本42），无需再次转换")
            first_ifd = struct.unpack(endian + "I", header[4:8])[0]
            variant = "ClassicTIFF"
            count_size, entry_size, inline_size, next_size = 2, 12, 4, 4
            count_code, entry_code, next_code = "H", "HHII", "I"
        else:
            raise ConversionError(f"未知 TIFF 版本号：{magic}")

        ifds: list[Ifd] = []
        seen: set[int] = set()
        ifd_offset = first_ifd
        while ifd_offset:
            if len(ifds) >= MAX_IFDS:
                raise ConversionError(f"IFD数量超过安全上限 {MAX_IFDS}")
            if ifd_offset in seen:
                raise ConversionError("检测到循环IFD指针")
            if not 0 < ifd_offset < size:
                raise ConversionError(f"IFD偏移超出文件范围：{ifd_offset}")
            seen.add(ifd_offset)
            handle.seek(ifd_offset)
            count = struct.unpack(endian + count_code, read_exact(handle, count_size, "IFD标签数"))[0]
            if count > MAX_IFD_ENTRIES:
                raise ConversionError(f"IFD标签数 {count} 超过普通 TIFF 可表示上限")
            entries: list[Entry] = []
            for _ in range(count):
                blob = read_exact(handle, entry_size, "IFD标签")
                tag, type_id, value_count, value_or_offset = struct.unpack(endian + entry_code, blob)
                type_size = TYPE_SIZES.get(type_id)
                if type_size is None:
                    raise ConversionError(f"{TAG_NAMES.get(tag, tag)} 使用未知 TIFF 类型 {type_id}")
                byte_length = value_count * type_size
                if byte_length > MAX_TAG_PAYLOAD:
                    raise ConversionError(
                        f"{TAG_NAMES.get(tag, tag)} 标签负载 {byte_length} 字节，超过安全上限"
                    )
                slot = blob[12:20] if variant == "BigTIFF" else blob[8:12]
                if byte_length <= inline_size:
                    raw = slot[:byte_length]
                    source_value_offset = None
                else:
                    source_value_offset = value_or_offset
                    if value_or_offset + byte_length > size:
                        raise ConversionError(f"{TAG_NAMES.get(tag, tag)} 标签数据超出文件范围")
                    return_pos = handle.tell()
                    handle.seek(value_or_offset)
                    raw = read_exact(handle, byte_length, f"标签 {TAG_NAMES.get(tag, tag)}")
                    handle.seek(return_pos)
                entries.append(Entry(tag, type_id, value_count, raw, source_value_offset))
            next_ifd = struct.unpack(endian + next_code, read_exact(handle, next_size, "下一IFD指针"))[0]

            by_tag = {entry.tag: entry for entry in entries}
            tiled = 324 in by_tag or 325 in by_tag
            stripped = 273 in by_tag or 279 in by_tag
            if tiled and stripped:
                raise ConversionError("同一IFD同时包含Tile与Strip存储字段，拒绝转换")
            if tiled:
                offsets_tag, bytecounts_tag = 324, 325
            elif stripped:
                offsets_tag, bytecounts_tag = 273, 279
            else:
                raise ConversionError("IFD缺少TileOffsets/StripOffsets，无法安全复制图像数据")
            if offsets_tag not in by_tag or bytecounts_tag not in by_tag:
                raise ConversionError("图像数据偏移与长度标签不成对")

            for entry in entries:
                if entry.tag in POINTER_TAGS and entry.tag != offsets_tag:
                    raise ConversionError(
                        f"检测到需要重定位的指针标签 {entry.name}；当前安全模式拒绝转换"
                    )
                if entry.type_id in (16, 17, 18) and entry.tag not in (offsets_tag, bytecounts_tag):
                    raise ConversionError(
                        f"标签 {entry.name} 使用普通 TIFF 无法原样表达的64位类型 {entry.type_id}"
                    )

            chunk_offsets = unpack_unsigned(by_tag[offsets_tag].raw, by_tag[offsets_tag].type_id, endian)
            chunk_bytecounts = unpack_unsigned(
                by_tag[bytecounts_tag].raw, by_tag[bytecounts_tag].type_id, endian
            )
            if len(chunk_offsets) != len(chunk_bytecounts):
                raise ConversionError("图像块偏移数量与长度数量不一致")
            for index, (chunk_offset, byte_count) in enumerate(zip(chunk_offsets, chunk_bytecounts)):
                if byte_count == 0:
                    continue
                if chunk_offset == 0 or chunk_offset + byte_count > size:
                    raise ConversionError(f"第 {index + 1} 个图像块超出源文件范围")

            ifds.append(
                Ifd(
                    source_offset=ifd_offset,
                    entries=entries,
                    next_source_offset=next_ifd,
                    data_offsets_tag=offsets_tag,
                    data_bytecounts_tag=bytecounts_tag,
                    source_chunk_offsets=chunk_offsets,
                    chunk_bytecounts=chunk_bytecounts,
                )
            )
            ifd_offset = next_ifd

    return ParsedTiff(path, size, endian, header[:2], variant, ifds)


def plan_output(source: ParsedTiff) -> tuple[list[OutputIfd], int]:
    endian = source.endian
    cursor = 8
    output_ifds: list[OutputIfd] = []

    # Place all Classic IFDs first so NextIFD pointers are known before writing.
    for source_ifd in source.ifds:
        ifd_offset = align(cursor, 4)
        entries: list[OutputEntry] = []
        for entry in source_ifd.entries:
            if entry.count > CLASSIC_MAX:
                raise ConversionError(f"标签 {entry.name} 的元素数量超过32位上限")
            if entry.tag == source_ifd.data_offsets_tag:
                raw = b"\x00" * (4 * entry.count)
                entries.append(OutputEntry(entry.tag, 4, entry.count, raw))
            elif entry.tag == source_ifd.data_bytecounts_tag and entry.type_id == 16:
                values = unpack_unsigned(entry.raw, 16, endian)
                if any(value > CLASSIC_MAX for value in values):
                    raise ConversionError("存在超过4 GB的单个图像块，不能写入普通 TIFF")
                raw = struct.pack(endian + "I" * len(values), *values)
                entries.append(OutputEntry(entry.tag, 4, entry.count, raw))
            else:
                entries.append(OutputEntry(entry.tag, entry.type_id, entry.count, entry.raw))
        output_ifds.append(OutputIfd(source_ifd, ifd_offset, entries))
        cursor = ifd_offset + 2 + len(entries) * 12 + 4

    for index, output_ifd in enumerate(output_ifds):
        output_ifd.next_offset = output_ifds[index + 1].offset if index + 1 < len(output_ifds) else 0

    # Allocate all out-of-line tag values.
    cursor = align(cursor, 4)
    for output_ifd in output_ifds:
        for entry in output_ifd.entries:
            if len(entry.raw) > 4:
                cursor = align(cursor, 4)
                entry.external_offset = cursor
                cursor += len(entry.raw)

    data_cursor = align(cursor, 8)
    for output_ifd in output_ifds:
        destination_offsets: list[int] = []
        for source_offset, byte_count in zip(
            output_ifd.source.source_chunk_offsets,
            output_ifd.source.chunk_bytecounts,
        ):
            if byte_count == 0:
                destination_offsets.append(0)
                continue
            destination_offsets.append(data_cursor)
            data_cursor += byte_count
            if data_cursor > CLASSIC_MAX:
                raise ConversionError(
                    "预计输出超过普通 TIFF 的4 GB上限；必须继续使用BigTIFF或采用无损压缩/分幅"
                )
        output_ifd.destination_chunk_offsets = destination_offsets
        offsets_entry = next(
            entry for entry in output_ifd.entries if entry.tag == output_ifd.source.data_offsets_tag
        )
        offsets_entry.raw = struct.pack(endian + "I" * len(destination_offsets), *destination_offsets)

    return output_ifds, data_cursor


def write_zeros(handle: BinaryIO, count: int) -> None:
    while count > 0:
        block = min(count, 1024 * 1024)
        handle.write(b"\x00" * block)
        count -= block


def pad_to(handle: BinaryIO, target: int) -> None:
    current = handle.tell()
    if current > target:
        raise ConversionError(f"内部布局错误：当前位置 {current} 超过目标位置 {target}")
    write_zeros(handle, target - current)


def payload_sha256(path: Path, ifds: Iterable[Ifd], offsets_override: list[list[int]] | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for page_index, ifd in enumerate(ifds):
            offsets = offsets_override[page_index] if offsets_override is not None else ifd.source_chunk_offsets
            for chunk_offset, byte_count in zip(offsets, ifd.chunk_bytecounts):
                if byte_count == 0:
                    continue
                handle.seek(chunk_offset)
                remaining = byte_count
                while remaining:
                    data = read_exact(handle, min(COPY_BUFFER, remaining), "图像块哈希")
                    digest.update(data)
                    remaining -= len(data)
    return digest.hexdigest()


def metadata_fingerprint(parsed: ParsedTiff) -> str:
    digest = hashlib.sha256()
    for ifd in parsed.ifds:
        for entry in ifd.entries:
            if entry.tag in (ifd.data_offsets_tag, ifd.data_bytecounts_tag):
                continue
            digest.update(struct.pack(">HHQ", entry.tag, entry.type_id, entry.count))
            digest.update(entry.raw)
    return digest.hexdigest()


def validate_conversion(source: ParsedTiff, output_path: Path, source_payload_hash: str) -> dict:
    output = parse_tiff(output_path, require_bigtiff=False)
    errors: list[str] = []
    if output.variant != "ClassicTIFF":
        errors.append("输出文件不是Classic TIFF")
    if output.byte_order != source.byte_order:
        errors.append("字节序发生改变")
    if len(output.ifds) != len(source.ifds):
        errors.append("IFD数量发生改变")

    unchanged_tags = 0
    expected_structural_changes: list[str] = []
    for page_index, (source_ifd, output_ifd) in enumerate(zip(source.ifds, output.ifds)):
        source_tags = {entry.tag: entry for entry in source_ifd.entries}
        output_tags = {entry.tag: entry for entry in output_ifd.entries}
        if set(source_tags) != set(output_tags):
            errors.append(f"IFD {page_index} 标签集合发生改变")
            continue
        for tag, source_entry in source_tags.items():
            output_entry = output_tags[tag]
            if tag == source_ifd.data_offsets_tag:
                if output_entry.type_id != 4 or output_entry.count != source_entry.count:
                    errors.append(f"IFD {page_index} 图像块偏移标签转换异常")
                else:
                    expected_structural_changes.append(
                        f"IFD {page_index} {source_entry.name}: 64/32位文件偏移按Classic TIFF重新计算"
                    )
                continue
            if tag == source_ifd.data_bytecounts_tag:
                source_counts = unpack_unsigned(source_entry.raw, source_entry.type_id, source.endian)
                output_counts = unpack_unsigned(output_entry.raw, output_entry.type_id, output.endian)
                if source_counts != output_counts:
                    errors.append(f"IFD {page_index} 图像块长度发生改变")
                elif source_entry.type_id != output_entry.type_id:
                    expected_structural_changes.append(
                        f"IFD {page_index} {source_entry.name}: LONG8转换为LONG，数值不变"
                    )
                else:
                    unchanged_tags += 1
                continue
            if (
                source_entry.type_id != output_entry.type_id
                or source_entry.count != output_entry.count
                or source_entry.raw != output_entry.raw
            ):
                errors.append(f"IFD {page_index} 标签 {source_entry.name} 的值发生改变")
            else:
                unchanged_tags += 1

    output_offsets = [ifd.source_chunk_offsets for ifd in output.ifds]
    output_payload_hash = payload_sha256(output_path, output.ifds, output_offsets)
    if output_payload_hash != source_payload_hash:
        errors.append("图像块字节SHA-256不一致")

    source_metadata_hash = metadata_fingerprint(source)
    output_metadata_hash = metadata_fingerprint(output)
    if source_metadata_hash != output_metadata_hash:
        errors.append("非结构性元数据SHA-256不一致")

    return {
        "status": "PASS" if not errors else "FAIL",
        "tool": {"name": APP_NAME, "version": APP_VERSION},
        "input": {
            "path": str(source.path),
            "format": source.variant,
            "size_bytes": source.size,
            "byte_order": source.byte_order.decode("ascii"),
            "ifd_count": len(source.ifds),
            "tag_count": sum(len(ifd.entries) for ifd in source.ifds),
        },
        "output": {
            "path": str(output_path.resolve()),
            "format": output.variant,
            "size_bytes": output_path.stat().st_size,
            "byte_order": output.byte_order.decode("ascii"),
            "ifd_count": len(output.ifds),
            "tag_count": sum(len(ifd.entries) for ifd in output.ifds),
        },
        "validation": {
            "raw_image_payload_sha256_input": source_payload_hash,
            "raw_image_payload_sha256_output": output_payload_hash,
            "raw_image_payload_identical": source_payload_hash == output_payload_hash,
            "non_structural_metadata_sha256_input": source_metadata_hash,
            "non_structural_metadata_sha256_output": output_metadata_hash,
            "non_structural_metadata_identical": source_metadata_hash == output_metadata_hash,
            "unchanged_tag_count": unchanged_tags,
            "expected_structural_changes": expected_structural_changes,
            "errors": errors,
        },
        "limits": [
            "Classic TIFF使用32位偏移，输出必须小于4 GiB",
            "文件头、IFD结构及图像块偏移必然改变",
            "图像块按原始字节复制，不进行解码、重采样、色彩调整或重新压缩",
        ],
    }


def convert(
    input_path: Path,
    output_path: Path,
    report_path: Path | None,
    overwrite: bool = False,
    verify: bool = True,
    progress: Progress | None = None,
) -> dict:
    def notify(phase: str, current: int, total: int, message: str) -> None:
        if progress:
            progress(phase, current, total, message)

    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if input_path == output_path:
        raise ConversionError("输出路径不能与输入文件相同；本工具不会覆盖原始影像")
    if output_path.exists() and not overwrite:
        raise ConversionError(f"输出文件已存在：{output_path}；请改名或明确允许覆盖")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    notify("inspect", 0, 1, "正在检查BigTIFF结构和Classic TIFF容量…")
    source = parse_tiff(input_path, require_bigtiff=True)
    output_ifds, predicted_size = plan_output(source)
    notify("inspect", 1, 1, f"结构检查通过，预计输出 {predicted_size:,} 字节")

    total_payload = sum(sum(ifd.chunk_bytecounts) for ifd in source.ifds)
    copied = 0
    source_payload_digest = hashlib.sha256()
    temp_path: Path | None = None
    start = time.time()
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=output_path.name + ".",
            suffix=".partial",
            dir=output_path.parent,
            delete=False,
        ) as destination, input_path.open("rb") as source_handle:
            temp_path = Path(destination.name)
            endian = source.endian
            destination.write(source.byte_order)
            destination.write(struct.pack(endian + "H", 42))
            destination.write(struct.pack(endian + "I", output_ifds[0].offset))

            for output_ifd in output_ifds:
                pad_to(destination, output_ifd.offset)
                destination.write(struct.pack(endian + "H", len(output_ifd.entries)))
                for entry in output_ifd.entries:
                    destination.write(struct.pack(endian + "HHI", entry.tag, entry.type_id, entry.count))
                    if len(entry.raw) <= 4:
                        destination.write(entry.raw.ljust(4, b"\x00"))
                    else:
                        if entry.external_offset is None:
                            raise ConversionError("内部布局错误：缺少标签外部偏移")
                        destination.write(struct.pack(endian + "I", entry.external_offset))
                destination.write(struct.pack(endian + "I", output_ifd.next_offset))

            for output_ifd in output_ifds:
                for entry in output_ifd.entries:
                    if entry.external_offset is not None:
                        pad_to(destination, entry.external_offset)
                        destination.write(entry.raw)

            first_data_offset = next(
                offset
                for output_ifd in output_ifds
                for offset in output_ifd.destination_chunk_offsets
                if offset
            )
            pad_to(destination, first_data_offset)
            notify("copy", 0, total_payload, "正在原样复制图像块…")
            last_notice = 0
            for output_ifd in output_ifds:
                for source_offset, destination_offset, byte_count in zip(
                    output_ifd.source.source_chunk_offsets,
                    output_ifd.destination_chunk_offsets,
                    output_ifd.source.chunk_bytecounts,
                ):
                    if byte_count == 0:
                        continue
                    pad_to(destination, destination_offset)
                    source_handle.seek(source_offset)
                    remaining = byte_count
                    while remaining:
                        data = read_exact(source_handle, min(COPY_BUFFER, remaining), "复制图像块")
                        destination.write(data)
                        source_payload_digest.update(data)
                        copied += len(data)
                        remaining -= len(data)
                        if copied - last_notice >= 32 * 1024 * 1024 or copied == total_payload:
                            notify("copy", copied, total_payload, f"已复制 {copied:,}/{total_payload:,} 字节")
                            last_notice = copied
            destination.flush()
            os.fsync(destination.fileno())
            if destination.tell() != predicted_size:
                raise ConversionError(
                    f"输出大小与计划不一致：实际 {destination.tell()}，预计 {predicted_size}"
                )

        if output_path.exists():
            output_path.unlink()
        os.replace(temp_path, output_path)
        temp_path = None

        source_payload_hash = source_payload_digest.hexdigest()
        if verify:
            notify("verify", 0, total_payload, "正在从磁盘回读并验证图像与元数据…")
            report = validate_conversion(source, output_path, source_payload_hash)
            notify("verify", total_payload, total_payload, f"验证完成：{report['status']}")
        else:
            report = {
                "status": "NOT_VERIFIED",
                "tool": {"name": APP_NAME, "version": APP_VERSION},
                "input": {"path": str(input_path), "format": source.variant, "size_bytes": source.size},
                "output": {
                    "path": str(output_path),
                    "format": "ClassicTIFF",
                    "size_bytes": output_path.stat().st_size,
                },
                "validation": {"raw_image_payload_sha256_input_during_copy": source_payload_hash},
            }
        report["elapsed_seconds"] = round(time.time() - start, 3)
        if report_path:
            report_path = report_path.resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if report["status"] == "FAIL":
            raise ConversionError("转换文件已生成，但验证失败；请勿用于生产，详见验证报告")
        return report
    except Exception:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


def default_output(input_path: Path) -> Path:
    return input_path.with_name(input_path.stem + "-classic.tif")


SUPPORTED_EXTENSIONS = {".tif", ".tiff", ".btf", ".tf8"}


def is_bigtiff_file(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            header = handle.read(4)
        return header in (b"II+\x00", b"MM\x00+")
    except OSError:
        return False


def scan_image_folder(folder: Path, recursive: bool) -> list[Path]:
    folder = folder.resolve()
    if not folder.is_dir():
        raise ConversionError(f"文件夹不存在：{folder}")
    iterator = folder.rglob("*") if recursive else folder.glob("*")
    return sorted(
        (
            path.resolve()
            for path in iterator
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
            and not path.stem.lower().endswith("-classic")
            and is_bigtiff_file(path)
        ),
        key=lambda path: str(path).lower(),
    )


def make_batch_outputs(inputs: list[Path], output_dir: Path) -> list[Path]:
    output_dir = output_dir.resolve()
    used: set[str] = set()
    outputs: list[Path] = []
    for input_path in inputs:
        base = input_path.stem + "-classic"
        candidate = output_dir / f"{base}.tif"
        key = candidate.name.lower()
        if key in used:
            suffix = hashlib.sha256(str(input_path.resolve()).encode("utf-8")).hexdigest()[:8]
            candidate = output_dir / f"{base}-{suffix}.tif"
            key = candidate.name.lower()
        counter = 2
        while key in used:
            candidate = output_dir / f"{base}-{counter}.tif"
            key = candidate.name.lower()
            counter += 1
        used.add(key)
        outputs.append(candidate)
    return outputs


def require_license(explicit_path: Path | None = None) -> LicenseInfo:
    info, error = find_valid_license(explicit_path)
    if info is None:
        raise LicenseError(error or "未找到有效许可证")
    return info


def convert_batch(
    inputs: list[Path],
    output_dir: Path,
    license_info: LicenseInfo,
    overwrite: bool = False,
    event_callback: Callable[[tuple], None] | None = None,
) -> tuple[dict, Path]:
    if "batch_conversion" not in license_info.features:
        raise LicenseError("当前许可证未授权批量转换")
    resolved_inputs = list(dict.fromkeys(path.resolve() for path in inputs))
    if not resolved_inputs:
        raise ConversionError("批量任务中没有输入文件")
    if (
        not license_info.unlimited_batch
        and len(resolved_inputs) > license_info.max_files_per_batch
    ):
        raise LicenseError(
            f"当前许可证单批最多处理 {license_info.max_files_per_batch} 个文件"
        )
    output_dir = output_dir.resolve()
    outputs = make_batch_outputs(resolved_inputs, output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    def emit(*event: object) -> None:
        if event_callback:
            event_callback(tuple(event))

    for index, (input_path, output_path) in enumerate(zip(resolved_inputs, outputs)):
        emit("file_start", str(input_path).lower(), index, len(resolved_inputs), input_path.name)

        def progress(phase: str, current: int, total: int, message: str) -> None:
            emit("progress", index, len(resolved_inputs), phase, current, total, message)

        report_path = output_path.with_suffix(output_path.suffix + ".validation.json")
        try:
            report = convert(input_path, output_path, report_path, overwrite, True, progress)
            results.append(
                {
                    "input": str(input_path),
                    "output": str(output_path),
                    "status": report["status"],
                    "report": str(report_path),
                }
            )
            emit("file_done", str(input_path).lower(), "PASS", str(output_path))
        except Exception as exc:
            results.append(
                {"input": str(input_path), "output": str(output_path), "status": "FAIL", "error": str(exc)}
            )
            emit("file_done", str(input_path).lower(), "FAIL", str(exc))

    summary = {
        "tool": {"name": APP_NAME, "version": APP_VERSION},
        "license": {
            "license_id": license_info.license_id,
            "customer": license_info.customer,
            "expires_at": license_info.expires_at,
        },
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(results),
        "passed": sum(item["status"] == "PASS" for item in results),
        "failed": sum(item["status"] != "PASS" for item in results),
        "results": results,
    }
    summary_path = output_dir / f"batch_validation_{time.strftime('%Y%m%d_%H%M%S')}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    emit("batch_done", summary, summary_path)
    return summary, summary_path


def run_cli(args: argparse.Namespace) -> int:
    try:
        license_info = require_license(Path(args.license) if args.license else None)
    except LicenseError as exc:
        print(f"许可证错误：{exc}", file=sys.stderr)
        print(f"本机设备码：{machine_code()}", file=sys.stderr)
        return 2
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else default_output(input_path)
    report_path = Path(args.report) if args.report else output_path.with_suffix(output_path.suffix + ".validation.json")

    def show_progress(phase: str, current: int, total: int, message: str) -> None:
        percent = (current / total * 100) if total else 0
        print(f"[{phase:7}] {percent:6.2f}%  {message}", flush=True)

    try:
        report = convert(
            input_path,
            output_path,
            report_path,
            overwrite=args.overwrite,
            verify=not args.no_verify,
            progress=show_progress,
        )
    except (ConversionError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    print(f"完成：{output_path.resolve()}")
    print(f"验证：{report['status']}")
    print(f"报告：{report_path.resolve()}")
    print(f"授权：{license_info.customer}，有效期至 {license_info.expires_at}")
    return 0


def run_batch_cli(args: argparse.Namespace) -> int:
    try:
        license_info = require_license(Path(args.license) if args.license else None)
        inputs = scan_image_folder(Path(args.folder), args.recursive)
        if not inputs:
            raise ConversionError("文件夹中没有找到TIFF类文件")
        output_dir = Path(args.output_dir) if args.output_dir else Path(args.folder) / "ClassicTIFF_Output"

        def show_event(event: tuple) -> None:
            if event[0] == "file_start":
                _, _, index, total, name = event
                print(f"\n[{index + 1}/{total}] {name}", flush=True)
            elif event[0] == "progress":
                _, _, _, phase, current, total, message = event
                percent = current / total * 100 if total else 0
                print(f"  [{phase:7}] {percent:6.2f}% {message}", flush=True)
            elif event[0] == "file_done":
                _, _, status, detail = event
                print(f"  {status}: {detail}", flush=True)

        summary, summary_path = convert_batch(
            inputs,
            output_dir,
            license_info,
            overwrite=args.overwrite,
            event_callback=show_event,
        )
        print(
            f"\n批量完成：成功 {summary['passed']}，失败 {summary['failed']}，报告 {summary_path.resolve()}"
        )
        return 0 if summary["failed"] == 0 else 1
    except (LicenseError, ConversionError, OSError) as exc:
        print(f"批量转换失败：{exc}", file=sys.stderr)
        return 2


def run_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title(f"{APP_NAME} v{APP_VERSION}")
    root.geometry("980x700")
    root.minsize(860, 620)

    output_var = tk.StringVar()
    status_var = tk.StringVar(value="请添加BigTIFF文件或文件夹。原文件不会被覆盖。")
    license_var = tk.StringVar(value="正在检查许可证…")
    recursive_var = tk.BooleanVar(value=True)
    overwrite_var = tk.BooleanVar(value=False)
    event_queue: queue.Queue[tuple] = queue.Queue()
    selected_files: dict[str, Path] = {}
    current_license: LicenseInfo | None = None

    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=APP_NAME, font=("Microsoft YaHei UI", 16, "bold")).grid(
        row=0, column=0, columnspan=6, sticky="w", pady=(0, 8)
    )
    ttk.Label(
        frame,
        text="批量将BigTIFF(版本43)安全转换为Classic TIFF(版本42)，原样复制图像块并逐文件验证。",
    ).grid(row=1, column=0, columnspan=6, sticky="w", pady=(0, 10))

    license_box = ttk.LabelFrame(frame, text="许可证", padding=10)
    license_box.grid(row=2, column=0, columnspan=6, sticky="ew", pady=(0, 12))
    ttk.Label(license_box, textvariable=license_var).grid(row=0, column=0, sticky="w")

    def copy_machine() -> None:
        code = machine_code()
        root.clipboard_clear()
        root.clipboard_append(code)
        status_var.set(f"设备码已复制：{code}")

    def refresh_license() -> None:
        nonlocal current_license
        info, error = find_valid_license()
        current_license = info
        if info:
            bind_text = "通用" if info.machine_id == "*" else "本机绑定"
            license_var.set(
                f"已授权：{info.customer}｜有效期至 {info.expires_at}｜{bind_text}｜单批上限 {info.batch_limit_text}"
            )
        else:
            license_var.set(f"未授权：{error}｜本机设备码 {machine_code()}")
        update_start_state()

    def choose_license() -> None:
        filename = filedialog.askopenfilename(
            title="安装许可证", filetypes=[("许可证", "*.lic"), ("所有文件", "*.*")]
        )
        if not filename:
            return
        try:
            info, destination = install_license(Path(filename))
            messagebox.showinfo(
                APP_NAME,
                f"许可证安装成功\n\n客户：{info.customer}\n有效期：{info.expires_at}\n位置：{destination}",
            )
            refresh_license()
        except (LicenseError, OSError) as exc:
            messagebox.showerror(APP_NAME, str(exc))

    ttk.Button(license_box, text="复制本机设备码", command=copy_machine).grid(row=0, column=1, padx=(10, 4))
    ttk.Button(license_box, text="安装许可证…", command=choose_license).grid(row=0, column=2)
    license_box.columnconfigure(0, weight=1)

    toolbar = ttk.Frame(frame)
    toolbar.grid(row=3, column=0, columnspan=6, sticky="ew", pady=(0, 8))

    def add_paths(paths: Iterable[str | Path]) -> int:
        added = 0
        for raw_path in paths:
            path = Path(raw_path).resolve()
            if (
                path.suffix.lower() not in SUPPORTED_EXTENSIONS
                or not path.is_file()
                or not is_bigtiff_file(path)
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
            output_var.set(str(first.parent / "ClassicTIFF_Output"))
        status_var.set(f"已选择 {len(selected_files)} 个影像文件。")
        update_start_state()
        return added

    def choose_files() -> None:
        filenames = filedialog.askopenfilenames(
            title="批量选择BigTIFF",
            filetypes=[("TIFF影像", "*.tif *.tiff *.btf *.tf8"), ("所有文件", "*.*")],
        )
        if filenames:
            add_paths(filenames)

    def choose_folder() -> None:
        folder = filedialog.askdirectory(title="选择包含BigTIFF的文件夹")
        if not folder:
            return
        try:
            paths = scan_image_folder(Path(folder), recursive_var.get())
            added = add_paths(paths)
            if added:
                output_var.set(str(Path(folder).resolve() / "ClassicTIFF_Output"))
            status_var.set(f"扫描到 {len(paths)} 个TIFF类文件，新增 {added} 个。")
        except (ConversionError, OSError) as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def remove_selected() -> None:
        for iid in tree.selection():
            selected_files.pop(iid, None)
            tree.delete(iid)
        status_var.set(f"已选择 {len(selected_files)} 个影像文件。")
        update_start_state()

    def clear_all() -> None:
        selected_files.clear()
        for iid in tree.get_children():
            tree.delete(iid)
        status_var.set("已清空文件列表。")
        update_start_state()

    ttk.Button(toolbar, text="添加文件…", command=choose_files).pack(side="left", padx=(0, 6))
    ttk.Button(toolbar, text="添加文件夹…", command=choose_folder).pack(side="left", padx=6)
    ttk.Button(toolbar, text="移除选中", command=remove_selected).pack(side="left", padx=6)
    ttk.Button(toolbar, text="清空", command=clear_all).pack(side="left", padx=6)
    ttk.Checkbutton(toolbar, text="递归扫描子文件夹", variable=recursive_var).pack(side="left", padx=(18, 6))

    tree_frame = ttk.Frame(frame)
    tree_frame.grid(row=4, column=0, columnspan=6, sticky="nsew")
    tree = ttk.Treeview(tree_frame, columns=("name", "folder", "status"), show="headings", selectmode="extended")
    tree.heading("name", text="文件名")
    tree.heading("folder", text="所在文件夹")
    tree.heading("status", text="状态")
    tree.column("name", width=240, anchor="w")
    tree.column("folder", width=500, anchor="w")
    tree.column("status", width=100, anchor="center")
    scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    ttk.Label(frame, text="输出文件夹：").grid(row=5, column=0, sticky="w", pady=(10, 6))
    output_entry = ttk.Entry(frame, textvariable=output_var)
    output_entry.grid(row=5, column=1, columnspan=3, sticky="ew", padx=8, pady=(10, 6))

    def choose_output() -> None:
        folder = filedialog.askdirectory(title="选择批量输出文件夹")
        if folder:
            output_var.set(folder)
            update_start_state()

    ttk.Button(frame, text="选择…", command=choose_output).grid(row=5, column=4, sticky="ew", pady=(10, 6))
    ttk.Checkbutton(frame, text="替换同名输出", variable=overwrite_var).grid(
        row=5, column=5, sticky="w", padx=(10, 0), pady=(10, 6)
    )

    current_progress = ttk.Progressbar(frame, mode="determinate", maximum=100)
    current_progress.grid(row=6, column=0, columnspan=6, sticky="ew", pady=(10, 3))
    overall_progress = ttk.Progressbar(frame, mode="determinate", maximum=100)
    overall_progress.grid(row=7, column=0, columnspan=6, sticky="ew", pady=3)
    ttk.Label(frame, textvariable=status_var).grid(row=8, column=0, columnspan=6, sticky="w", pady=(3, 6))
    log = tk.Text(frame, height=7, wrap="word", state="disabled", font=("Consolas", 9))
    log.grid(row=9, column=0, columnspan=6, sticky="nsew", pady=(4, 8))

    def append_log(text: str) -> None:
        log.configure(state="normal")
        log.insert("end", text + "\n")
        log.see("end")
        log.configure(state="disabled")

    def worker(inputs: list[Path], output_dir: Path, overwrite: bool, license_info: LicenseInfo) -> None:
        try:
            convert_batch(
                inputs,
                output_dir,
                license_info,
                overwrite=overwrite,
                event_callback=event_queue.put,
            )
        except Exception as exc:
            event_queue.put(("fatal_error", str(exc)))

    def start_conversion() -> None:
        if current_license is None:
            messagebox.showerror(APP_NAME, "请先安装有效许可证。")
            return
        if not selected_files:
            messagebox.showerror(APP_NAME, "请至少添加一个输入影像。")
            return
        if not output_var.get().strip():
            messagebox.showerror(APP_NAME, "请选择批量输出文件夹。")
            return
        if (
            not current_license.unlimited_batch
            and len(selected_files) > current_license.max_files_per_batch
        ):
            messagebox.showerror(
                APP_NAME,
                f"当前许可证单批最多处理 {current_license.max_files_per_batch} 个文件。",
            )
            return
        inputs = list(selected_files.values())
        output_dir = Path(output_var.get().strip())
        start_button.configure(state="disabled")
        current_progress["value"] = 0
        overall_progress["value"] = 0
        status_var.set(f"正在开始批量转换，共 {len(inputs)} 个文件…")
        append_log(f"输出目录：{output_dir}")
        threading.Thread(
            target=worker,
            args=(inputs, output_dir, overwrite_var.get(), current_license),
            daemon=True,
        ).start()

    start_button = ttk.Button(frame, text="开始批量安全转换", command=start_conversion, state="disabled")
    start_button.grid(row=10, column=5, sticky="e", pady=(4, 0))
    ttk.Label(
        frame,
        text="限制：Classic TIFF小于4 GiB；遇到无法无损表达的结构会停止，不会生成伪成功文件。",
        foreground="#555555",
    ).grid(row=10, column=0, columnspan=5, sticky="w", pady=(4, 0))

    for column in (1, 2, 3):
        frame.columnconfigure(column, weight=1)
    frame.rowconfigure(4, weight=3)
    frame.rowconfigure(9, weight=1)

    def update_start_state() -> None:
        enabled = current_license is not None and bool(selected_files) and bool(output_var.get().strip())
        start_button.configure(state="normal" if enabled else "disabled")

    def poll_events() -> None:
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
                    status_var.set(f"正在处理 {index + 1}/{total_files}：{name}")
                elif event[0] == "progress":
                    _, index, file_count, phase, current, total, message = event
                    fraction = (current / total) if total else 0
                    current_progress["value"] = fraction * 100
                    overall_progress["value"] = ((index + fraction) / file_count) * 100
                    status_var.set(message)
                    if phase in ("inspect", "verify") or current == total:
                        append_log(message)
                elif event[0] == "file_done":
                    _, key, result, detail = event
                    if tree.exists(key):
                        values = list(tree.item(key, "values"))
                        values[2] = result
                        tree.item(key, values=values)
                    append_log(f"{result}：{detail}")
                elif event[0] == "batch_done":
                    _, summary, summary_path = event
                    start_button.configure(state="normal")
                    current_progress["value"] = 100
                    overall_progress["value"] = 100
                    status_var.set(
                        f"批量完成：成功 {summary['passed']}，失败 {summary['failed']}，共 {summary['total']}。"
                    )
                    append_log(f"批量报告：{summary_path}")
                    messagebox.showinfo(
                        APP_NAME,
                        f"批量转换完成\n\n成功：{summary['passed']}\n失败：{summary['failed']}\n报告：{summary_path}",
                    )
                elif event[0] == "fatal_error":
                    start_button.configure(state="normal")
                    status_var.set("批量转换未能启动。")
                    append_log("错误：" + event[1])
                    messagebox.showerror(APP_NAME, event[1])
        except queue.Empty:
            pass
        root.after(150, poll_events)

    output_var.trace_add("write", lambda *_: update_start_state())
    refresh_license()
    root.after(150, poll_events)
    root.mainloop()
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将小于4 GiB且可无损表示的BigTIFF转换为Classic TIFF。无参数启动图形界面。"
    )
    parser.add_argument("input", nargs="?", help="输入BigTIFF路径")
    parser.add_argument("-o", "--output", help="输出Classic TIFF路径")
    parser.add_argument("--folder", help="批量扫描输入文件夹")
    parser.add_argument("--output-dir", help="批量输出文件夹")
    parser.add_argument("--recursive", action="store_true", help="递归扫描输入文件夹")
    parser.add_argument("--report", help="JSON验证报告路径")
    parser.add_argument("--license", help="临时指定许可证路径")
    parser.add_argument("--machine-code", action="store_true", help="显示本机设备码")
    parser.add_argument("--install-license", help="验证并安装许可证")
    parser.add_argument("--overwrite", action="store_true", help="允许替换已存在的输出文件")
    parser.add_argument("--no-verify", action="store_true", help="跳过回读验证（不推荐）")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    return parser


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    if args.machine_code:
        print(machine_code())
        return 0
    if args.install_license:
        try:
            info, destination = install_license(Path(args.install_license))
            print(f"许可证已安装：{destination}")
            print(f"客户：{info.customer}；有效期至：{info.expires_at}")
            return 0
        except (LicenseError, OSError) as exc:
            print(f"许可证安装失败：{exc}", file=sys.stderr)
            return 2
    if args.folder:
        return run_batch_cli(args)
    if args.input:
        return run_cli(args)
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
