"""A map-only release cannot mix data versions or reissue unpublished forecasts."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'engine'))
from validate_ocean_publication import check_publication


class OceanPublicationTests(unittest.TestCase):
    def fixture(self,folder):
        repo=Path(folder)
        for relative in ['engine/ocean_current_forecast.json','engine/map_trace.json','engine/map_flow_field.json',
                         'engine/map_points.json','engine/forecast_data.json','docs/index.html','docs/map/index.html','docs/aim/index.html']:
            target=repo/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/relative,target)
        shutil.copytree(ROOT/'engine/forecast_archive',repo/'engine/forecast_archive')
        return repo

    def test_current_data_and_geometry_are_embedded_together(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo=self.fixture(temporary)
            self.assertTrue(check_publication(repo=repo,marine_only=True))
            path=repo/'engine/ocean_current_forecast.json'
            data=json.loads(path.read_text(encoding='utf-8'))
            data['retrieved_at']='2000-01-01T00:00:00+09:00'
            path.write_text(json.dumps(data),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'OCEAN_CURRENT'):
                check_publication(repo=repo)

    def test_unpublished_hammer_data_cannot_hitchhike_on_map_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo=self.fixture(temporary)
            path=repo/'engine/forecast_data.json'
            data=json.loads(path.read_text(encoding='utf-8'))
            data['generated_at']='2099-01-01T00:00:00+09:00'
            path.write_text(json.dumps(data),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'unissued hammer forecast'):
                check_publication(repo=repo,marine_only=True)


if __name__=='__main__':unittest.main()
