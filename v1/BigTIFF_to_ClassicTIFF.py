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


APP_NAME = "BigTIFF 转普通 TIFF 工具"
APP_VERSION = "1.0.0"
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


def run_cli(args: argparse.Namespace) -> int:
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
    return 0


def run_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title(f"{APP_NAME} v{APP_VERSION}")
    root.geometry("760x470")
    root.minsize(680, 420)

    input_var = tk.StringVar()
    output_var = tk.StringVar()
    status_var = tk.StringVar(value="请选择BigTIFF文件。原文件不会被覆盖。")
    event_queue: queue.Queue[tuple] = queue.Queue()

    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=APP_NAME, font=("Microsoft YaHei UI", 16, "bold")).grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 12)
    )
    ttk.Label(
        frame,
        text="将BigTIFF(版本43)安全转换为Classic TIFF(版本42)，原样复制图像块并验证元数据。",
    ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 16))

    ttk.Label(frame, text="输入BigTIFF：").grid(row=2, column=0, sticky="w", pady=6)
    input_entry = ttk.Entry(frame, textvariable=input_var)
    input_entry.grid(row=2, column=1, sticky="ew", padx=8)
    ttk.Label(frame, text="输出普通TIFF：").grid(row=3, column=0, sticky="w", pady=6)
    output_entry = ttk.Entry(frame, textvariable=output_var)
    output_entry.grid(row=3, column=1, sticky="ew", padx=8)

    def choose_input() -> None:
        filename = filedialog.askopenfilename(
            title="选择BigTIFF",
            filetypes=[("TIFF影像", "*.tif *.tiff *.btf *.tf8"), ("所有文件", "*.*")],
        )
        if filename:
            input_var.set(filename)
            output_var.set(str(default_output(Path(filename))))

    def choose_output() -> None:
        filename = filedialog.asksaveasfilename(
            title="保存普通TIFF",
            defaultextension=".tif",
            filetypes=[("TIFF影像", "*.tif"), ("所有文件", "*.*")],
            initialfile=Path(output_var.get()).name if output_var.get() else "output-classic.tif",
        )
        if filename:
            output_var.set(filename)

    ttk.Button(frame, text="浏览…", command=choose_input).grid(row=2, column=2, sticky="ew")
    ttk.Button(frame, text="浏览…", command=choose_output).grid(row=3, column=2, sticky="ew")

    progress_bar = ttk.Progressbar(frame, mode="determinate", maximum=100)
    progress_bar.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(18, 6))
    ttk.Label(frame, textvariable=status_var).grid(row=5, column=0, columnspan=3, sticky="w")
    log = tk.Text(frame, height=10, wrap="word", state="disabled", font=("Consolas", 9))
    log.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(10, 10))

    def append_log(text: str) -> None:
        log.configure(state="normal")
        log.insert("end", text + "\n")
        log.see("end")
        log.configure(state="disabled")

    def worker(input_path: Path, output_path: Path, overwrite: bool) -> None:
        def progress(phase: str, current: int, total: int, message: str) -> None:
            event_queue.put(("progress", phase, current, total, message))

        try:
            report_path = output_path.with_suffix(output_path.suffix + ".validation.json")
            report = convert(input_path, output_path, report_path, overwrite, True, progress)
            event_queue.put(("done", report, report_path))
        except Exception as exc:
            event_queue.put(("error", str(exc)))

    def start_conversion() -> None:
        if not input_var.get().strip() or not output_var.get().strip():
            messagebox.showerror(APP_NAME, "请选择输入文件和输出位置。")
            return
        input_path = Path(input_var.get().strip())
        output_path = Path(output_var.get().strip())
        if input_path.resolve() == output_path.resolve():
            messagebox.showerror(APP_NAME, "输出不能覆盖原文件。")
            return
        overwrite = False
        if output_path.exists():
            overwrite = messagebox.askyesno(APP_NAME, f"输出文件已经存在，是否替换？\n\n{output_path}")
            if not overwrite:
                return
        start_button.configure(state="disabled")
        progress_bar["value"] = 0
        status_var.set("正在开始转换…")
        append_log(f"输入：{input_path}")
        append_log(f"输出：{output_path}")
        threading.Thread(target=worker, args=(input_path, output_path, overwrite), daemon=True).start()

    start_button = ttk.Button(frame, text="开始安全转换", command=start_conversion)
    start_button.grid(row=7, column=2, sticky="e", pady=(4, 0))
    ttk.Label(
        frame,
        text="限制：Classic TIFF小于4 GiB；遇到无法无损表达的结构会停止，不会生成伪成功文件。",
        foreground="#555555",
    ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(4, 0))

    frame.columnconfigure(1, weight=1)
    frame.rowconfigure(6, weight=1)

    def poll_events() -> None:
        try:
            while True:
                event = event_queue.get_nowait()
                if event[0] == "progress":
                    _, phase, current, total, message = event
                    progress_bar["value"] = (current / total * 100) if total else 0
                    status_var.set(message)
                    if phase in ("inspect", "verify") or current == total:
                        append_log(message)
                elif event[0] == "done":
                    _, report, report_path = event
                    start_button.configure(state="normal")
                    progress_bar["value"] = 100
                    status_var.set("转换及验证完成：PASS")
                    append_log(f"图像块SHA-256：{report['validation']['raw_image_payload_sha256_output']}")
                    append_log(f"验证报告：{report_path}")
                    messagebox.showinfo(APP_NAME, "转换完成，像素数据和非结构性元数据验证一致。")
                elif event[0] == "error":
                    start_button.configure(state="normal")
                    status_var.set("转换失败或被安全检查阻止。")
                    append_log("错误：" + event[1])
                    messagebox.showerror(APP_NAME, event[1])
        except queue.Empty:
            pass
        root.after(150, poll_events)

    root.after(150, poll_events)
    root.mainloop()
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将小于4 GiB且可无损表示的BigTIFF转换为Classic TIFF。无参数启动图形界面。"
    )
    parser.add_argument("input", nargs="?", help="输入BigTIFF路径")
    parser.add_argument("-o", "--output", help="输出Classic TIFF路径")
    parser.add_argument("--report", help="JSON验证报告路径")
    parser.add_argument("--overwrite", action="store_true", help="允许替换已存在的输出文件")
    parser.add_argument("--no-verify", action="store_true", help="跳过回读验证（不推荐）")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    return parser


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    if args.input:
        return run_cli(args)
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
