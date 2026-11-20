import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
from verify_publication import compare_page


class ServedPublicationTests(unittest.TestCase):
    def test_cdn_old_forecast_or_calendar_is_not_published_evidence(self):
        page='const FORECAST = {"issued_at":"new"};\nconst LONGRANGE = {"version":2};\nconst OCEAN_CURRENT = {};\n'
        compare_page(page,page)
        for old in (page.replace('"new"','"old"'),page.replace('"version":2','"version":1')):
            with self.assertRaises(ValueError):compare_page(old,page)
