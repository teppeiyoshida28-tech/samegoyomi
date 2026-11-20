"""Fetch official issue/coverage metadata; never infer a flow-path class from prose.

Only structured dates and provenance are stored. Failed fetches or extraction leave
the last successful snapshot untouched; its original retrieval timestamp remains.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import requests
from domain import now_jst
from longrange_contract import validate_kuroshio

FEED_URL = "https://www.jamstec.go.jp/aplinfo/kowatch/?feed=rss2"
DEFAULT_OUTPUT = Path(__file__).with_name("kuroshio_forecast.json")
TITLE = re.compile(
    r"(?P<ey>\d{4})年\s*(?P<em>\d{1,2})月\s*(?P<ed>\d{1,2})日までの黒潮[「『\"\s]*長期[」』\"\s]*予測"
    r".*?\(\s*(?P<iy>\d{4})年\s*(?P<im>\d{1,2})月\s*(?P<id>\d{1,2})日発表\s*\)")


def extract_snapshot(xml_text, retrieved_at=None):
    fetched = retrieved_at or now_jst()
    if fetched.utcoffset() is None:
        raise ValueError("Retrieval timestamp must include timezone")
    candidates = []
    for item in ET.fromstring(xml_text).findall("./channel/item"):
        title = unicodedata.normalize("NFKC", item.findtext("title", ""))
        match = TITLE.search(title)
        if not match:
            if all(word in title for word in ("黒潮", "長期", "予測")):
                raise ValueError("Long-range forecast title format changed; do not fall back to an older article")
            continue
        values = {key: int(value) for key, value in match.groupdict().items()}
        issue = date(values["iy"], values["im"], values["id"])
        end = date(values["ey"], values["em"], values["ed"])
        link = item.findtext("link", "").strip()
        parsed = urlparse(link)
        if parsed.scheme != "https" or parsed.hostname != "www.jamstec.go.jp" or parsed.path != "/aplinfo/kowatch/" or not re.fullmatch(r"p=\d+", parsed.query):
            raise ValueError("Unexpected official article URL")
        if issue > fetched.date() or end < issue or (end - issue).days > 120:
            raise ValueError("Invalid or future issue/coverage dates")
        candidates.append((issue, end, link))
    if not candidates:
        raise ValueError("No dated long-range Kuroshio forecast found in the official feed")
    issue, end, link = max(candidates, key=lambda c: c[0])
    if len({(e, u) for i, e, u in candidates if i == issue}) != 1:
        raise ValueError("Ambiguous latest official issue")
    result = {
        "schema_version": 2, "retrieved_at": fetched.isoformat(timespec="seconds"),
        "updated": fetched.date().isoformat(), "issued_on": issue.isoformat(),
        "valid_from": issue.isoformat(), "valid_until": end.isoformat(),
        "source": "JAMSTEC 黒潮親潮ウォッチ 長期予測", "source_url": link,
        "source_status": "available", "status": "unknown", "score": None,
        "label": "黒潮未評価（計算上は中立）",
        "assessment": {"status": "unreviewed", "method": "not_automatically_classified",
                       "reason": "公式資料の発表日・対象期間を取得。期間内の流路変化を単一の接岸・離岸スコアへ自動分類しない。"},
        "note": "資料を取得できたことと、神子元の黒潮条件を日別に評価できたことは別です。黒潮条件は未評価として計算上の中立値0.5を使用します。",
        "extraction_version": "kowatch-title-v1",
    }
    errors = validate_kuroshio(result)
    if errors:
        raise ValueError("; ".join(errors))
    return result


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def fetch(output=DEFAULT_OUTPUT, session=None):
    client = session or requests.Session()
    response = client.get(FEED_URL, timeout=(10, 30), headers={"User-Agent": "SamegoyomiAI/1.0 (https://teppeiyoshida28-tech.github.io/samegoyomi/)"})
    response.raise_for_status()
    snapshot = extract_snapshot(response.content)
    # A cached older feed must not replace a newer successfully retrieved issue.
    path = Path(output)
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        errors = validate_kuroshio(previous)
        if errors:
            raise ValueError("Existing Kuroshio snapshot is structurally invalid: " + "; ".join(errors))
        if previous.get("issued_on", "") > snapshot["issued_on"]:
            raise ValueError("Official feed regressed to an older issue; keeping previous snapshot")
    atomic_write(path, snapshot)
    return snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.validate_only:
            data = json.loads(args.output.read_text(encoding="utf-8"))
            errors = validate_kuroshio(data)
            if errors:
                raise ValueError("; ".join(errors))
        else:
            data = fetch(args.output)
        print(f"Kuroshio source: issue={data.get('issued_on', 'legacy')} until={data['valid_until']} classification={data['status']}")
        return 0
    except (ValueError, OSError, requests.RequestException, ET.ParseError) as error:
        print(f"::warning::Kuroshio metadata update failed; previous snapshot unchanged: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
