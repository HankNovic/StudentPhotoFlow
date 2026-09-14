import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from build_candidate import validate_web

class CandidateTest(unittest.TestCase):
    def test_missing_stale_and_incomplete_output_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            web=Path(folder)
            with self.assertRaises(ValueError):validate_web(web)
            (web/'assets').mkdir()
            (web/'index.html').write_text('<script type="module" src="/assets/main.js"></script>',encoding='utf-8')
            asset=web/'assets/main.js';asset.write_text('test',encoding='utf-8')
            info=dict(source_commit='test-commit',files={p.relative_to(web).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in web.rglob('*') if p.is_file()})
            (web/'build-info.json').write_text(json.dumps(info),encoding='utf-8')
            self.assertEqual(validate_web(web)['source_commit'],'test-commit')
            asset.write_text('stale',encoding='utf-8')
            with self.assertRaises(ValueError):validate_web(web)
            asset.unlink()
            with self.assertRaises(ValueError):validate_web(web)
