"""Publication must reject a map that uses different coordinates from scoring."""
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
import monitor


class MapPublicationTests(unittest.TestCase):
    def test_stale_map_catalog_blocks_publication(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            site = Path(tmp) / "docs"
            for rel in ["index.html", "map/index.html", "aim/index.html", "history/index.html"]:
                target = site / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / "docs" / rel, target)
            with patch.object(monitor, 'REPO_ROOT', tmp), patch.object(monitor, 'problems', []):
                monitor.check_site_built()
                self.assertEqual(monitor.problems, [])
                page = site / "map/index.html"
                text = page.read_text(encoding='utf-8')
                match = re.search(r"const MAP_CATALOG = (.*?);\s*\n", text)
                catalog = json.loads(match.group(1))
                catalog['points']['west_west']['pos'][0] += 180
                text = text[:match.start(1)] + json.dumps(catalog, ensure_ascii=False) + text[match.end(1):]
                page.write_text(text, encoding='utf-8')
                monitor.check_site_built()
                self.assertTrue(any('地点座標' in p for p in monitor.problems))


if __name__ == '__main__':
    unittest.main()
