"""Read an explicitly supported EXIF subset without following opaque pointers."""
from pathlib import Path
import struct
import tile_core as core

# Separate parser instance keeps the other conversion modes strict.
core.POINTER_TAGS.discard(34665)
SUPPORTED_EXIF = {40961: (3, 1), 40962: (4, 1), 40963: (4, 1)}
SUPPORTED_MAIN = {254, 256, 257, 258, 259, 262, 273, 274, 277, 278, 279,
                  282, 283, 284, 286, 287, 296, 305, 306, 322, 323, 324, 325,
                  339, 340, 341, 700, 34264, 34377, 34665, 34735, 34736, 34737}

def parse(path):
    p = core.parse_tiff(Path(path), False)
    if len(p.ifds) != 1:
        raise ValueError('瓦片模式仅支持单页 TIFF')
    page = p.ifds[0]
    if len({x.tag for x in page.entries}) != len(page.entries):
        raise ValueError('存在重复标签')
    unknown = sorted({x.tag for x in page.entries} - SUPPORTED_MAIN)
    if unknown:
        raise ValueError(f'标签超出瓦片模式支持范围：{unknown}')
    ptrs = [x for x in page.entries if x.tag == 34665]
    if not ptrs:
        return p, ()
    if len(ptrs) != 1:
        raise ValueError('不支持多个 EXIF 目录')
    ptr = ptrs[0]
    fmt = {4:'I', 13:'I', 16:'Q', 18:'Q'}.get(ptr.type_id)
    if ptr.count != 1 or not fmt:
        raise ValueError('EXIF 指针类型不支持')
    pos = struct.unpack(p.endian + fmt, ptr.raw)[0]
    count_len, count_fmt, entry_len, entry_fmt, next_fmt = ((2,'H',12,'HHII','I') if p.variant == 'ClassicTIFF' else (8,'Q',20,'HHQQ','Q'))
    with p.path.open('rb') as f:
        if pos < 8 or pos + count_len > p.size:
            raise ValueError('EXIF 指针越界')
        f.seek(pos)
        count = struct.unpack(p.endian+count_fmt, core.read_exact(f,count_len,'EXIF标签数'))[0]
        if count > len(SUPPORTED_EXIF) or pos + count_len + count * entry_len + struct.calcsize(next_fmt) > p.size:
            raise ValueError('EXIF 目录结构超出支持范围')
        exif = []
        for _ in range(count):
            record = core.read_exact(f,entry_len,'EXIF标签')
            tag, typ, n, _ = struct.unpack(p.endian+entry_fmt,record)
            if (typ,n) != SUPPORTED_EXIF.get(tag):
                raise ValueError(f'EXIF 标签 {tag} 不受支持')
            nbytes = core.TYPE_SIZES[typ] * n
            raw = record[8:12][:nbytes] if p.variant == 'ClassicTIFF' else record[12:20][:nbytes]
            exif.append((tag, typ, n, raw))
        nxt = struct.unpack(p.endian+next_fmt,core.read_exact(f,struct.calcsize(next_fmt),'EXIF后续指针'))[0]
        if nxt or len({x[0] for x in exif}) != len(exif):
            raise ValueError('EXIF 目录有额外结构或重复标签')
    return p, tuple(exif)
