import re

import mutagen
from mutagen.id3 import ID3

from conftest import load_app

KEYS = ["albumartist", "album", "date", "genre", "discnumber", "tracknumber", "title", "artist"]


def csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def msg(resp) -> str:
    return re.search(r'class="msg [a-z]+">([^<]+)', resp.get_data(as_text=True)).group(1)


def tags(mod, path):
    a = mutagen.File(path, easy=True)
    return {k: mod.get_tag(a, k) for k in KEYS}


def album_form(c, mod, folder, rel, overrides):
    html = c.get(f"/browse/{rel}").get_data(as_text=True)
    files = re.findall(r'name="(t\d+)__file" value="([^"]+)"', html)
    form = {"csrf": csrf(html), "count": str(len(files))}
    for key in ["albumartist", "album", "date", "genre", "discnumber"]:
        m = re.search(rf'name="album__{key}" value="([^"]*)"', html)
        form[f"album__{key}"] = m.group(1) if m else ""
        if f'name="album__{key}__mixed"' in html:
            form[f"album__{key}__mixed"] = "1"
    for tid, name in files:
        t = tags(mod, folder / name)
        form[f"{tid}__file"] = name
        for key in ["tracknumber", "title", "artist"]:
            form[f"{tid}__{key}"] = t[key]
    form.update(overrides)
    return form, files


def test_browse_lists_folders(client):
    c, _, _ = client
    html = c.get("/").get_data(as_text=True)
    assert "Artist A" in html and "Mixed" in html


def test_hidden_and_non_audio_ignored(client):
    c, _, _ = client
    html = c.get("/browse/Mixed").get_data(as_text=True)
    assert "._a.flac" not in html and "notes.txt" not in html


def test_album_edit_mp3(client):
    c, mod, root = client
    folder = root / "Artist A" / "Album One"
    form, _ = album_form(c, mod, folder, "Artist A/Album One",
                         {"album__album": "Album One", "album__albumartist": "Artist A",
                          "album__date": "2001", "album__genre": "Rock",
                          "t2__title": "Song 3", "t2__tracknumber": "3", "t2__artist": "Artist A"})
    r = c.post("/save/Artist A/Album One", data=form, follow_redirects=True)
    assert msg(r) == "Saved. 3 file(s) changed."
    for name in ["01.mp3", "02.mp3", "03-untagged.mp3"]:
        t = tags(mod, folder / name)
        assert t["album"] == "Album One" and t["genre"] == "Rock" and t["albumartist"] == "Artist A"
    assert tags(mod, folder / "03-untagged.mp3")["title"] == "Song 3"
    assert ID3(folder / "03-untagged.mp3").version == (2, 3, 0)


def test_no_change_writes_nothing(client):
    c, mod, root = client
    folder = root / "Artist A" / "Album One"
    form, _ = album_form(c, mod, folder, "Artist A/Album One", {})
    r = c.post("/save/Artist A/Album One", data=form, follow_redirects=True)
    assert msg(r) == "No changes."


def test_mixed_left_empty_keeps_values_all_formats(client):
    c, mod, root = client
    folder = root / "Mixed"
    form, files = album_form(c, mod, folder, "Mixed", {"album__genre": "Test", "album__album": ""})
    assert form.get("album__album__mixed") == "1"
    for tid, _ in files:
        form[f"{tid}__title"] += " ü"
    r = c.post("/save/Mixed", data=form, follow_redirects=True)
    assert msg(r) == f"Saved. {len(files)} file(s) changed."
    albums = {}
    for _, name in files:
        t = tags(mod, folder / name)
        assert t["genre"] == "Test" and t["title"].endswith(" ü")
        albums[name] = t["album"]
    assert albums == {"a.flac": "X", "b.m4a": "Y", "c.ogg": "Z"}


def test_clear_uniform_field_removes_tag(client):
    c, mod, root = client
    folder = root / "Artist A" / "Album One"
    album_form_data, _ = album_form(c, mod, folder, "Artist A/Album One",
                                    {"album__genre": "Rock", "album__album": "Album One",
                                     "album__albumartist": "Artist A", "album__date": "2001",
                                     "t2__title": "Song 3", "t2__tracknumber": "3", "t2__artist": "Artist A"})
    c.post("/save/Artist A/Album One", data=album_form_data)
    form, _ = album_form(c, mod, folder, "Artist A/Album One", {"album__genre": ""})
    assert "album__genre__mixed" not in form
    c.post("/save/Artist A/Album One", data=form)
    assert all(tags(mod, folder / n)["genre"] == "" for n in ["01.mp3", "02.mp3", "03-untagged.mp3"])


def test_path_traversal_blocked(client):
    c, _, _ = client
    assert c.get("/browse/../outside").status_code == 404
    assert c.get("/browse/%2e%2e/outside").status_code == 404


def test_bad_filename_rejected(client):
    c, _, root = client
    tok = csrf(c.get("/browse/Mixed").get_data(as_text=True))
    r = c.post("/save/Mixed", data={"csrf": tok, "count": "1", "t0__file": "../../outside/x.mp3",
                                    "t0__title": "pwn"}, follow_redirects=True)
    assert "invalid file" in msg(r)
    assert (root.parent / "outside" / "x.mp3").read_text() == "secret"


def test_csrf_required(client):
    c, _, _ = client
    assert c.post("/save/Mixed", data={"csrf": "nope", "count": "0"}).status_code == 400


def test_rescan_hidden_without_config(client):
    c, _, _ = client
    assert "Rescan library" not in c.get("/").get_data(as_text=True)


def test_login_required_when_password_set(library):
    mod = load_app(library, NAVITAG_PASSWORD="hunter2-long")
    c = mod.app.test_client()
    r = c.get("/browse/Mixed")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert c.get("/healthz").status_code == 200
    page = c.get("/login").get_data(as_text=True)
    assert c.post("/login", data={"csrf": csrf(page), "password": "wrong"}).status_code == 401
    page = c.get("/login").get_data(as_text=True)
    r = c.post("/login?next=/browse/Mixed", data={"csrf": csrf(page), "password": "hunter2-long"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/browse/Mixed")
    assert c.get("/browse/Mixed").status_code == 200


def test_login_next_cannot_redirect_offsite(library):
    mod = load_app(library, NAVITAG_PASSWORD="pw-long-enough")
    c = mod.app.test_client()
    page = c.get("/login").get_data(as_text=True)
    r = c.post("/login?next=//evil.example", data={"csrf": csrf(page), "password": "pw-long-enough"})
    assert r.headers["Location"] in ("/", "/browse/")


def test_subsonic_password_auth_token(library):
    mod = load_app(library, NAVIDROME_URL="http://nd:4533", NAVIDROME_USER="u", NAVIDROME_PASSWORD="sesame")
    import hashlib
    a = mod.subsonic_auth()
    assert a["u"] == "u" and a["t"] == hashlib.md5(("sesame" + a["s"]).encode()).hexdigest()
    c = mod.app.test_client()
    assert "Rescan library" in c.get("/").get_data(as_text=True)
