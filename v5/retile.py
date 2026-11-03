"""Losslessly retile supported uncompressed RGB scanlines into BigTIFF."""
from pathlib import Path
import hashlib, json, os, struct, tempfile, time
import tile_core as core
import safe_exif as ex

def number(entry,endian):
    if entry.count!=1 or entry.type_id not in (3,4): raise ValueError('不支持的单值标签')
    return struct.unpack(endian+('H' if entry.type_id==3 else 'I'),entry.raw)[0]

def metadata_hash(entries, exif):
    digest=hashlib.sha256()
    for tag,(typ,count,raw) in sorted(entries.items()):
        digest.update(struct.pack('>HHQ',tag,typ,count));digest.update(raw)
    for tag,typ,count,raw in exif:
        digest.update(struct.pack('>HHQ',tag,typ,count));digest.update(raw)
    return digest.hexdigest()

def source_pixels_sha256(path):
    p,_=ex.parse(path);page=p.ifds[0];digest=hashlib.sha256()
    with p.path.open('rb') as f:
        for offset,n in zip(page.source_chunk_offsets,page.chunk_bytecounts):
            f.seek(offset);remaining=n
            while remaining:
                data=core.read_exact(f,min(core.COPY_BUFFER,remaining),'源图像');digest.update(data);remaining-=len(data)
    return digest.hexdigest()

def convert(source,target,progress=lambda s:None):
    source,target=Path(source).resolve(),Path(target).resolve()
    if source==target or target.exists(): raise ValueError('不能覆盖输入文件或现有输出')
    p,exif=ex.parse(source)
    if p.variant!='ClassicTIFF': raise ValueError('输入必须是普通 TIFF')
    page=p.ifds[0];tags={x.tag:x for x in page.entries}; e=p.endian
    for tag,val in ((259,1),(262,2),(277,3),(278,1),(284,1)):
        if tag not in tags or number(tags[tag],e)!=val: raise ValueError(f'不支持该文件的标签 {tag}')
    if tags[258].type_id!=3 or tags[258].count!=3 or struct.unpack(e+'HHH',tags[258].raw)!=(8,8,8):
        raise ValueError('仅支持每通道 8 位 RGB')
    if 339 in tags:
        sample=tags[339]
        if sample.type_id!=3 or sample.count not in (1,3) or any(v!=1 for v in struct.unpack(e+'H'*sample.count,sample.raw)):
            raise ValueError('仅支持无符号整数样本')
    w,h=number(tags[256],e),number(tags[257],e)
    if page.data_offsets_tag!=273 or len(page.chunk_bytecounts)!=h or any(n!=w*3 for n in page.chunk_bytecounts):
        raise ValueError('仅支持逐行无压缩条带')
    if w <= 0 or h <= 0 or w*3*256 > 128*1024*1024:
        raise ValueError('图像尺寸超出安全处理范围')
    cols=(w+255)//256;rows=(h+255)//256;count=cols*rows;tilebytes=256*256*3
    if count > 1000000: raise ValueError('瓦片数超出安全处理范围')
    entries=[]
    for item in page.entries:
        if item.tag in (273,278,279):continue
        if item.tag==34665:entries.append([34665,4,1,b'\0'*4,None])
        else:entries.append([item.tag,item.type_id,item.count,item.raw,None])
    entries.extend([[322,3,1,struct.pack(e+'H',256),None],
                    [323,3,1,struct.pack(e+'H',256),None],
                    [324,16,count,b'\0'*(count*8),None],
                    [325,4,count,struct.pack(e+'I'*count,*([tilebytes]*count)),None]])
    entries.sort(key=lambda x:x[0])
    cursor=16+8+20*len(entries)+8
    exif_offset=core.align(cursor,8) if 34665 in tags else None
    if exif_offset is not None:
        cursor=exif_offset+8+20*len(exif)+8
    for item in entries:
        if item[0]==34665:item[3]=struct.pack(e+'I',exif_offset)
        elif len(item[3])>8:
            cursor=core.align(cursor,8);item[4]=cursor;cursor+=len(item[3])
    cursor=core.align(cursor,8); tile_offsets=[cursor+i*tilebytes for i in range(count)]
    next(x for x in entries if x[0]==324)[3]=struct.pack(e+'Q'*count,*tile_offsets)
    before=source.stat(); target.parent.mkdir(parents=True,exist_ok=True)
    fd,tmpname=tempfile.mkstemp(prefix='.retile_',suffix='.partial',dir=target.parent);tmp=Path(tmpname)
    try:
        with os.fdopen(fd,'wb') as out,source.open('rb') as inp:
            out.write(p.byte_order+struct.pack(e+'HHHQ',43,8,0,16));out.write(struct.pack(e+'Q',len(entries)))
            for tag,typ,n,raw,offset in entries:
                out.write(struct.pack(e+'HHQ',tag,typ,n));out.write(struct.pack(e+'Q',offset) if offset is not None else raw.ljust(8,b'\0'))
            out.write(struct.pack(e+'Q',0))
            if exif_offset is not None:
                core.pad_to(out,exif_offset);out.write(struct.pack(e+'Q',len(exif)))
                for tag,typ,n,raw in exif:out.write(struct.pack(e+'HHQ',tag,typ,n)+raw.ljust(8,b'\0'))
                out.write(struct.pack(e+'Q',0))
            for tag,typ,n,raw,offset in entries:
                if offset is not None:core.pad_to(out,offset);out.write(raw)
            source_digest=hashlib.sha256()
            for ty in range(rows):
                actual=min(256,h-ty*256); image_rows=[]
                for j in range(actual):
                    i=ty*256+j;inp.seek(page.source_chunk_offsets[i]);b=core.read_exact(inp,w*3,'源影像行');image_rows.append(b);source_digest.update(b)
                for tx in range(cols):
                    tile=bytearray(tilebytes);tw=min(256,w-tx*256)*3
                    for j,row in enumerate(image_rows):tile[j*768:j*768+tw]=row[tx*768:tx*768+tw]
                    core.pad_to(out,tile_offsets[ty*cols+tx]);out.write(tile)
                if ty%5==0:progress(f'已重排 {ty+1}/{rows} 瓦片行')
            out.flush();os.fsync(out.fileno())
        q,exifout=ex.parse(tmp);qt={x.tag:x for x in q.ifds[0].entries}
        keep=lambda tag:tag not in (273,278,279,34665)
        old={t:(x.type_id,x.count,x.raw) for t,x in tags.items() if keep(t)}
        new={t:(qt[t].type_id,qt[t].count,qt[t].raw) for t in old if t in qt}
        if old!=new or exif!=exifout or number(qt[322],e)!=256 or number(qt[323],e)!=256:
            raise ValueError('标签或 EXIF 回读不一致')
        if q.ifds[0].chunk_bytecounts!=[tilebytes]*count:raise ValueError('瓦片长度异常')
        # Full reverse traversal, hashing all valid pixels in scanline order; ignore edge padding.
        output_digest=hashlib.sha256()
        with tmp.open('rb') as check:
            for ty in range(rows):
                actual=min(256,h-ty*256)
                tile_rows=[]
                for tx in range(cols):
                    check.seek(q.ifds[0].source_chunk_offsets[ty*cols+tx]);tile_rows.append(core.read_exact(check,tilebytes,'瓦片回读'))
                for j in range(actual):
                    for tx,tile in enumerate(tile_rows):
                        tw=min(256,w-tx*256)*3;output_digest.update(tile[j*768:j*768+tw])
                if ty%5==0:progress(f'已核验 {ty+1}/{rows} 瓦片行')
        if source_digest.hexdigest()!=output_digest.hexdigest():raise ValueError('像素完整哈希不一致')
        if (before.st_size,before.st_mtime_ns)!=(source.stat().st_size,source.stat().st_mtime_ns):raise ValueError('源文件发生变化')
        if os.name!='nt':raise ValueError('本程序仅支持 Windows')
        os.rename(tmp,target)
        return {'status':'PASS','source':str(source),'output':str(target),'pixels_sha256':source_digest.hexdigest(),
                'pixel_hash_output':output_digest.hexdigest(),'tile':[256,256],'tile_count':count,
                'metadata_sha256':metadata_hash(old,exif),'output_bytes':target.stat().st_size,'exif_fields_equal':True,'nonstructural_tags_equal':True,
                'note':'保留当前输入标签；无法恢复输入已缺失的数据，不代表原图逐字节一致。'}
    finally:
        if tmp.exists():tmp.unlink()
