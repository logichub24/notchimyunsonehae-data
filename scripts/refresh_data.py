#!/usr/bin/env python3
"""Refresh the public, static official-data feed without storing API keys."""
import datetime as dt
import hashlib
import html
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

CULTURE_KEY = os.environ.get("CULTURE_API_KEY")
PUBLIC_KEY = os.environ.get("PUBLIC_DATA_API_KEY")
OUTPUT = Path(__file__).resolve().parents[1] / "official-data.json"
CULTURE_API = "https://api.kcisa.kr/openapi/CNV_060/request"
# ponytail: hosted GitHub runners intermittently cannot resolve this official host; remove this fallback when its DNS is stable there.
CULTURE_API_FALLBACK_IP = "175.125.91.8"
TARGET_KEYWORDS = {
    "car": ("자동차", "차량", "운전"),
    "house": ("주택", "부동산", "임대", "전세", "월세", "주거"),
    "biz": ("사업자", "소상공인", "중소기업", "창업", "사업"),
    "child": ("자녀", "아동", "영유아", "보육", "출산", "육아", "청소년"),
    "job": ("직장", "근로", "고용", "취업", "채용", "일자리"),
}


def node_text(item, *names):
    for name in names:
        value = item.findtext(name)
        if value and value.strip():
            return value.strip()
    return ""


def iso_date(value):
    value = re.sub(r"\D", "", value or "")
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if len(value) == 8 else ""


def period_end(value):
    matches = re.findall(r"(20\d{2})[^0-9]{0,3}(\d{1,2})[^0-9]{0,3}(\d{1,2})", value or "")
    if not matches:
        return ""
    year, month, day = matches[-1]
    return f"{year}-{int(month):02d}-{int(day):02d}"


def target_types(title):
    return [target for target, keywords in TARGET_KEYWORDS.items() if any(keyword in title for keyword in keywords)]


def public_events(today):
    sources = [
        ("https://api.odcloud.kr/api/gov24/v3/serviceList", {"page": 1, "perPage": 100}, "혜택", "행정안전부"),
        ("https://apis.data.go.kr/1421000/bizinfo/pblancBsnsService", {"pageNo": 1, "numOfRows": 100, "returnType": "json"}, "사업", "중소벤처기업부"),
        ("https://apis.data.go.kr/B552735/kisedKstartupService01/getAnnouncementInformation01", {"page": 1, "perPage": 100}, "사업", "창업진흥원"),
    ]
    events = []
    for url, params, category, provider in sources:
        params["serviceKey"] = PUBLIC_KEY
        try:
            with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=60) as response:
                payload = json.loads(response.read())
        except (urllib.error.URLError, json.JSONDecodeError) as error:
            print(f"Could not refresh {provider}: {error}", file=sys.stderr)
            continue
        rows = payload.get("data") or payload.get("response", {}).get("body", {}).get("items", {}).get("item", [])
        rows = [rows] if isinstance(rows, dict) else rows
        for row in rows if isinstance(rows, list) else []:
            title = row.get("serviceName") or row.get("서비스명") or row.get("pblancNm") or row.get("bizPbancNm") or row.get("bizNm") or row.get("title")
            deadline = period_end(str(row.get("applicationPeriod") or row.get("신청기한") or row.get("reqstBeginEndDe") or row.get("rceptPd") or row.get("bizEndDt") or row.get("recruitPeriod") or ""))
            always_available = category == "혜택" and not deadline
            if not title or (not always_available and (not deadline or deadline < today.isoformat())):
                continue
            item_category = "교육" if any(word in title for word in ("교육", "장학")) else "취업" if any(word in title for word in ("취업", "고용", "일자리", "채용")) else category
            identity = hashlib.sha256(f"{provider}\0{title}\0{deadline}".encode()).hexdigest()[:16]
            events.append({"id": f"public-{identity}", "title": title, "category": item_category, "endDate": deadline or None, "alwaysAvailable": always_available, "repeat": "none", "regionCodes": ["전국"], "interestTags": [item_category], "targetTypes": target_types(title), "providerName": provider, "sourceUrl": row.get("onlineApplyUrl") or row.get("pblancUrl") or row.get("detailUrl") or row.get("상세조회URL") or row.get("serviceUrl") or "https://www.k-startup.go.kr", "status": "open", "verificationStatus": "official", "sourceUpdatedAt": today.isoformat()})
    return events


def culture_events(today):
    params = {"serviceKey": CULTURE_KEY, "numOfRows": 100, "pageNo": 1}
    request = urllib.request.Request(f"{CULTURE_API}?{urllib.parse.urlencode(params)}", headers={"Accept": "application/xml"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        message = error.read().decode("utf-8", "replace").replace(CULTURE_KEY, "[redacted]")[:300]
        raise RuntimeError(f"Culture API HTTP {error.code}: {message}") from error
    except urllib.error.URLError as error:
        if not isinstance(error.reason, socket.gaierror):
            raise
        body = subprocess.run(["curl", "--fail", "--silent", "--show-error", "--resolve", f"api.kcisa.kr:443:{CULTURE_API_FALLBACK_IP}", request.full_url], check=True, capture_output=True).stdout
    root = ET.fromstring(body)
    result_code = root.findtext(".//resultCode")
    if result_code and result_code not in {"0", "00", "0000"}:
        raise RuntimeError(f"Culture API returned {result_code}: {root.findtext('.//resultMsg') or 'unknown error'}")
    events = []
    for item in root.iter():
        title = node_text(item, "title")
        period = node_text(item, "period", "eventPeriod")
        end_date = iso_date(node_text(item, "endDate", "enddate", "startDate", "startdate")) or period_end(period)
        if not title or not end_date:
            continue
        source_url = node_text(item, "url", "link")
        identity = hashlib.sha256(f"{title}\0{period}\0{source_url}".encode()).hexdigest()[:16]
        events.append({"id": f"culture-{identity}", "title": html.unescape(title), "category": "문화", "endDate": end_date, "repeat": "none", "regionCodes": [node_text(item, "eventSite", "area", "place") or "전국"], "interestTags": ["문화"], "targetTypes": [], "providerName": "문화체육관광부 문화공공데이터광장", "sourceUrl": source_url or "https://www.culture.go.kr/data/openapi/openapiView.do?id=580", "status": "open", "verificationStatus": "official", "sourceUpdatedAt": today.isoformat()})
    return events


def main():
    if not CULTURE_KEY or not PUBLIC_KEY:
        raise RuntimeError("CULTURE_API_KEY and PUBLIC_DATA_API_KEY must be configured as Actions secrets.")
    today = dt.date.today()
    items = culture_events(today) + public_events(today)
    if not items:
        raise RuntimeError("No official items were fetched; keeping the previous data file unchanged.")
    checked_at = dt.datetime.now(dt.timezone.utc).isoformat()
    OUTPUT.write_text(json.dumps({"checkedAt": checked_at, "items": items}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(items)} official items.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(error, file=sys.stderr)
        sys.exit(1)
