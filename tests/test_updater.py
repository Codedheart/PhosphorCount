import hashlib
import zipfile

import pytest

from phosphor import updater
from phosphor.updater import Release, UpdateError


def test_parse_and_compare_versions():
    assert updater.parse_version("v1.2") == (1, 2)
    assert updater.parse_version("1.10.3") == (1, 10, 3)
    for bad in ("dev", "", None, "v1.2-beta", "main"):
        assert updater.parse_version(bad) is None
    assert updater.is_newer((1, 10), (1, 9))
    assert updater.is_newer((1, 2, 1), (1, 2))
    assert not updater.is_newer((1, 2), (1, 2, 0))
    assert not updater.is_newer((1, 1), (1, 2))


def test_release_from_api():
    data = {"tag_name": "v1.3", "html_url": "https://x/rel", "assets": [
        {"name": "other.zip", "browser_download_url": "https://x/other"},
        {"name": updater.ASSET, "browser_download_url": "https://x/z", "size": 12, "digest": "sha256:ABC"}]}
    r = updater.release_from_api(data)
    assert (r.tag, r.version, r.asset_url, r.size, r.sha256) == ("v1.3", (1, 3), "https://x/z", 12, "abc")
    assert updater.release_from_api({**data, "prerelease": True}) is None
    assert updater.release_from_api({"tag_name": "nightly"}) is None
    assert updater.release_from_api({"tag_name": "v2"}).asset_url is None


def _fake_zip(path, broken=False):
    with zipfile.ZipFile(path, "w") as z:
        if not broken:
            z.writestr("PhosphorCount/PhosphorCount.exe", b"exe")
            z.writestr("PhosphorCount/_internal/base_library.zip", b"lib")
        z.writestr("PhosphorCount/READ ME FIRST.txt", b"hi")
    return path


def _release(zip_path, **kw):
    data = zip_path.read_bytes()
    return Release("v9.9", (9, 9), "https://x", asset_url=zip_path.as_uri(), size=len(data),
                   sha256=hashlib.sha256(data).hexdigest(), **kw)


def test_download_verify_extract(tmp_path):
    src = _fake_zip(tmp_path / "src.zip")
    seen = []
    z = updater.download(_release(src), tmp_path / "stage" / "dl.zip", progress=seen.append)
    assert seen and seen[-1] == 1.0
    root = updater.extract(z, tmp_path / "stage" / "new")
    assert (root / "PhosphorCount.exe").read_bytes() == b"exe"
    assert (root / "_internal").is_dir()


def test_download_rejects_bad_hash_and_size(tmp_path):
    src = _fake_zip(tmp_path / "src.zip")
    rel = _release(src)
    rel.sha256 = "0" * 64
    with pytest.raises(UpdateError, match="CORRUPTED"):
        updater.download(rel, tmp_path / "a.zip")
    rel = _release(src)
    rel.size += 5
    with pytest.raises(UpdateError, match="INCOMPLETE"):
        updater.download(rel, tmp_path / "b.zip")
    with pytest.raises(UpdateError):
        updater.download(Release("v1", (1,), "x"), tmp_path / "c.zip")


def test_extract_rejects_zip_without_app(tmp_path):
    with pytest.raises(UpdateError):
        updater.extract(_fake_zip(tmp_path / "x.zip", broken=True), tmp_path / "out")


def test_location_problem(tmp_path, monkeypatch):
    app = tmp_path / "Documents" / "PhosphorCount"
    (app / "_internal").mkdir(parents=True)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path / "Temp"))
    assert updater.location_problem(app) is None
    monkeypatch.setattr(updater, "_can_write", lambda d: False)
    msg = updater.location_problem(app)
    assert "Documents" in msg and "administrators" in msg and str(app) in msg
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path / "Documents"))
    assert "inside the zip" in updater.location_problem(app)


def test_helper_script(tmp_path):
    bat = updater.write_helper(tmp_path, tmp_path / "My Apps" / "PhosphorCount", tmp_path / "new", "v9.9", 4242)
    text = bat.read_bytes().decode()
    assert "\r\n" in text and 'set "PIDNUM=4242"' in text
    assert f'set "APP={tmp_path / "My Apps" / "PhosphorCount"}"' in text
    assert 'start "" "%APP%\\PhosphorCount.exe"' in text
    assert text.count("goto ") >= 6 and ":restore" in text and ":launch" in text
    with pytest.raises(UpdateError):
        updater.write_helper(tmp_path, tmp_path / "100%", tmp_path / "new", "v1", 1)


def test_take_result(tmp_path):
    (tmp_path / "result.txt").write_text("ok")
    assert updater.take_result(tmp_path) == "ok"
    assert not tmp_path.exists()
    tmp_path.mkdir()
    (tmp_path / "result.txt").write_text("fail")
    (tmp_path / "update.log").write_text("log")
    (tmp_path / "backup").mkdir()
    assert updater.take_result(tmp_path) == "fail"
    assert [p.name for p in tmp_path.iterdir()] == ["update.log"]
    assert updater.take_result(tmp_path) is None
