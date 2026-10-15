import io
import json
import stat
import zipfile

import pytest

from streamdeck_ui.elgato_plugin import marketplace
from streamdeck_ui.elgato_plugin.manifest import ManifestError

MANIFEST = {"Name": "Demo", "UUID": "com.example.demo", "Version": "1.0", "CodePath": "bin/linux.app"}


def bundle_zip(tmp_path, uuid="com.example.demo", extra=None):
    """A zip laid out like a published package: one <uuid>.sdPlugin folder inside."""
    path = tmp_path / f"{uuid}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{uuid}.sdPlugin/manifest.json", json.dumps(MANIFEST))
        for name, payload in (extra or {}).items():
            archive.writestr(f"{uuid}.sdPlugin/{name}", payload)
    return path


def test_github_repo_parsing():
    assert marketplace.github_repo("https://github.com/open/repo") == ("open", "repo")
    assert marketplace.github_repo("https://github.com/open/repo.git/") == ("open", "repo")
    assert marketplace.github_repo("https://gitlab.com/open/repo") is None


def test_catalogue_parsing():
    entries = marketplace._parse_catalogue(
        {"com.example.demo": {"name": "Demo", "author": "me", "repository": "https://github.com/open/repo"}}
    )
    assert entries["com.example.demo"].name == "Demo"
    assert entries["com.example.demo"].icon_url.endswith("icons/com.example.demo.png")


def test_catalogue_that_is_not_an_object_is_refused():
    with pytest.raises(marketplace.MarketplaceError):
        marketplace._parse_catalogue([1, 2])


def test_asset_name_filtering():
    assert marketplace._could_hold_bundle("com.example.demo.zip", "com.example.demo")
    assert marketplace._could_hold_bundle("com.example.demo.sdPlugin.streamDeckPlugin", "com.example.demo")
    assert marketplace._could_hold_bundle("package.zip", "com.example.demo")
    assert not marketplace._could_hold_bundle("com.other.thing.zip", "com.example.demo")
    assert not marketplace._could_hold_bundle("profile.streamdeckprofile", "com.example.demo")
    assert not marketplace._could_hold_bundle("notes.txt", "com.example.demo")


def test_install_unpacks_and_records_provenance(tmp_path):
    archive = bundle_zip(tmp_path, extra={"bin/linux.app": "#!/bin/sh\n"})
    result = marketplace.install_bundle(archive, destination=tmp_path / "plugins")
    assert result.uuid == "com.example.demo"
    assert (result.path / "manifest.json").is_file()
    assert not list((tmp_path / "plugins").glob("*.part"))

    marketplace.record_installation(result, tmp_path / "meta.json")
    provenance = marketplace.provenance_of("com.example.demo", tmp_path / "meta.json")
    assert provenance["sha256"] == result.sha256


def test_install_refuses_existing_bundle_without_overwrite(tmp_path):
    archive = bundle_zip(tmp_path)
    destination = tmp_path / "plugins"
    marketplace.install_bundle(archive, destination=destination)
    with pytest.raises(marketplace.MarketplaceError, match="already exists"):
        marketplace.install_bundle(archive, destination=destination)
    marketplace.install_bundle(archive, destination=destination, overwrite=True)
    assert (destination / "com.example.demo.sdPlugin" / "manifest.json").is_file()


def test_install_refuses_uuid_mismatch(tmp_path):
    archive = bundle_zip(tmp_path)
    with pytest.raises(marketplace.MarketplaceError, match="not com.other.uuid"):
        marketplace.install_bundle(archive, expected_uuid="com.other.uuid", destination=tmp_path / "plugins")


def test_install_refuses_non_archive(tmp_path):
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip")
    with pytest.raises(marketplace.MarketplaceError, match="not a readable zip"):
        marketplace.install_bundle(junk, destination=tmp_path / "plugins")


def test_install_refuses_traversal(tmp_path):
    path = tmp_path / "evil.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("com.example.demo.sdPlugin/manifest.json", json.dumps(MANIFEST))
        archive.writestr("../escaped.txt", "nope")
    with pytest.raises(marketplace.MarketplaceError, match="outside"):
        marketplace.install_bundle(path, destination=tmp_path / "plugins")
    assert not (tmp_path / "escaped.txt").exists()


def test_install_refuses_symlink_entries(tmp_path):
    path = tmp_path / "link.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("com.example.demo.sdPlugin/manifest.json", json.dumps(MANIFEST))
        info = zipfile.ZipInfo("com.example.demo.sdPlugin/link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(marketplace.MarketplaceError, match="symbolic link"):
        marketplace.install_bundle(path, destination=tmp_path / "plugins")


def test_install_refuses_archive_without_bundle(tmp_path):
    path = tmp_path / "loose.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("readme.txt", "hello")
    with pytest.raises(marketplace.MarketplaceError, match="does not hold"):
        marketplace.install_bundle(path, destination=tmp_path / "plugins")


def test_uninstall_only_removes_installed_bundles(tmp_path):
    archive = bundle_zip(tmp_path)
    destination = tmp_path / "plugins"
    marketplace.install_bundle(archive, destination=destination)

    removed = marketplace.uninstall_bundle("com.example.demo", directory=destination)
    assert not removed.exists()
    with pytest.raises(marketplace.MarketplaceError, match="not installed"):
        marketplace.uninstall_bundle("com.example.demo", directory=destination)


def test_provenance_of_unknown_plugin_is_none(tmp_path):
    assert marketplace.provenance_of("com.example.demo", tmp_path / "missing.json") is None


def test_uuid_less_bundle_installs_under_its_folder_name(tmp_path):
    """Many bundles take their identity from the folder name; staging must not break that."""
    path = tmp_path / "no-uuid.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("com.example.no-uuid.sdPlugin/manifest.json", json.dumps({"Name": "NoUUID", "Version": "1"}))
    result = marketplace.install_bundle(path, destination=tmp_path / "plugins")
    assert result.uuid == "com.example.no-uuid"
    leftovers = [p.name for p in (tmp_path / "plugins").iterdir() if not p.name.endswith(".sdPlugin")]
    assert leftovers == []


def test_leftover_installing_directory_does_not_get_discovered(tmp_path):
    """A crash mid-install leaves a dot folder that discovery must ignore."""
    staging = tmp_path / ".installing-com.example.demo" / "com.example.demo.sdPlugin"
    staging.mkdir(parents=True)
    (staging / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    from streamdeck_ui.elgato_plugin.manifest import discover_plugin_directories

    assert discover_plugin_directories(tmp_path) == []


def make_zip_entry(tmp_path, manifest, uuid="com.example.demo"):
    path = bundle_zip(tmp_path, uuid=uuid)
    return marketplace.BundleAsset(url=str(path), tag="v1", filename=path.name)


def _entry_with_local_bundle(tmp_path, manifest, uuid):
    path = tmp_path / f"{uuid}-peek.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{uuid}.sdPlugin/manifest.json", json.dumps(manifest))
    return marketplace.CatalogueEntry(uuid=uuid, name=uuid, author="a", repository="", download_url=str(path))


def test_compatibility_hidden_web_plugin(tmp_path):
    entry = _entry_with_local_bundle(
        tmp_path, {"Name": "Webby", "UUID": "com.example.webby", "CodePath": "index.html"}, "com.example.webby"
    )
    result = marketplace.filter_compatible_entries({entry.uuid: entry}, cache_path=tmp_path / "compat.json")
    assert result == {}


def test_compatibility_hidden_mac_only_bundle(tmp_path):
    manifest = {
        "Name": "MacOnly",
        "UUID": "com.example.mac",
        "OS": [{"Platform": "macos"}, {"Platform": "windows"}],
        "CodePath": "bin/app.app",
    }
    entry = _entry_with_local_bundle(tmp_path, manifest, "com.example.mac")
    result = marketplace.filter_compatible_entries({entry.uuid: entry}, cache_path=tmp_path / "compat.json")
    assert result == {}


def test_compatibility_keeps_linux_and_unknown(tmp_path):
    good = _entry_with_local_bundle(
        tmp_path, {"Name": "Good", "UUID": "com.example.good", "CodePath": "bin/linux.bin"}, "com.example.good"
    )
    bad = _entry_with_local_bundle(
        tmp_path,
        {"Name": "WinOnly", "UUID": "com.example.win", "OS": [{"Platform": "windows"}], "CodePath": "app.exe"},
        "com.example.win",
    )
    result = marketplace.filter_compatible_entries(
        {good.uuid: good, bad.uuid: bad}, cache_path=tmp_path / "compat.json"
    )
    assert set(result) == {good.uuid}


def test_compatibility_cache_survives_between_passes(tmp_path):
    entry = _entry_with_local_bundle(
        tmp_path, {"Name": "Good", "UUID": "com.example.good", "CodePath": "bin/linux.bin"}, "com.example.good"
    )
    cache = tmp_path / "compat.json"
    first = marketplace.filter_compatible_entries({entry.uuid: entry}, cache_path=cache)
    assert set(first) == {entry.uuid}
    second = marketplace.filter_compatible_entries({entry.uuid: entry}, cache_path=cache)
    assert set(second) == {entry.uuid}


def test_runs_on_manifest_property():
    import tempfile

    from streamdeck_ui.elgato_plugin.manifest import PluginManifest, load_plugin_manifest

    root = Path(tempfile.mkdtemp()) / "com.example.demo.sdPlugin"
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps({"Name": "Demo", "Version": "1", "CodePath": "bin/linux.app"}), encoding="utf-8"
    )
    manifest = load_plugin_manifest(root)
    assert manifest.runs_on() is True

    (root / "manifest.json").write_text(
        json.dumps({"Name": "Web", "Version": "1", "CodePath": "index.html"}), encoding="utf-8"
    )
    assert load_plugin_manifest(root).runs_on() is False

    (root / "manifest.json").write_text(
        json.dumps({"Name": "Win", "Version": "1", "OS": [{"Platform": "windows"}], "CodePath": "app.exe"}),
        encoding="utf-8",
    )
    assert load_plugin_manifest(root).runs_on() is False


from pathlib import Path  # noqa: E402  (test uses it above)
