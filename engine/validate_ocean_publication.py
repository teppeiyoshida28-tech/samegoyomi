"""Validate an independent ocean-map release without weakening forecast gates."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent


def embedded(html, name):
    match = re.search(rf'const {name} = (.*?);\s*\n', html)
    if not match:
        raise ValueError(f'Missing embedded {name}')
    return json.loads(match.group(1))


def check_publication(*, marine_only=False, repo=None):
    repo = Path(repo) if repo else ROOT.parent
    engine, docs = repo / 'engine', repo / 'docs'
    expected = {name: json.loads((engine / filename).read_text(encoding='utf-8')) for name, filename in [
        ('OCEAN_CURRENT', 'ocean_current_forecast.json'), ('MAP_TRACE', 'map_trace.json'),
        ('MAP_FLOW', 'map_flow_field.json'), ('MAP_CATALOG', 'map_points.json')]}
    if expected['MAP_TRACE']['source']['sha256'] != expected['MAP_FLOW']['source']['traceSha256']:
        raise ValueError('Flow field and trace geometry do not match')
    if not expected['MAP_FLOW']['validation']['passed']:
        raise ValueError('Flow field validation has not passed')
    for relative in ['index.html', 'map/index.html', 'aim/index.html']:
        text = (docs / relative).read_text(encoding='utf-8')
        for name, value in expected.items():
            if embedded(text, name) != value:
                raise ValueError(f'{relative}: embedded {name} differs from the validated input')
        if 'refreshForecastAvailability' not in text or 'forecast-expired' not in text:
            raise ValueError(f'{relative}: missing legacy forecast expiry guard')
        if '/*__OCEAN_' in text or '/*__MAP_FLOW__' in text:
            raise ValueError(f'{relative}: unresolved template marker')
    # A marine-only release may retain only a forecast already recorded as
    # published, never a pending candidate that failed the full forecast gate.
    if marine_only:
        forecast = json.loads((engine / 'forecast_data.json').read_text(encoding='utf-8'))
        digest = hashlib.sha256(json.dumps(forecast, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        issued = [json.loads(p.read_text(encoding='utf-8')) for p in (engine / 'forecast_archive').glob('*.json')]
        if not any(x.get('forecast_sha256') == digest for x in issued):
            raise ValueError('Marine-only release cannot publish an unissued hammer forecast')
    return True


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--marine-only', action='store_true')
    args=parser.parse_args()
    # Keep the existing build/catalog/forecast consistency checks in both paths.
    import monitor
    from fetch_ocean_current import validate_snapshot
    ocean=json.loads((ROOT/'ocean_current_forecast.json').read_text(encoding='utf-8'))
    errors=validate_snapshot(ocean)
    if errors:
        raise ValueError('; '.join(errors))
    monitor.check_site_built()
    if monitor.problems:
        raise ValueError('; '.join(monitor.problems))
    check_publication(marine_only=args.marine_only)
    print('[ocean-map] embedded data, geometry, and publication boundary OK')


if __name__ == '__main__':
    main()
