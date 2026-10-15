import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from streamdeck_ui.elgato_plugin import marketplace  # noqa: E402
from streamdeck_ui.plugin_manager_dialog import PluginManagerDialog  # noqa: E402

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


MANIFEST = {"Name": "Mine", "UUID": "net.millaguie.test.clock", "Version": "1.0"}


class FakeService:
    def __init__(self, directories):
        self.plugin_directories = directories
        self.reloads = 0

    def reload_plugins(self):
        self.reloads += 1


def make_bundle(root, manifest=None):
    directory = root / "net.millaguie.test.clock.sdPlugin"
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps(manifest or MANIFEST), encoding="utf-8")
    return directory


def test_installed_list_shows_provenance(qapp, tmp_path, monkeypatch):
    ours = tmp_path / "ours" / "elgato_plugins"
    shared = tmp_path / "shared"
    make_bundle(ours)
    other = shared / "com.other.thing.sdPlugin"
    other.mkdir(parents=True)
    (other / "manifest.json").write_text(
        json.dumps({"Name": "Other", "UUID": "com.other.thing", "Version": "2"}), encoding="utf-8"
    )
    monkeypatch.setattr(marketplace, "INSTALL_DIRECTORY", ours)

    dialog = PluginManagerDialog(None, service=FakeService([ours, shared]))
    labels = [dialog.installed_list.item(i).text() for i in range(dialog.installed_list.count())]
    assert any("Mine" in label and "installed here" in label for label in labels)
    assert any("Other" in label and "shared directory" in label for label in labels)

    dialog._populate_installed()  # refreshing again must not duplicate rows
    assert dialog.installed_list.count() == 2


def test_marketplace_provenance_is_named(qapp, tmp_path, monkeypatch):
    ours = tmp_path / "elgato_plugins"
    ours.mkdir(parents=True)
    bundle = make_bundle(ours)
    monkeypatch.setattr(marketplace, "INSTALL_DIRECTORY", ours)
    result = marketplace.InstallResult(
        uuid="net.millaguie.test.clock",
        path=bundle,
        name="Mine",
        version="1.0",
        source_url="https://github.com/open/repo/releases/download/v9/pkg.zip",
        sha256="abc",
        tag="v9",
        repository="https://github.com/open/repo",
        catalogue=True,
    )
    marketplace.record_installation(result)
    provenance = marketplace.describe_provenance(bundle, [ours])
    assert "OpenAction catalogue v9" in provenance


def test_installed_bundle_can_be_removed(qapp, tmp_path, monkeypatch):
    ours = tmp_path / "elgato_plugins"
    make_bundle(ours)
    monkeypatch.setattr(marketplace, "INSTALL_DIRECTORY", ours)

    service = FakeService([ours])
    dialog = PluginManagerDialog(None, service=service)
    item = dialog.installed_list.item(0)
    assert item is not None
    from PySide6.QtCore import Qt

    data = item.data(Qt.ItemDataRole.UserRole)
    assert data["uuid"] == "net.millaguie.test.clock"
    assert data["ours"] is True

    marketplace.uninstall_bundle("net.millaguie.test.clock")
    dialog._populate_installed()
    assert "No plugin bundles found." in dialog.installed_list.item(0).text()


def test_catalogue_search_filters(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(marketplace, "INSTALL_DIRECTORY", tmp_path / "nothing")
    dialog = PluginManagerDialog(None, service=FakeService([tmp_path / "nothing"]))
    dialog._catalogue = {
        "a.b.c": marketplace.CatalogueEntry("a.b.c", "Alpha", "me", ""),
        "d.e.f": marketplace.CatalogueEntry("d.e.f", "Beta", "you", ""),
    }
    dialog._populate_catalogue_list()
    assert dialog.catalogue_list.count() == 2
    dialog.search_box.setText("beta")
    assert dialog.catalogue_list.count() == 1
