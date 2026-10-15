import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FFMPEG = shutil.which("ffmpeg")


def make(path: Path, args: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-t", "1", *args, str(path)], check=True)


@pytest.fixture()
def library(tmp_path):
    if not FFMPEG:
        pytest.skip("ffmpeg not installed")
    root = tmp_path / "music"
    album = root / "Artist A" / "Album One"
    for i in (1, 2):
        make(album / f"0{i}.mp3", ["-metadata", f"title=Song {i}", "-metadata", "artist=Artist A",
                                   "-metadata", "album=Album One", "-metadata", "album_artist=Artist A",
                                   "-metadata", "date=2001", "-metadata", f"track={i}", "-id3v2_version", "3"])
    make(album / "03-untagged.mp3", ["-map_metadata", "-1", "-write_id3v2", "0"])
    mixed = root / "Mixed"
    make(mixed / "a.flac", ["-metadata", "title=F", "-metadata", "album=X", "-metadata", "date=1999"])
    make(mixed / "b.m4a", ["-c:a", "aac", "-metadata", "title=M", "-metadata", "album=Y"])
    make(mixed / "c.ogg", ["-c:a", "libvorbis", "-metadata", "title=O", "-metadata", "album=Z"])
    (mixed / "._a.flac").write_bytes(b"")
    (mixed / "notes.txt").write_text("x")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "x.mp3").write_text("secret")
    return root


def load_app(root: Path, **extra):
    env = {"NAVITAG_MUSIC_ROOT": str(root), "NAVITAG_PASSWORD": "", "NAVIDROME_URL": "",
           "NAVIDROME_USER": "", "NAVIDROME_PASSWORD": "", **extra}
    os.environ.update(env)
    mod = importlib.import_module("navitag.app")
    return importlib.reload(mod)


@pytest.fixture()
def client(library):
    mod = load_app(library)
    mod.app.config["TESTING"] = True
    return mod.app.test_client(), mod, library
