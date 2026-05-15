#!/usr/bin/env python3
"""
ytmp3.py — YouTube → MP3/MP4 downloader with LAN web UI
Run: python ytmp3.py  (or python3 ytmp3.py on Linux)
Access from BB browser: http://<your-PC-LAN-IP>:5555

Requires: pip install yt-dlp flask --break-system-packages
Requires: ffmpeg in PATH

This converter is made mainly for old devices like my BlackBerry Bold 9700
that can't stream YouTube directly but can play local MP3/MP4 files. It uses yt-dlp to
download and convert videos, and serves a simple web UI for searching and managing downloads.
The UI is intentionally minimal and mobile-friendly, with no external dependencies.

Made by AzizBgBoss
"""

import os, re, threading, time, json, shutil, subprocess, random, hashlib
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, send_file, Response

# ── Config ─────────────────────────────────────────────────────────────────
PORT         = 5555
DOWNLOADS_DIR = Path("downloads")
HISTORY_FILE  = Path("downloads_history.json")
THUMBS_DIR    = Path("thumbs")

# Set to your ffmpeg.exe if not in PATH, e.g. r"C:\ffmpeg\bin\ffmpeg.exe"
FFMPEG_PATH  = None

# Number of search results to return (yt-dlp uses the `ytsearchN:` syntax)
SEARCH_RESULTS = 20

# ── Auto-detect ffmpeg on Windows ──────────────────────────────────────────
def find_ffmpeg():
    if FFMPEG_PATH and Path(FFMPEG_PATH).exists():
        return str(Path(FFMPEG_PATH).parent)
    # Check PATH first
    if shutil.which("ffmpeg"):
        return None  # None = use PATH (yt-dlp default)
    # Common Windows install locations
    candidates = [
        r"C:\ffmpeg\bin",
        r"C:\Program Files\ffmpeg\bin",
        r"C:\Tools\ffmpeg\bin",
        Path.home() / "ffmpeg" / "bin",
        Path.home() / "scoop" / "shims",
    ]
    for c in candidates:
        if Path(c, "ffmpeg.exe").exists():
            return str(c)
    return None  # will fail with a clear error from yt-dlp

FFMPEG_LOCATION = find_ffmpeg()

# ── Setup ───────────────────────────────────────────────────────────────────
try:
    import yt_dlp
except ImportError:
    print("ERROR: yt-dlp not installed. Run: pip install yt-dlp flask")
    exit(1)

DOWNLOADS_DIR.mkdir(exist_ok=True)
THUMBS_DIR.mkdir(exist_ok=True)
app = Flask(__name__, static_folder=".", static_url_path="")

# In-memory active jobs  { job_id: { status, title, progress, filename, error } }
jobs = {}

# ── Persistent history ──────────────────────────────────────────────────────
history_lock = threading.Lock()

def load_history():
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text("utf-8"))
        except Exception:
            pass
    return []

def save_entry(entry):
    with history_lock:
        h = load_history()
        # avoid duplicates by filename
        h = [x for x in h if x.get("filename") != entry["filename"]]
        h.insert(0, entry)
        HISTORY_FILE.write_text(json.dumps(h, ensure_ascii=False, indent=2), "utf-8")

# ── Helpers ─────────────────────────────────────────────────────────────────
def sanitize(name):
    return re.sub(r'[\\/*?:"<>|]', "_", name)

def fmt_duration(secs):
    if not secs: return "?"
    secs = int(secs)
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def strip_ansi(text):
    """Remove ANSI escape codes from text"""
    return re.sub(r'\x1b\[[0-9;]*m', '', str(text))

def file_url(filename, route="dl"):
    return f"/{route}/{filename}"

def thumb_name(filename, suffix=".jpg"):
    key = hashlib.sha1(filename.encode("utf-8", "ignore")).hexdigest()
    return THUMBS_DIR / f"{key}{suffix}"

def ffmpeg_bin():
    if FFMPEG_LOCATION:
        return str(Path(FFMPEG_LOCATION) / "ffmpeg")
    return "ffmpeg"

def ffprobe_bin():
    if FFMPEG_LOCATION:
        return str(Path(FFMPEG_LOCATION) / "ffprobe")
    return "ffprobe"

def media_duration(path):
    try:
        result = subprocess.run(
            [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=8
        )
        if result.returncode == 0:
            return max(1, int(float(result.stdout.strip() or "1")))
    except Exception:
        pass
    return 12

def make_video_thumb(path, filename):
    out = thumb_name(filename)
    if out.exists():
        return out
    duration = media_duration(path)
    at = random.randint(1, max(1, min(duration - 1, duration)))
    try:
        result = subprocess.run(
            [ffmpeg_bin(), "-y", "-ss", str(at), "-i", str(path),
             "-frames:v", "1", "-vf", "scale=160:-1", str(out)],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 and out.exists():
            return out
    except Exception:
        pass
    return None

def make_audio_thumb(path, filename):
    out = thumb_name(filename)
    if out.exists():
        return out
    try:
        result = subprocess.run(
            [ffmpeg_bin(), "-y", "-i", str(path), "-map", "0:v:0",
             "-frames:v", "1", "-vf", "scale=160:-1", str(out)],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 and out.exists():
            return out
    except Exception:
        pass
    return None

def music_note_svg():
    return """<svg xmlns="http://www.w3.org/2000/svg" width="160" height="90" viewBox="0 0 160 90"><rect width="160" height="90" fill="#0f0f1a"/><path d="M91 18v39c0 9-8 15-18 15-8 0-14-4-14-10s6-10 14-10c3 0 6 1 8 2V26h33v11H91z" fill="#00ff88"/><rect x="1" y="1" width="158" height="88" fill="none" stroke="#1a1a2e" stroke-width="2"/></svg>"""

def history_items():
    h = load_history()
    items = []
    for e in h:
        filename = os.path.basename(e.get("filename", ""))
        path = DOWNLOADS_DIR / filename
        if path.exists():
            item = dict(e)
            item["filename"] = filename
            item["size"] = path.stat().st_size
            item["open_url"] = file_url(filename, "open")
            item["download_url"] = file_url(filename, "dl")
            item["thumb"] = file_url(filename, "thumb")
            items.append(item)
    return items

# ── Download workers ─────────────────────────────────────────────────────────
def make_hook(job):
    class Hook:
        def __call__(self, d):
            if d["status"] == "downloading":
                # Extract just the number from percent_str (e.g., "5.63%" -> "5.63")
                percent_str = strip_ansi(d.get("_percent_str", "?%")).strip()
                job["progress"] = percent_str.rstrip('%')
                job["speed"]    = strip_ansi(d.get("_speed_str", "")).strip()
                job["eta"]      = strip_ansi(d.get("_eta_str", "")).strip()
                job["status"]   = "downloading"
            elif d["status"] == "finished":
                job["progress"] = "100"
                job["status"]   = "converting"
    return Hook()

def download_job(job_id, url, fmt):
    if fmt == "mp4":
        download_mp4(job_id, url)
    else:
        download_mp3(job_id, url)

def download_mp3(job_id, url):
    job = jobs[job_id]
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(DOWNLOADS_DIR / "%(title)s.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "progress_hooks": [make_hook(job)],
        "quiet": True,
        "no_warnings": True,
    }
    if FFMPEG_LOCATION:
        ydl_opts["ffmpeg_location"] = FFMPEG_LOCATION

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info  = ydl.extract_info(url, download=False)
            title = info.get("title", "unknown")
            job["title"]  = title
            job["status"] = "starting"
            ydl.download([url])

        safe = sanitize(title)
        out  = DOWNLOADS_DIR / f"{safe}.mp3"
        if not out.exists():
            candidates = sorted(DOWNLOADS_DIR.glob("*.mp3"), key=os.path.getmtime, reverse=True)
            out = candidates[0] if candidates else None

        if out and out.exists():
            job["filename"] = out.name
            job["status"]   = "done"
            job["progress"] = "100%"
            save_entry({"filename": out.name, "title": title, "fmt": "mp3",
                        "date": time.strftime("%Y-%m-%d %H:%M"), "size": out.stat().st_size})
        else:
            raise FileNotFoundError("MP3 not found after conversion")

    except Exception as e:
        job["status"] = "error"
        job["error"]  = str(e)

def download_mp4(job_id, url):
    job = jobs[job_id]
    # Download video ≤480p + audio (skip high-res, we re-encode to 480×360 anyway)
    raw_tmpl = str(DOWNLOADS_DIR / "%(title)s.%(ext)s")
    ydl_opts = {
        "format": "best[ext=mp4][height<=480]+bestaudio[ext=m4a]/best[height<=480]+bestaudio/best",
        "outtmpl": raw_tmpl,
        "merge_output_format": "mp4",
        "progress_hooks": [make_hook(job)],
        "quiet": True,
        "no_warnings": True,
    }
    if FFMPEG_LOCATION:
        ydl_opts["ffmpeg_location"] = FFMPEG_LOCATION

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info  = ydl.extract_info(url, download=False)
            title = info.get("title", "unknown")
            job["title"]  = title
            job["status"] = "starting"
            ydl.download([url])

        # Find the raw downloaded file
        safe = sanitize(title)
        raw  = DOWNLOADS_DIR / f"{safe}.mp4"
        if not raw.exists():
            candidates = sorted(DOWNLOADS_DIR.glob("*.mp4"), key=os.path.getmtime, reverse=True)
            raw = candidates[0] if candidates else None

        if not raw or not raw.exists():
            raise FileNotFoundError("Downloaded video not found")

        # Re-encode with BB-compatible settings (from bbvideo.sh)
        job["status"]   = "converting"
        job["progress"] = "100%"
        out = DOWNLOADS_DIR / f"{safe}_bb.mp4"

        ffmpeg_bin = "ffmpeg"
        if FFMPEG_LOCATION:
            ffmpeg_bin = str(Path(FFMPEG_LOCATION) / "ffmpeg")

        cmd = [
            ffmpeg_bin, "-y", "-i", str(raw),
            "-vf", "scale=480:360:force_original_aspect_ratio=decrease",
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0",
            "-preset", "fast", "-crf", "30",
            "-c:a", "aac", "-b:a", "128k",
            "-threads", "0",
            str(out)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg error: {result.stderr[-300:]}")

        # Remove raw file if re-encode succeeded and it's different from output
        if raw.resolve() != out.resolve() and raw.exists():
            raw.unlink()

        job["filename"] = out.name
        job["status"]   = "done"
        save_entry({"filename": out.name, "title": title, "fmt": "mp4",
                    "date": time.strftime("%Y-%m-%d %H:%M"), "size": out.stat().st_size})

    except Exception as e:
        job["status"] = "error"
        job["error"]  = str(e)


# ── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_file("index.html")

@app.route("/search", methods=["POST"])
def search():
    q = (request.get_json(force=True) or {}).get("q", "").strip()
    if not q:
        return jsonify({"error": "empty query"}), 400
    try:
        opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{SEARCH_RESULTS}:{q}", download=False)
        results = []
        for e in (info.get("entries") or []):
            vid = e.get("id", "")
            results.append({
                "title":    e.get("title", "Unknown"),
                "url":      e.get("url") or f"https://www.youtube.com/watch?v={vid}",
                "channel":  e.get("channel") or e.get("uploader") or "?",
                "duration": fmt_duration(e.get("duration")),
                "thumb":    f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg" if vid else "",
            })
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/start", methods=["POST"])
def start():
    body = request.get_json(force=True) or {}
    url  = body.get("url", "").strip()
    fmt  = body.get("fmt", "mp3").strip().lower()
    fmt = fmt if fmt in ("mp3", "mp4") else "mp3"
    if not url:
        return jsonify({"error": "no url"}), 400
    job_id = str(int(time.time() * 1000))
    jobs[job_id] = {"status": "queued", "title": url[:60], "progress": "0%",
                    "speed": "", "eta": "", "filename": "", "error": "", "fmt": fmt}
    threading.Thread(target=download_job, args=(job_id, url, fmt), daemon=True).start()
    return jsonify({"job_id": job_id})

@app.route("/status/<job_id>")
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)

@app.route("/jobs")
def all_jobs():
    return jsonify(jobs)

@app.route("/history")
def history():
    return jsonify(history_items())

@app.route("/stats")
def stats():
    items = history_items()
    total = sum(int(e.get("size") or 0) for e in items)
    return jsonify({"count": len(items), "bytes": total})

@app.route("/feed/<kind>")
def feed(kind):
    want = "mp4" if kind == "video" else "mp3"
    items = [e for e in history_items() if (e.get("fmt") or "").lower() == want]
    random.shuffle(items)
    return jsonify(items)

@app.route("/dl/<filename>")
def dl(filename):
    return send_from_directory(DOWNLOADS_DIR.resolve(), os.path.basename(filename), as_attachment=True)

@app.route("/open/<filename>")
def open_media(filename):
    return send_from_directory(DOWNLOADS_DIR.resolve(), os.path.basename(filename), as_attachment=False)

@app.route("/thumb/<filename>")
def thumb(filename):
    safe = os.path.basename(filename)
    path = DOWNLOADS_DIR / safe
    if not path.exists():
        return Response(music_note_svg(), mimetype="image/svg+xml")

    ext = path.suffix.lower()
    made = None
    if ext == ".mp4":
        made = make_video_thumb(path, safe)
    elif ext == ".mp3":
        made = make_audio_thumb(path, safe)

    if made and made.exists():
        return send_file(made)
    return Response(music_note_svg(), mimetype="image/svg+xml")

@app.route("/delete/<filename>", methods=["POST", "DELETE"])
def delete(filename):
    safe = os.path.basename(filename)
    path = DOWNLOADS_DIR / safe
    if path.exists():
        path.unlink()
    for ext in (".jpg", ".png", ".webp"):
        cached = thumb_name(safe, ext)
        if cached.exists():
            cached.unlink()
    # Remove from history
    with history_lock:
        h = load_history()
        h = [e for e in h if e.get("filename") != safe]
        HISTORY_FILE.write_text(json.dumps(h, ensure_ascii=False, indent=2), "utf-8")
    return jsonify({"ok": True})


if __name__ == "__main__":
    import socket
    try:
        lan_ip = socket.gethostbyname(socket.gethostname())
    except:
        lan_ip = "0.0.0.0"

    ffmpeg_status = f"found at {FFMPEG_LOCATION}" if FFMPEG_LOCATION else \
                    ("found in PATH" if shutil.which("ffmpeg") else "NOT FOUND — run: winget install ffmpeg")

    print(f"""
  ytmp3 — YouTube → MP3
  ─────────────────────────────────
  Local : http://localhost:{PORT}
  LAN   : http://{lan_ip}:{PORT}
  MP3s  : {DOWNLOADS_DIR.resolve()}
  ffmpeg: {ffmpeg_status}
  ─────────────────────────────────
""")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
