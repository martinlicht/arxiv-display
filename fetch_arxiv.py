#!/usr/bin/env python3
"""Fetch compact arXiv metadata by first-submission month, without abstracts.

Standard library only. Uses the legacy search API because OAI-PMH's `from`
filters metadata modification dates, not original submission dates.
Store one gzip-compressed JSON snapshot per month, atomically. Successfully
saved historical months survive interruption and are reused on the next run.
Use --refresh periodically to capture revisions to historical metadata.

Keep only one maintenance process running across all your machines, in
accordance with https://info.arxiv.org/help/api/tou.html (metadata: CC0 1.0).
No network requests are made merely by opening the website.
"""

import argparse
import calendar
import gzip
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

API = "https://export.arxiv.org/api/query"
NS = {"atom": "http://www.w3.org/2005/Atom",
      "os": "http://a9.com/-/spec/opensearch/1.1/",
      "arxiv": "http://arxiv.org/schemas/atom"}
PAGE_SIZE = 1000
DELAY = 3.1


class Client:
    def __init__(self):
        self.last_request = 0.0

    def fetch(self, params):
        request = Request(API + "?" + urlencode(params), headers={
            "User-Agent": "arxiv-aggregate-maintenance/1.0",
            "Accept": "application/atom+xml"})
        for attempt in range(5):
            time.sleep(max(0, DELAY - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                with urlopen(request, timeout=90) as response:
                    root = ET.fromstring(response.read())
                entries = root.findall("atom:entry", NS)
                for entry in entries:
                    if entry.findtext("atom:id", "", NS).endswith("/errors"):
                        raise ValueError(entry.findtext("atom:summary", "API error", NS))
                total_text = root.findtext("os:totalResults", None, NS)
                if total_text is None:
                    raise ValueError("arXiv response has no totalResults")
                total = int(total_text)
                if total < 0:
                    raise ValueError("arXiv returned a negative result count")
                return total, entries
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                if isinstance(exc, HTTPError) and exc.code not in (429, 500, 502, 503, 504):
                    raise
                if attempt == 4:
                    raise
                wait = 15 * (2 ** attempt)
                if isinstance(exc, HTTPError):
                    retry = exc.headers.get("Retry-After", "")
                    try:
                        wait = max(wait, float(retry))
                    except ValueError:
                        try:
                            wait = max(wait, (parsedate_to_datetime(retry) -
                                datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError, OverflowError):
                            pass
                print(f"Request failed; retrying after {wait:.0f}s: {exc}", file=sys.stderr)
                time.sleep(wait)


def parse_entry(entry):
    def field(name):
        return " ".join(entry.findtext("atom:" + name, "", NS).split())
    url = field("id")
    if "/abs/" not in url:
        raise ValueError(f"Unexpected arXiv entry ID: {url}")
    identifier = url.split("/abs/", 1)[1]
    identifier = re.sub(r"v\d+$", "", identifier)
    submitted, updated = field("published"), field("updated")
    if not identifier or not submitted or not updated:
        raise ValueError("Entry missing required identifier or dates")
    # Validate dates before counting this entry in a monthly snapshot.
    datetime.fromisoformat(submitted.replace("Z", "+00:00"))
    datetime.fromisoformat(updated.replace("Z", "+00:00"))
    primary = entry.find("arxiv:primary_category", NS)
    return {"id": identifier, "submitted": submitted, "updated": updated,
            "title": field("title"),
            "author_count": len(entry.findall("atom:author", NS)),
            "primary_category": primary.get("term") if primary is not None else None,
            "categories": sorted({e.get("term") for e in
                entry.findall("atom:category", NS) if e.get("term")})}


def fetch_interval(client, query, first, last):
    date_query = f"submittedDate:[{first:%Y%m%d%H%M} TO {last:%Y%m%d%H%M}]"
    search = f"({query}) AND {date_query}" if query else date_query
    params = {"search_query": search, "start": 0, "max_results": PAGE_SIZE,
              "sortBy": "submittedDate", "sortOrder": "ascending"}
    total, entries = client.fetch(params)
    # The legacy API caps queries at 30,000 results. Split busy intervals
    # instead of silently truncating (needed for all-arXiv harvests).
    if total > 30000:
        minutes = int((last - first).total_seconds() // 60)
        if minutes < 1:
            raise ValueError("More than 30,000 entries in one minute; narrow QUERY")
        middle = first + timedelta(minutes=minutes // 2)
        left = fetch_interval(client, query, first, middle)
        right = fetch_interval(client, query, middle + timedelta(minutes=1), last)
        if left.keys() & right.keys():
            raise ValueError("Overlapping date partitions returned duplicate papers")
        return {**left, **right}
    records = {}
    received = 0
    while received < total:
        if not entries:
            raise ValueError("Empty page before all results were retrieved; rerun")
        for entry in entries:
            record = parse_entry(entry)
            date = datetime.fromisoformat(record["submitted"].replace("Z", "+00:00"))
            if not first <= date < last + timedelta(minutes=1):
                raise ValueError("API returned an entry outside the requested date range")
            if record["id"] in records:
                raise ValueError("Duplicate result during pagination; rerun")
            records[record["id"]] = record
        received += len(entries)
        if received < total:
            params["start"] = received
            next_total, entries = client.fetch(params)
            if next_total != total:
                raise ValueError("Result count changed during pagination; rerun")
    if len(records) != total:
        raise ValueError("Retrieved count differs from arXiv total; snapshot not saved")
    return records


def atomic_save(path, snapshot, compressed=True):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as raw:
            temporary = Path(raw.name)
            payload = json.dumps(snapshot, ensure_ascii=False,
                separators=(",", ":"), sort_keys=True).encode("utf-8")
            if compressed:
                with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
                    stream.write(payload)
            else:
                raw.write(payload)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_snapshot(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        snapshot = json.load(stream)
    month = path.name[:7]
    if (snapshot.get("schema_version") != 1 or snapshot.get("month") != month
            or not isinstance(snapshot.get("papers"), list)):
        raise ValueError(f"Unsupported or malformed snapshot: {path}")
    return snapshot


def make_index(directory, unpack=False):
    directory.mkdir(parents=True, exist_ok=True)
    # Pick gzip as the source when both versions exist. Never advertise an
    # unpacked copy from an older fetch: the browser must not see stale data.
    paths = {}
    for path in sorted(directory.glob("*.json*")):
        if re.fullmatch(r"\d{4}-\d{2}\.json(?:\.gz)?", path.name):
            if path.suffix == ".gz" or path.name[:7] not in paths:
                paths[path.name[:7]] = path
    months = []
    for month, path in sorted(paths.items()):
        snapshot = read_snapshot(path)
        plain = directory / (month + ".json")
        item = {"month": month, "count": len(snapshot["papers"]),
                "query": snapshot.get("query", ""),
                "fetched_at": snapshot.get("fetched_at")}
        if path.suffix == ".gz":
            item["packed"] = path.name
            if unpack:
                atomic_save(plain, snapshot, compressed=False)
                print(f"Unpacked {plain}", flush=True)
                item["unpacked"] = plain.name
            elif plain.exists() and read_snapshot(plain) == snapshot:
                item["unpacked"] = plain.name
        else:
            item["unpacked"] = plain.name
        months.append(item)
    atomic_save(directory / "manifest.json", {
        "schema_version": 1, "license": "CC0-1.0", "months": months}, compressed=False)
    print(f"Indexed {len(months)} months in {directory / 'manifest.json'}", flush=True)


def run(args):
    now = datetime.now(timezone.utc)
    if not 1991 <= args.start_year <= args.end_year <= now.year:
        raise ValueError("Require 1991 <= START_YEAR <= END_YEAR <= current UTC year")
    args.data_dir.mkdir(parents=True, exist_ok=True)
    # Serialize maintenance runs for this directory on Unix (GNU make's
    # usual platform). The OS releases the advisory lock after interruption.
    import fcntl
    with (args.data_dir / ".fetch.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another fetch is running in this data directory")
        client = Client()
        recent = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        for year in range(args.start_year, args.end_year + 1):
            for month in range(1, 13):
                if (year, month) > (now.year, now.month):
                    break
                if (year, month) < (1991, 8):
                    continue
                period = f"{year:04d}-{month:02d}"
                path = args.data_dir / (period + ".json.gz")
                if path.exists():
                    with gzip.open(path, "rt", encoding="utf-8") as stream:
                        previous = json.load(stream)
                    if previous.get("query") != args.query or previous.get("month") != period:
                        raise ValueError(f"{path} belongs to a different query/month; use another DATA_DIR")
                    if not args.refresh and period < recent:
                        print(f"Cached {period}: {len(previous['papers'])} records", flush=True)
                        continue
                first = datetime(year, month, 1, tzinfo=timezone.utc)
                last = datetime(year, month, calendar.monthrange(year, month)[1],
                                23, 59, tzinfo=timezone.utc)
                print(f"Fetching {period} ...", flush=True)
                records = fetch_interval(client, args.query, first, last)
                snapshot = {"schema_version": 1, "source": API, "license": "CC0-1.0",
                    "license_url": "https://info.arxiv.org/help/api/tou.html",
                    "query": args.query, "month": period,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "papers": sorted(records.values(), key=lambda r: (r["submitted"], r["id"]))}
                atomic_save(path, snapshot)
                print(f"Saved {path}: {len(records)} records", flush=True)
        make_index(args.data_dir)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--unpack", action="store_true", help="unpack existing data and index it, offline")
    mode.add_argument("--index", action="store_true", help="index existing data, offline")
    parser.add_argument("--start-year", type=int)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--data-dir", type=Path, default=Path("data/arxiv"))
    parser.add_argument("--query", default="cat:math.*")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    try:
        if args.unpack or args.index:
            make_index(args.data_dir, unpack=args.unpack)
        else:
            if args.start_year is None or args.end_year is None:
                parser.error("fetching requires --start-year and --end-year")
            run(args)
    except (ValueError, OSError, URLError, ET.ParseError) as exc:
        parser.exit(1, f"Fetch failed: {exc}\nPreviously saved snapshots remain available.\n")
    except KeyboardInterrupt:
        parser.exit(130, "Interrupted. Previously saved months will be reused on the next run.\n")


if __name__ == "__main__":
    main()
