"""Convert uncompressed RGB strips to a minimal classic tiled TIFF profile."""
from pathlib import Path
import hashlib
import os
import struct
import tempfile
import tile_core as core
import safe_exif as ex
import retile

def convert(source, target, progress=lambda s: None, nodata_zero=False, reference=None):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or target.exists():
        raise ValueError('不能覆盖输入文件或现有输出')
    p, exif = ex.parse(source)
    if reference is None:
        raise ValueError('请选择参考 TIFF，程序将核对格式和文件大小')
    reference = Path(reference).resolve()
    if reference == target:
        raise ValueError('输出文件不能覆盖参考 TIFF')
    ref_before = reference.stat()
    ref, ref_exif = ex.parse(reference)
    if ref.variant != 'ClassicTIFF' or ref.ifds[0].data_offsets_tag != 324 or ref_exif:
        raise ValueError('参考文件不是受支持的普通瓦片 TIFF 格式')
    if ref.byte_order != p.byte_order:
        raise ValueError('输入与参考文件的字节序不同')
    ref_tags = {item.tag: item for item in ref.ifds[0].entries}
    if p.variant != 'ClassicTIFF':
        raise ValueError('输入必须是普通 TIFF')
    page = p.ifds[0]
    tags = {item.tag: item for item in page.entries}
    endian = p.endian
    for tag, value in ((259, 1), (262, 2), (277, 3), (284, 1)):
        if tag not in tags or retile.number(tags[tag], endian) != value:
            raise ValueError(f'不支持的影像属性：标签 {tag}')
    bits = tags.get(258)
    if bits is None or bits.type_id != 3 or bits.count != 3 or struct.unpack(endian+'HHH', bits.raw) != (8, 8, 8):
        raise ValueError('仅支持每通道 8 位 RGB')
    sample = tags.get(339)
    if sample and (sample.type_id != 3 or sample.count not in (1, 3) or
                   any(x != 1 for x in struct.unpack(endian+'H'*sample.count, sample.raw))):
        raise ValueError('仅支持无符号整数样本')
    if page.data_offsets_tag != 273:
        raise ValueError('仅支持条带存储的输入')
    width, height = retile.number(tags[256], endian), retile.number(tags[257], endian)
    # TIFF 6.0 defaults an absent RowsPerStrip to 2**32 - 1 (one strip).
    rows_per_strip = retile.number(tags[278], endian) if 278 in tags else 0xFFFFFFFF
    if width <= 0 or height <= 0 or rows_per_strip <= 0 or width*3*256 > 128*1024*1024:
        raise ValueError('影像尺寸或条带参数超出安全范围')
    strip_count = (height + rows_per_strip - 1)//rows_per_strip
    expected_lengths = [min(rows_per_strip, height-i*rows_per_strip)*width*3 for i in range(strip_count)]
    if len(page.chunk_bytecounts) != strip_count or page.chunk_bytecounts != expected_lengths:
        raise ValueError('条带长度不符合无压缩 RGB 结构')
    cols, rows = (width+255)//256, (height+255)//256
    count, tilebytes = cols*rows, 256*256*3
    if count > 1000000:
        raise ValueError('瓦片数量超出安全范围')
    geo_tags = {33550, 33922, 34264, 34735, 34736, 34737}
    if geo_tags.intersection(tags):
        raise ValueError('输入带有空间定位标签；精简格式模式拒绝丢弃定位信息')
    if 42113 in tags and (not nodata_zero or (tags[42113].type_id,tags[42113].count,tags[42113].raw) != (2,2,b'0\0')):
        raise ValueError('输入的 NoData 与输出设置不同，已停止')
    entries = []
    for tag in (256, 257, 258, 259, 262, 277, 284):
        item = tags[tag]
        if tag in (256, 257) and retile.number(item, endian) <= 65535:
            entries.append([tag, 3, 1, struct.pack(endian+'H', retile.number(item, endian)), None])
        else:
            entries.append([tag, item.type_id, item.count, item.raw, None])
    entries.extend([[322, 3, 1, struct.pack(endian+'H', 256), None],
                    [323, 3, 1, struct.pack(endian+'H', 256), None],
                    [324, 4, count, b'\0'*(count*4), None],
                    [325, 4, count, struct.pack(endian+'I'*count, *([tilebytes]*count)), None]])
    entries.append([339, 3, 3, struct.pack(endian+'HHH', 1, 1, 1), None])
    # Pixel-space default; never copied from another image or claimed as a geographic CRS.
    entries.append([33550, 12, 3, struct.pack(endian+'ddd', 1.0, 1.0, 0.0), None])
    entries.append([33922, 12, 6, struct.pack(endian+'dddddd', 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), None])
    if nodata_zero:
        entries.append([42113, 2, 2, b'0\0', None])
    entries.sort(key=lambda item: item[0])
    if set(ref_tags) != {item[0] for item in entries}:
        raise ValueError('参考文件的标签结构不受支持')
    for tag, typ, n, raw, _ in entries:
        if tag in (324, 325):
            continue
        ref_tag = ref_tags[tag]
        if (ref_tag.type_id, ref_tag.count, ref_tag.raw) != (typ, n, raw):
            raise ValueError(f'输入与参考文件的格式参数不一致：标签 {tag}')
    cursor = 8 + 2 + 12*len(entries) + 4
    for item in entries:
        if len(item[3]) > 4:
            cursor = core.align(cursor, 2)
            item[4] = cursor
            cursor += len(item[3])
    cursor = core.align(cursor, 2)
    if cursor + count*tilebytes > 0xffffffff:
        raise ValueError('输出超出普通 TIFF 的 4 GiB 偏移范围；请选择 BigTIFF')
    if cursor + count*tilebytes != ref_before.st_size:
        raise ValueError('预计输出字节数与参考文件不同；已停止')
    offsets = [cursor + i*tilebytes for i in range(count)]
    next(item for item in entries if item[0] == 324)[3] = struct.pack(endian+'I'*count, *offsets)
    before = source.stat()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.tiled_', suffix='.partial', dir=target.parent)
    tmp = Path(temporary)
    try:
        with os.fdopen(fd, 'wb') as out, source.open('rb') as inp:
            out.write(p.byte_order + struct.pack(endian+'HI', 42, 8))
            out.write(struct.pack(endian+'H', len(entries)))
            for tag, typ, n, raw, offset in entries:
                out.write(struct.pack(endian+'HHI', tag, typ, n))
                out.write(struct.pack(endian+'I', offset) if offset is not None else raw.ljust(4, b'\0'))
            out.write(struct.pack(endian+'I', 0))
            for tag, typ, n, raw, offset in entries:
                if offset is not None:
                    core.pad_to(out, offset)
                    out.write(raw)
            source_hash = hashlib.sha256()
            for ty in range(rows):
                height_in_tile = min(256, height-ty*256)
                image_rows = []
                for j in range(height_in_tile):
                    y = ty*256+j
                    inp.seek(page.source_chunk_offsets[y//rows_per_strip] + (y % rows_per_strip)*width*3)
                    data = core.read_exact(inp, width*3, '源像素行')
                    image_rows.append(data)
                    source_hash.update(data)
                for tx in range(cols):
                    tile = bytearray(tilebytes)
                    tile_width = min(256, width-tx*256)*3
                    for j, row in enumerate(image_rows):
                        tile[j*768:j*768+tile_width] = row[tx*768:tx*768+tile_width]
                    core.pad_to(out, offsets[ty*cols+tx])
                    out.write(tile)
                if ty % 5 == 0:
                    progress(f'正在重排瓦片：{ty+1}/{rows}')
            out.flush()
            os.fsync(out.fileno())
        result, result_exif = ex.parse(tmp)
        out_tags = {item.tag: item for item in result.ifds[0].entries}
        expected_tags = {256,257,258,259,262,277,284,322,323,324,325,339,33550,33922}
        if nodata_zero:
            expected_tags.add(42113)
        old = {tag:(tags[tag].type_id,tags[tag].count,tags[tag].raw) for tag in (258,259,262,277,284)}
        new = {tag:(out_tags[tag].type_id,out_tags[tag].count,out_tags[tag].raw) for tag in old if tag in out_tags}
        if (result.variant != 'ClassicTIFF' or old != new or result_exif or set(out_tags) != expected_tags or
            retile.number(out_tags[256], endian) != width or retile.number(out_tags[257], endian) != height or
            retile.number(out_tags[322], endian) != 256 or retile.number(out_tags[323], endian) != 256 or
            out_tags[339].raw != struct.pack(endian+'HHH',1,1,1) or
            out_tags[33550].raw != struct.pack(endian+'ddd',1.0,1.0,0.0) or
            out_tags[33922].raw != struct.pack(endian+'dddddd',0.0,0.0,0.0,0.0,0.0,0.0) or
            result.ifds[0].chunk_bytecounts != [tilebytes]*count):
            raise ValueError('输出结构或标签回读不一致')
        if (ref_tags[324].type_id, ref_tags[324].count) != (out_tags[324].type_id, out_tags[324].count):
            raise ValueError('输出与参考文件的瓦片索引类型或数量不同')
        for tag in expected_tags - {324}:
            ref_tag, out_tag = ref_tags[tag], out_tags[tag]
            if (ref_tag.type_id, ref_tag.count, ref_tag.raw) != (out_tag.type_id, out_tag.count, out_tag.raw):
                raise ValueError(f'输出与参考文件格式不一致：标签 {tag}')
        if tmp.stat().st_size != ref_before.st_size:
            raise ValueError('输出字节数与参考文件不同')
        if nodata_zero and (out_tags[42113].type_id, out_tags[42113].raw) != (2, b'0\0'):
            raise ValueError('NoData 设置回读不一致')
        output_hash = hashlib.sha256()
        with tmp.open('rb') as check:
            for ty in range(rows):
                height_in_tile = min(256, height-ty*256)
                tile_rows = []
                for tx in range(cols):
                    check.seek(result.ifds[0].source_chunk_offsets[ty*cols+tx])
                    tile_rows.append(core.read_exact(check, tilebytes, '瓦片回读'))
                for j in range(height_in_tile):
                    for tx, tile in enumerate(tile_rows):
                        n = min(256, width-tx*256)*3
                        output_hash.update(tile[j*768:j*768+n])
                if ty % 5 == 0:
                    progress(f'正在核验像素：{ty+1}/{rows}')
        if source_hash.hexdigest() != output_hash.hexdigest():
            raise ValueError('输出像素与输入不一致')
        after = source.stat()
        if (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns):
            raise ValueError('源文件在转换期间发生变化')
        ref_after = reference.stat()
        if (ref_before.st_size, ref_before.st_mtime_ns) != (ref_after.st_size, ref_after.st_mtime_ns):
            raise ValueError('参考文件在转换期间发生变化')
        if os.name != 'nt':
            raise ValueError('本程序仅支持 Windows')
        os.rename(tmp, target)
        return {'status':'PASS','pixels_sha256':source_hash.hexdigest(),
                'pixel_hash_output':output_hash.hexdigest(),'metadata_sha256':retile.metadata_hash(new,()),
                'metadata_profile':'minimal_pixel_space','nodata_zero':nodata_zero,'reference_size_equal':True}
    finally:
        if tmp.exists():
            tmp.unlink()
