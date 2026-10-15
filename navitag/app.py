"""navitag: a small self-hosted web editor for music tags.

Browse folders under NAVITAG_MUSIC_ROOT, edit album-wide fields and per-track
number/title/artist, save with mutagen. Optional password login. Optional
"Rescan" button for Navidrome (or any Subsonic-compatible server).
"""
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from pathlib import Path

import mutagen
from flask import (Flask, abort, flash, redirect, render_template, request,
                   session, url_for)
from mutagen.mp3 import EasyMP3

__version__ = "0.1.0"


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


ROOT = Path(env("NAVITAG_MUSIC_ROOT", "/music")).resolve()
TITLE = env("NAVITAG_TITLE", "navitag")
PASSWORD = env("NAVITAG_PASSWORD")
ID3_VERSION = 4 if env("NAVITAG_ID3_VERSION", "3") == "4" else 3
COOKIE_SECURE = env("NAVITAG_COOKIE_SECURE").lower() in ("1", "true", "yes")

ND_URL = env("NAVIDROME_URL").rstrip("/")
ND_USER = env("NAVIDROME_USER")
ND_PASSWORD = env("NAVIDROME_PASSWORD")
ND_TOKEN = env("NAVIDROME_TOKEN")
ND_SALT = env("NAVIDROME_SALT")

AUDIO_EXT = {".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".oga", ".opus"}

# (tag key, label). Keys are mutagen "easy" names, valid for ID3, MP4 and Vorbis.
ALBUM_FIELDS = [
    ("albumartist", "Album artist"),
    ("album", "Album"),
    ("date", "Year"),
    ("genre", "Genre"),
    ("discnumber", "Disc"),
]
TRACK_FIELDS = [
    ("tracknumber", "Track"),
    ("title", "Title"),
    ("artist", "Artist"),
]

app = Flask(__name__)
# Set NAVITAG_SECRET_KEY to keep logins across restarts; otherwise random per start.
app.secret_key = env("NAVITAG_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SECURE=COOKIE_SECURE)


# ------------------------------------------------------------------ paths --
def safe_path(rel: str) -> Path:
    """Resolve rel under ROOT; 404 on anything that escapes it."""
    p = (ROOT / rel).resolve()
    if p != ROOT and ROOT not in p.parents:
        abort(404)
    return p


def rel_of(p: Path) -> str:
    return "" if p == ROOT else p.relative_to(ROOT).as_posix()


def is_audio(p: Path) -> bool:
    return p.is_file() and not p.name.startswith(".") and p.suffix.lower() in AUDIO_EXT


# ------------------------------------------------------------------- tags --
def load(path: Path):
    return mutagen.File(path, easy=True)


def get_tag(audio, key: str) -> str:
    if audio is None or audio.tags is None:
        return ""
    try:
        vals = audio.tags.get(key)
    except (KeyError, ValueError):
        return ""
    if not vals:
        return ""
    return "; ".join(str(v) for v in vals)


def set_tag(audio, key: str, value: str) -> None:
    if audio.tags is None:
        audio.add_tags()
    if value == "":
        try:
            if key in audio.tags:
                del audio.tags[key]
        except KeyError:
            pass
    else:
        audio.tags[key] = [value]


def save(audio) -> None:
    if isinstance(audio, EasyMP3):
        audio.save(v2_version=ID3_VERSION)
    else:
        audio.save()


# ------------------------------------------------------- csrf and login --
def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf() -> None:
    sent = request.form.get("csrf", "")
    if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
        abort(400, "Form expired. Reload the page and try again.")


@app.context_processor
def template_globals():
    return {"csrf_token": csrf_token, "app_title": TITLE,
            "login_enabled": bool(PASSWORD), "version": __version__}


@app.before_request
def require_login():
    if not PASSWORD or request.endpoint in ("login", "static", "healthz"):
        return None
    if not session.get("auth"):
        return redirect(url_for("login", next=request.full_path if request.query_string else request.path))
    return None


def safe_next(target: str) -> str:
    # Only same-site relative paths, never //host or scheme URLs.
    if target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("browse")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not PASSWORD:
        return redirect(url_for("browse"))
    error = None
    if request.method == "POST":
        check_csrf()
        if secrets.compare_digest(request.form.get("password", "").encode(), PASSWORD.encode()):
            session.clear()
            session["auth"] = True
            return redirect(safe_next(request.args.get("next", "")))
        time.sleep(1)  # slow down guessing
        error = "Wrong password."
    return render_template("login.html", error=error), (401 if error else 200)


@app.post("/logout")
def logout():
    check_csrf()
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------------ views --
@app.get("/")
@app.get("/browse/")
@app.get("/browse/<path:rel>")
def browse(rel: str = ""):
    folder = safe_path(rel)
    if not folder.is_dir():
        abort(404)
    entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    dirs = [p for p in entries if p.is_dir() and not p.name.startswith(".")]
    files = [p for p in entries if is_audio(p)]

    tracks, errors = [], []
    for f in files:
        try:
            audio = load(f)
        except Exception as exc:  # unreadable or corrupt file
            errors.append(f"{f.name}: {exc}")
            continue
        if audio is None:
            errors.append(f"{f.name}: not a supported audio file")
            continue
        tracks.append({"name": f.name,
                       "tags": {k: get_tag(audio, k) for k, _ in ALBUM_FIELDS + TRACK_FIELDS}})

    album = {}
    for key, label in ALBUM_FIELDS:
        values = {t["tags"][key] for t in tracks}
        album[key] = {"label": label,
                      "value": values.pop() if len(values) == 1 else "",
                      "mixed": len(values) > 1}

    crumbs, acc = [], []
    for part in Path(rel).parts:
        acc.append(part)
        crumbs.append((part, "/".join(acc)))

    return render_template("browse.html", rel=rel_of(folder), crumbs=crumbs,
                           dirs=[(d.name, rel_of(d)) for d in dirs],
                           tracks=tracks, album=album, errors=errors,
                           track_fields=TRACK_FIELDS, id3_version=ID3_VERSION,
                           can_rescan=bool(ND_URL and ND_USER and (ND_PASSWORD or (ND_TOKEN and ND_SALT))))


@app.post("/save/")
@app.post("/save/<path:rel>")
def save_folder(rel: str = ""):
    check_csrf()
    folder = safe_path(rel)
    if not folder.is_dir():
        abort(404)

    changed, failed = 0, []
    try:
        count = int(request.form.get("count", "0"))
    except ValueError:
        abort(400)
    for i in range(count):
        name = request.form.get(f"t{i}__file", "")
        path = folder / name
        # Only plain filenames of audio files in this folder.
        if not name or "/" in name or "\\" in name or name != Path(name).name or not is_audio(path):
            failed.append(f"{name or '?'}: invalid file")
            continue
        try:
            audio = load(path)
            if audio is None:
                raise ValueError("not a supported audio file")
            dirty = False
            for key, _ in ALBUM_FIELDS:
                new = request.form.get(f"album__{key}", "").strip()
                mixed = request.form.get(f"album__{key}__mixed") == "1"
                if mixed and new == "":
                    continue  # mixed and left empty: keep each file's value
                if get_tag(audio, key) != new:
                    set_tag(audio, key, new)
                    dirty = True
            for key, _ in TRACK_FIELDS:
                new = request.form.get(f"t{i}__{key}", "").strip()
                if get_tag(audio, key) != new:
                    set_tag(audio, key, new)
                    dirty = True
            if dirty:
                save(audio)
                changed += 1
        except Exception as exc:
            failed.append(f"{name}: {exc}")

    if failed:
        flash(f"Saved {changed} file(s). {len(failed)} failed: " + "; ".join(failed), "error")
    else:
        flash(f"Saved. {changed} file(s) changed." if changed else "No changes.", "ok")
    return redirect(url_for("browse", rel=rel_of(folder)))


def subsonic_auth() -> dict:
    if ND_PASSWORD:
        salt = secrets.token_hex(6)
        token = hashlib.md5((ND_PASSWORD + salt).encode()).hexdigest()
        return {"u": ND_USER, "t": token, "s": salt}
    return {"u": ND_USER, "t": ND_TOKEN, "s": ND_SALT}


@app.post("/rescan")
def rescan():
    check_csrf()
    back = request.form.get("back", "")
    params = urllib.parse.urlencode({**subsonic_auth(), "v": "1.16.1", "c": "navitag", "f": "json"})
    try:
        with urllib.request.urlopen(f"{ND_URL}/rest/startScan.view?{params}", timeout=10) as r:
            data = json.load(r)["subsonic-response"]
        if data.get("status") == "ok":
            flash("Library rescan started.", "ok")
        else:
            flash(f"Server refused: {data.get('error', {}).get('message', 'unknown error')}", "error")
    except Exception as exc:
        flash(f"Could not reach the music server: {exc}", "error")
    return redirect(url_for("browse", rel=back))


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": __version__}


def main() -> None:
    from waitress import serve
    if not ROOT.is_dir():
        raise SystemExit(f"NAVITAG_MUSIC_ROOT does not exist: {ROOT}")
    port = int(env("PORT", "8000"))
    print(f"navitag {__version__}: serving {ROOT} on port {port}"
          f"{' (login required)' if PASSWORD else ' (no login: keep it on a private network)'}", flush=True)
    serve(app, host=env("HOST", "0.0.0.0"), port=port, threads=4)


if __name__ == "__main__":
    main()
