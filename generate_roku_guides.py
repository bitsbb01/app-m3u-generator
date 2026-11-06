#!/usr/bin/env python3
"""Generate plain JSON guide files for Roku from the IPTV-org/EPGshare feeds."""
import gzip
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

PLAYLIST_URL = "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8"
EPG_INDEX_URL = "https://epgshare01.online/epgshare01/"
OUTPUT_DIR = "epg"
USER_AGENT = "JGJG-Roku-Guide-Builder/1.0"

def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()

def parse_attributes(line):
    return dict(re.findall(r'([\w-]+)="([^"]*)"', line))

def parse_playlist(text):
    by_country = {}
    pending = None
    for line in text.splitlines():
        if line.startswith("#EXTINF:"):
            attrs = parse_attributes(line)
            guide_id = attrs.get("tvg-id", "").strip()
            country = attrs.get("tvg-country", "").strip().upper()
            if not country and "." in guide_id:
                country = guide_id.rsplit(".", 1)[-1].upper()
            if country == "GB":
                country = "UK"
            channel_name = attrs.get("tvg-name", line.rsplit(",", 1)[-1]).strip()
            pending = (country, guide_id, channel_name)
        elif line and not line.startswith("#") and pending:
            country, guide_id, channel_name = pending
            if country and guide_id:
                by_country.setdefault(country, []).append({"id": guide_id, "name": channel_name})
            pending = None
    return by_country

def available_feeds():
    html = fetch(EPG_INDEX_URL).decode("utf-8", errors="replace")
    return {name.upper() for name in re.findall(r"epg_ripper_([^\"/?]+)\.xml\.gz", html, re.I)}

def choose_feed(country, available):
    code = country.upper()
    candidates = {"US": ["US2", "US1"], "CA": ["CA2", "CA1"]}.get(code, [code + "1", code + "2"])
    for candidate in candidates:
        if candidate in available:
            return candidate
    return None

def normalize(value):
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())

def parse_time(value):
    try:
        return datetime.strptime(value[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None

def build_country(country, channels, feed_url):
    target_ids = {channel["id"].lower(): channel["id"] for channel in channels}
    target_names = {}
    for channel in channels:
        name = normalize(channel["name"])
        if name:
            target_names.setdefault(name, []).append(channel["id"])

    now = datetime.now(timezone.utc)
    earliest, latest = now - timedelta(hours=3), now + timedelta(hours=36)
    channel_map, programmes = {}, {}
    request = urllib.request.Request(feed_url, headers={"User-Agent": USER_AGENT})

    # These source files are gzip archives, not HTTP gzip-encoded XML.
    # Decompress and filter upstream so the Roku only downloads small JSON.
    with urllib.request.urlopen(request, timeout=180) as response:
        with gzip.GzipFile(fileobj=response) as xml_file:
            for _, element in ET.iterparse(xml_file, events=("end",)):
                if element.tag == "channel":
                    source_id = element.attrib.get("id", "")
                    matches = []
                    direct = target_ids.get(source_id.lower())
                    if direct:
                        matches.append(direct)
                    if not matches:
                        for display_name in element.findall("display-name"):
                            candidates = target_names.get(normalize(display_name.text), [])
                            if len(candidates) == 1:
                                matches.extend(candidates)
                    if matches:
                        channel_map[source_id] = list(dict.fromkeys(matches))
                    element.clear()
                elif element.tag == "programme":
                    targets = channel_map.get(element.attrib.get("channel", ""), [])
                    if targets:
                        start = element.attrib.get("start", "")
                        stop = element.attrib.get("stop", "")
                        start_dt, stop_dt = parse_time(start), parse_time(stop)
                        if start_dt and stop_dt and stop_dt >= earliest and start_dt <= latest:
                            entry = {
                                "start": start,
                                "stop": stop,
                                "title": element.findtext("title") or "Untitled programme",
                                "description": element.findtext("desc") or ""
                            }
                            for target in targets:
                                programmes.setdefault(target, []).append(entry)
                    element.clear()

    for entries in programmes.values():
        entries.sort(key=lambda entry: entry["start"])
    return {"country": country, "updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "programmes": programmes}

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    playlist = fetch(PLAYLIST_URL).decode("utf-8", errors="replace")
    feeds = available_feeds()
    for country, channels in sorted(parse_playlist(playlist).items()):
        feed_name = choose_feed(country, feeds)
        path = os.path.join(OUTPUT_DIR, country.lower() + ".json")
        if not feed_name:
            result = {"country": country, "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "programmes": {}}
            print("No current guide feed for " + country + "; writing an empty guide")
        else:
            url = EPG_INDEX_URL + "epg_ripper_" + feed_name + ".xml.gz"
            try:
                result = build_country(country, channels, url)
            except Exception as error:
                result = {"country": country, "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "programmes": {}}
                print("Guide update failed for " + country + ": " + str(error) + "; writing an empty guide")
        with open(path, "w", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False, separators=(",", ":"))
        count = sum(len(entries) for entries in result["programmes"].values())
        print(path + ": " + str(count) + " programmes")

if __name__ == "__main__":
    main()
