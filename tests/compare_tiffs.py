"""Compare two TIFFs without publishing their data; optionally sample via Rasterio."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "v4"))
import tiff_core

left, right = (Path(x) for x in sys.argv[1:3])
a = tiff_core.parse_tiff(left, False)
b = tiff_core.parse_tiff(right, False)
checks = {
    "same_byte_order": a.byte_order == b.byte_order,
    "same_page_count": len(a.ifds) == len(b.ifds),
    "same_image_blocks_sha256": tiff_core.payload_sha256(left, a.ifds) == tiff_core.payload_sha256(right, b.ifds),
    "same_nonstructural_metadata": tiff_core.metadata_fingerprint(a) == tiff_core.metadata_fingerprint(b),
    "same_block_lengths": [i.chunk_bytecounts for i in a.ifds] == [i.chunk_bytecounts for i in b.ifds],
    "same_non_offset_tags": [
        {e.tag: (e.type_id, e.count, e.raw) for e in i.entries if e.tag != i.data_offsets_tag}
        for i in a.ifds
    ] == [
        {e.tag: (e.type_id, e.count, e.raw) for e in i.entries if e.tag != i.data_offsets_tag}
        for i in b.ifds
    ],
}
try:
    import rasterio
    from rasterio.windows import Window
    import numpy as np
    with rasterio.open(left) as x, rasterio.open(right) as y:
        checks["same_shape_crs_transform"] = (
            (x.width, x.height, x.count, x.crs, x.transform)
            == (y.width, y.height, y.count, y.crs, y.transform)
        )
        windows = [Window(0, 0, 100, 100),
                   Window(max(0, x.width // 2 - 50), max(0, x.height // 2 - 50), 100, 100),
                   Window(max(0, x.width - 100), max(0, x.height - 100), 100, 100)]
        checks["three_decoded_pixel_samples"] = all(
            np.array_equal(x.read(window=w), y.read(window=w)) for w in windows
        )
except ImportError:
    print("Rasterio unavailable; decoded sample comparison skipped", file=sys.stderr)
print(json.dumps(checks, ensure_ascii=False, indent=2))
if not all(checks.values()):
    raise SystemExit(1)
