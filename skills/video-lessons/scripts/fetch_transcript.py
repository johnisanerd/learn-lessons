#!/usr/bin/env python3
"""Fetch YouTube transcripts through Apify and write them as timestamped Markdown.

Needs an Apify API token in the APIFY_TOKEN environment variable, or in
~/.config/video-lessons/.env as a line `APIFY_TOKEN=...`. The token is sent
only to api.apify.com, in a header, and is never printed.

  fetch_transcript.py list <url> [<url> ...]
      Expand playlists (needs yt-dlp) and print id, seconds, title as TSV,
      plus the estimated cost. Free: no Apify call.

  fetch_transcript.py fetch <url> [<url> ...] --out DIR [--lang en,bn] [--max-usd 0.10]
      Fetch captions with johnvc/YoutubeTranscripts. Writes DIR/transcripts/<id>.md
      and DIR/meta.json. Prints a JSON summary; exit code 3 if some videos have
      no captions (listed under "no_captions").

  fetch_transcript.py fetch <url> ... --out DIR --ai-fallback --max-usd 1.50
      Same, but transcribe caption-less videos with Whisper via
      codepoetry/youtube-transcript-ai-scraper (~$0.012 per minute).
      Only run this after the user has agreed to the cost.

  fetch_transcript.py render <items.json> --out DIR
      Write transcripts from dataset items you already have (for example,
      fetched through the Apify MCP server). No token needed.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

API = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
CAPTIONS_ACTOR = "johnvc~YoutubeTranscripts"
AI_ACTOR = "codepoetry~youtube-transcript-ai-scraper"
PRICE_PER_VIDEO = 0.003
MAX_URLS_PER_RUN = 100  # johnvc/YoutubeTranscripts only processes the first 100 URLs of a run
PRICE_AI_PER_MIN = 0.012
PARAGRAPH_SECONDS = 30
ENV_FILE = Path.home() / ".config" / "video-lessons" / ".env"


def token():
    value = os.environ.get("APIFY_TOKEN", "").strip()
    if not value and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            key, _, val = line.partition("=")
            if key.strip() == "APIFY_TOKEN":
                value = val.strip().strip("'\"")
    if not value:
        sys.exit(
            "No Apify token. Set APIFY_TOKEN, or put `APIFY_TOKEN=...` in "
            f"{ENV_FILE} (chmod 600). Get one at https://console.apify.com/settings/integrations"
        )
    return value


def run_actor(actor, payload, max_usd):
    query = urllib.parse.urlencode({"timeout": 300, "maxTotalChargeUsd": f"{max_usd:.2f}"})
    request = urllib.request.Request(
        API.format(actor=actor) + "?" + query,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=330) as response:
            return json.load(response)
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")[:300]
        if err.code == 401:
            sys.exit("Apify rejected the token (401). Check APIFY_TOKEN.")
        sys.exit(f"Apify error {err.code}: {detail}")
    except urllib.error.URLError as err:
        sys.exit(f"Could not reach Apify: {err.reason}")


def video_id(url):
    match = re.search(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([\w-]{11})", url)
    return match.group(1) if match else None


YOUTUBE = re.compile(r"^https?://([\w-]+\.)?(youtube\.com|youtu\.be)/\S+$")


def expand(urls):
    """Return [(id, seconds, title)] with playlists expanded through yt-dlp."""
    rows = []
    for url in urls:
        if not YOUTUBE.match(url):
            sys.exit(f"Not a YouTube URL: {url[:100]}")
        is_playlist = "/playlist" in url or ("list=" in url and not video_id(url))
        if is_playlist and not shutil.which("yt-dlp"):
            sys.exit("Playlists need yt-dlp (pip install yt-dlp, or your package manager).")
        if shutil.which("yt-dlp"):
            if not is_playlist and video_id(url):
                url = f"https://www.youtube.com/watch?v={video_id(url)}"
            out = subprocess.run(
                ["yt-dlp", "--flat-playlist", "--no-warnings", "--print", "%(id)s\t%(duration)s\t%(title)s", "--", url],
                capture_output=True, text=True,
            )
            if out.returncode == 0 and out.stdout.strip():
                for line in out.stdout.splitlines():
                    vid, secs, title = (line.split("\t") + ["", ""])[:3]
                    rows.append((vid, int(float(secs)) if secs not in ("", "NA", "None") else None, title))
                continue
            if is_playlist:
                sys.exit(f"yt-dlp failed: {out.stderr.strip()[:300]}")
        rows.append((video_id(url) or url, None, ""))
    seen = set()
    return [r for r in rows if not (r[0] in seen or seen.add(r[0]))]


def stamp(seconds):
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def chapters(description):
    return [line.strip() for line in (description or "").splitlines()
            if re.match(r"^\s*(\d+:)?\d{1,2}:\d{2}\s+\S", line)]


def paragraphs(segments):
    out, current, start = [], [], None
    for seg in segments:
        text = " ".join(str(seg.get("text", "")).split())
        if not text:
            continue
        if start is None:
            start = float(seg.get("start", 0))
        current.append(text)
        if float(seg.get("start", 0)) - start >= PARAGRAPH_SECONDS:
            out.append((start, " ".join(current)))
            current, start = [], None
    if current:
        out.append((start, " ".join(current)))
    return out


def segments_of(item):
    for key in ("timestamped", "transcript", "segments", "transcript_json", "json"):
        value = item.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict) and "start" in value[0]:
            return value
    text = item.get("non_timestamped") or item.get("transcript_text") or item.get("text") or ""
    return [{"start": 0, "text": text}] if text else []


def normalise(item):
    meta = item.get("metadata") or {}
    vid = item.get("video_id") or meta.get("id") or video_id(item.get("url", "") or meta.get("url", ""))
    return {
        "id": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "title": item.get("title") or meta.get("title") or "",
        "channel": item.get("channel_name") or meta.get("channel") or "",
        "duration_seconds": item.get("video_duration_seconds") or meta.get("duration"),
        "language": item.get("language_code") or item.get("language") or "",
        "captions": "ai-transcribed" if item.get("is_ai_generated") else
                    "auto-generated" if item.get("is_generated") or item.get("is_auto_generated") else "manual",
        "description": item.get("description") or meta.get("description") or "",
        "segments": segments_of(item),
    }


def write_transcript(video, out_dir):
    path = out_dir / "transcripts" / f"{video['id']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Transcript: {video['title']}",
        "",
        f"{video['channel']} · {video['url']} · {stamp(video['duration_seconds'] or 0)} · "
        f"{video['language']} captions ({video['captions']})",
        "",
    ]
    marks = chapters(video["description"])
    if marks:
        lines += ["## Chapters (from the description)", ""] + [f"- {m}" for m in marks] + [""]
    lines += ["## Transcript", ""]
    for start, text in paragraphs(video["segments"]):
        lines += [f"[{stamp(start)}] {text}", ""]
    path.write_text("\n".join(lines))
    return path


def save(items, out_dir, actor, cost):
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"videos": [], "cost_usd": 0}
    known = {v["id"] for v in meta["videos"]}
    written = []
    for item in items:
        video = normalise(item)
        if not video["id"] or not video["segments"]:
            continue
        path = write_transcript(video, out_dir)
        written.append({"id": video["id"], "title": video["title"], "transcript": str(path)})
        if video["id"] not in known:
            meta["videos"].append({k: video[k] for k in ("id", "url", "title", "channel", "duration_seconds", "language", "captions")})
    meta.update({"fetched": date.today().isoformat(), "actor": actor})
    meta["cost_usd"] = round(meta.get("cost_usd", 0) + cost, 4)
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    return written


def parse_args(argv):
    opts = {"out": None, "lang": "en", "max_usd": None, "ai": False}
    urls, i = [], 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--out":
            opts["out"], i = Path(argv[i + 1]).expanduser(), i + 2
        elif arg == "--lang":
            opts["lang"], i = argv[i + 1], i + 2
        elif arg == "--max-usd":
            opts["max_usd"], i = float(argv[i + 1]), i + 2
        elif arg == "--ai-fallback":
            opts["ai"], i = True, i + 1
        else:
            urls.append(arg)
            i += 1
    return urls, opts


def cmd_list(urls):
    rows = expand(urls)
    known = [r[1] for r in rows if r[1]]
    for vid, secs, title in rows:
        print(f"{vid}\t{secs if secs is not None else '?'}\t{title}")
    total_min = sum(known) / 60
    print(f"# {len(rows)} videos, {total_min:.0f} min known; captions ~${len(rows) * PRICE_PER_VIDEO:.3f}; "
          f"if none had captions, AI ~${total_min * PRICE_AI_PER_MIN:.2f}", file=sys.stderr)


def cmd_fetch(urls, opts):
    if not opts["out"]:
        sys.exit("--out DIR is required")
    rows = expand(urls)
    links = [f"https://www.youtube.com/watch?v={vid}" for vid, _, _ in rows]
    langs = [code.strip() for code in opts["lang"].split(",") if code.strip()]
    items = []
    for start in range(0, len(links), MAX_URLS_PER_RUN):
        batch = links[start:start + MAX_URLS_PER_RUN]
        if opts["max_usd"]:  # --max-usd covers all the caption runs, so each batch gets its share
            max_usd = max(0.01, opts["max_usd"] * len(batch) / len(links))
        else:
            max_usd = max(0.05, len(batch) * PRICE_PER_VIDEO * 2)
        items += run_actor(CAPTIONS_ACTOR, {"youtube_url": batch, "languages": langs, "include_metadata": True}, max_usd)
    good = [it for it in items if it.get("success") is not False and segments_of(it)]
    written = save(good, opts["out"], "johnvc/YoutubeTranscripts", len(good) * PRICE_PER_VIDEO)
    done = {w["id"] for w in written}
    missing = [{"id": vid, "seconds": secs, "title": title} for vid, secs, title in rows if vid not in done]

    if missing and opts["ai"]:
        minutes = sum((m["seconds"] or 3600) for m in missing) / 60
        ai_items = run_actor(AI_ACTOR, {
            "startUrls": [{"url": f"https://www.youtube.com/watch?v={m['id']}"} for m in missing],
            "languages": langs, "enableAiFallback": True, "useAnyAvailableCaptionLanguage": True,
            "outputFormats": ["json", "text"], "maxAiMinutes": min(600, int(minutes) + 1),
        }, opts["max_usd"] or minutes * PRICE_AI_PER_MIN * 1.5 + 0.05)
        ai_cost = sum(float(it.get("ai_duration_charged_min") or 0) for it in ai_items) * PRICE_AI_PER_MIN
        written += save(ai_items, opts["out"], "codepoetry/youtube-transcript-ai-scraper", ai_cost)
        done = {w["id"] for w in written}
        missing = [m for m in missing if m["id"] not in done]

    print(json.dumps({"written": written, "no_captions": missing}, indent=2, ensure_ascii=False))
    sys.exit(3 if missing else 0)


def cmd_render(path, opts):
    if not opts["out"]:
        sys.exit("--out DIR is required")
    data = json.loads(Path(path).read_text())
    items = data.get("items", data) if isinstance(data, dict) else data
    print(json.dumps({"written": save(items, opts["out"], "render", 0)}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    command, (args, options) = sys.argv[1], parse_args(sys.argv[2:])
    if command == "list":
        cmd_list(args)
    elif command == "fetch":
        cmd_fetch(args, options)
    elif command == "render" and args:
        cmd_render(args[0], options)
    else:
        sys.exit(__doc__)
