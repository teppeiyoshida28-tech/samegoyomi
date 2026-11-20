"""Verify the served release before recording it as a published prediction."""
import json
from pathlib import Path
import time
import requests
from validate_ocean_publication import embedded

ROOT = Path(__file__).resolve().parent
SITE = 'https://teppeiyoshida28-tech.github.io/samegoyomi/'


def compare_page(actual, expected):
    for name in ('FORECAST', 'LONGRANGE', 'OCEAN_CURRENT'):
        if embedded(actual, name) != embedded(expected, name):
            raise ValueError(f'Served {name} does not match the validated release')


def main():
    pending = {
        rel: (ROOT.parent / 'docs' / rel).read_text(encoding='utf-8')
        for rel in ('index.html', 'aim/index.html', 'map/index.html')
    }
    for attempt in range(12):
        for relative in list(pending):
            try:
                response = requests.get(SITE + relative, params={'release_check': time.time_ns()}, timeout=30,
                    headers={'Cache-Control': 'no-cache', 'User-Agent': 'samegoyomi-publication-check/1.0'})
                response.raise_for_status()
                response.encoding = 'utf-8'
                compare_page(response.text, pending[relative])
                del pending[relative]
            except (requests.RequestException, ValueError) as error:
                print(f'[publication] {relative}: {type(error).__name__}; waiting for served release')
        if not pending:
            print('[publication] All served forecast, calendar and ocean values match the release')
            return
        if attempt < 11:
            time.sleep(10)
    raise RuntimeError('Served release mismatch; no new forecast issuance record may be saved')


if __name__ == '__main__':
    main()
