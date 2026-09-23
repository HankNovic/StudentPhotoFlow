"""Verify archives actually downloaded by export-dialog.cjs, including real JPEG bytes."""
import io
import json
import sys
import zipfile
from pathlib import Path
from PIL import Image

root = Path(sys.argv[1])
photo = (root / 'synthetic.jpg').read_bytes()
preview = json.loads((root / 'batch-preview.json').read_text('utf-8'))
results = []
for filename, expected in [('single-selected.zip', ['00123-张三.jpg']),
                           ('batch-selected.zip', [x['file_name'] for x in preview['items']])]:
    with zipfile.ZipFile(root / filename) as z:
        assert set(z.namelist()) == set(expected) | {'manifest.json'}
        manifest = json.loads(z.read('manifest.json'))
        assert manifest['status'] == 'prepared'
        assert manifest['format_snapshot'] == preview['profile_snapshot']
        assert manifest['format_snapshot']['profile_id'] == 'named'
        assert len(manifest['items']) == len(expected)
        for item in manifest['items']:
            assert item['file_name'] == f"{item['student_id']}-{item['name']}.jpg"
            assert item['file_name'] in expected
            assert item['result_id'] and item['source_id']
            raw = z.read(item['file_name'])
            assert raw == photo
            with Image.open(io.BytesIO(raw)) as image:
                assert image.format == 'JPEG'
                image.verify()
        (root / (filename + '.manifest.json')).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), 'utf-8')
        results.append(dict(archive=filename,photos=len(expected),names=expected,format_snapshot=manifest['format_snapshot'],jpeg_bytes_match=True))
(root / 'zip-verification.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), 'utf-8')
print('Verified real downloaded ZIPs: 1 single + 70 batch JPEGs, filenames, original bytes and chosen format snapshots.')
