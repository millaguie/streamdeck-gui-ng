"""The window that manages plugin bundles, both ours and third party ones.

Installed bundles are listed with where each one came from, and the open catalogue can be
browsed to download bundles into our own plugin directory. Downloads happen in a worker thread
so the window stays responsive; nothing downloaded is ever run here.
"""

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from streamdeck_ui.elgato_plugin import marketplace
from streamdeck_ui.elgato_plugin.manifest import PluginManifest, discover_plugin_directories, load_plugin_manifest


class _TokenHolder:
    """Carries the discovery token to the asset lookup without exposing it further."""

    def __init__(self, token: str | None):
        self.token = token


class _WorkerSignals(QObject):
    """Outlives the runnable it belongs to, so its signals stay deliverable."""

    loaded = Signal(dict)
    progress = Signal(int, int)
    done = Signal(str)
    failed = Signal(str)


class _CatalogueLoader(QRunnable):
    """Fetches the online catalogue away from the GUI thread."""

    def __init__(self) -> None:
        super().__init__()
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            entries = marketplace.fetch_catalogue(force_refresh=True)
        except marketplace.MarketplaceError as error:
            self.signals.failed.emit(str(error))
            return
        except Exception as error:  # noqa: BLE001 - reported through the signal
            self.signals.failed.emit(f"{type(error).__name__}: {error}")
            return
        total = len(entries)
        self._done = 0

        def on_progress(_uuid: str, _verdict: object) -> None:
            self._done += 1
            if self._done % 5 == 0 or self._done == total:
                self.signals.progress.emit(self._done, total)

        try:
            compatible = marketplace.filter_compatible_entries(entries, on_progress=on_progress, token=github_token())
        except Exception as error:  # noqa: BLE001 - reported through the signal
            self.signals.failed.emit(f"{type(error).__name__}: {error}")
            return
        self._filtered_out = total - len(compatible)
        self.signals.loaded.emit(compatible)

    def filtered_out_count(self) -> int:
        return getattr(self, "_filtered_out", 0)


def github_token() -> str | None:
    """A GitHub token for the release listings, if this machine has one configured.

    The anonymous API rate limit runs out on a catalogue this size, so we reuse what git-adjacent
    tools already keep: `gh` in its keyring or config, then the GH_TOKEN/GITHUB_TOKEN variables.
    Listing releases is read only public data; the token never leaves requests to api.github.com.
    """
    import os
    import shutil
    import subprocess

    for variable in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(variable)
        if value:
            return value.strip()

    gh = shutil.which("gh")
    if gh:
        try:
            result = subprocess.run(  # noqa: S603 - fixed binary discovered on PATH
                [gh, "auth", "token"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass

    try:
        import yaml

        hosts = yaml.safe_load(open(os.path.expanduser("~/.config/gh/hosts.yml"))) or {}
        for host in (hosts.get("hosts") or {}).values():
            token = host.get("oauth_token") if isinstance(host, dict) else None
            if token:
                return str(token)
    except Exception:  # noqa: BLE001 - best effort discovery only
        pass
    return None


class _InstallWorker(QRunnable):
    """Downloads and installs one package away from the GUI thread."""

    def __init__(self, asset, entry):
        super().__init__()
        self.asset = asset
        self.entry = entry
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            result = marketplace.install_from_asset(self.asset, entry=self.entry)
            marketplace.record_installation(result)
        except Exception as error:  # noqa: BLE001 - reported through the signal
            self.signals.failed.emit(str(error))
            return
        self.signals.done.emit(result.uuid)


class PluginManagerDialog(QDialog):
    """Installed plugins plus the open catalogue, in two tabs."""

    def __init__(self, parent, service=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Plugins")
        self.resize(760, 520)
        self._catalogue: dict[str, marketplace.CatalogueEntry] = {}
        self._pool = QThreadPool(self)
        self._loading = False
        self._installing = False
        self._catalogue_loader: _CatalogueLoader | None = None

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)
        self.tabs.addTab(self._build_installed_tab(), "Installed")
        self.tabs.addTab(self._build_catalogue_tab(), "Browse the catalogue")
        self.tabs.currentChanged.connect(self._tab_changed)

        self._populate_installed()

    # --- Installed plugins -------------------------------------------------------------

    def _build_installed_tab(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)

        note = QLabel("Every plugin bundle this application can see, and where it came from.")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.installed_list = QListWidget(panel)
        self.installed_list.currentItemChanged.connect(lambda *_: self._update_installed_buttons())
        layout.addWidget(self.installed_list, 1)

        self.installed_details = QTextBrowser(panel)
        self.installed_details.setOpenExternalLinks(True)
        self.installed_details.setMaximumHeight(140)
        layout.addWidget(self.installed_details)

        buttons = QHBoxLayout()
        self.install_dir_label = QLabel("", panel)
        self.install_dir_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.install_dir_label.setWordWrap(True)
        buttons.addWidget(self.install_dir_label, 1)

        self.refresh_button = QPushButton("Refresh", panel)
        self.refresh_button.clicked.connect(self._populate_installed)
        buttons.addWidget(self.refresh_button)

        self.remove_button = QPushButton("Remove", panel)
        self.remove_button.clicked.connect(self._remove_selected)
        buttons.addWidget(self.remove_button)
        layout.addLayout(buttons)
        return panel

    def _populate_installed(self) -> None:
        directories = list(self.service.plugin_directories) if self.service else [marketplace.INSTALL_DIRECTORY]
        our_directory = directories[0]
        rows: list[tuple[str, str, Path, "PluginManifest | None"]] = []
        for directory in directories:
            for root in discover_plugin_directories(directory):
                manifest: "PluginManifest | None"
                try:
                    manifest = load_plugin_manifest(root)
                except Exception as error:  # noqa: BLE001 - the list must show what is broken too
                    rows.append((root.name, f"{root} — unreadable: {error}", root, None))
                    continue
                if any(row[3] is not None and row[3].uuid == manifest.uuid for row in rows):
                    continue  # our directory wins over a shared one, same as at runtime
                provenance = marketplace.describe_provenance(root, directories)
                label = f"{manifest.name or manifest.uuid} {manifest.version}  ({provenance})"
                rows.append((label, str(root), root, manifest))

        self.installed_list.clear()
        for label, _path, root, manifest in sorted(rows, key=lambda row: row[0].lower()):
            item = QListWidgetItem(label)
            item.setData(
                Qt.ItemDataRole.UserRole,
                {
                    "path": str(root),
                    "uuid": manifest.uuid if manifest else "",
                    "ours": _inside_any(root, [our_directory]),
                },
            )
            self.installed_list.addItem(item)

        if not rows:
            self.installed_list.addItem("No plugin bundles found.")
        self.install_dir_label.setText(f"Installed here: {our_directory}")
        self._update_installed_buttons()

    def _update_installed_buttons(self, *_) -> None:
        item = self.installed_list.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        can_remove = bool(data and data.get("uuid") and data.get("ours"))
        self.remove_button.setEnabled(can_remove)
        if not item or not data:
            self.installed_details.setPlainText("")
            return
        try:
            manifest = load_plugin_manifest(__import__("pathlib").Path(data["path"]))
        except Exception as error:  # noqa: BLE001 - details may fail for broken bundles
            self.installed_details.setPlainText(str(error))
            return
        provenance = marketplace.describe_provenance(
            __import__("pathlib").Path(data["path"]),
            list(self.service.plugin_directories) if self.service else None,
        )
        actions = ", ".join(action.name or action.uuid for action in manifest.actions) or "none"
        record = marketplace.provenance_of(manifest.uuid) or {}
        lines = [
            f"{manifest.name} {manifest.version} — uuid {manifest.uuid}",
            f"Author: {manifest.author or '-'}   Provenance: {provenance}",
            f"Actions: {actions}",
        ]
        if record.get("sha256"):
            lines.append(f"Downloaded from {record.get('source', '?')} (sha256 {record['sha256'][:16]}…)")
        self.installed_details.setPlainText("\n".join(lines))

    def _remove_selected(self) -> None:
        item = self.installed_list.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not data or not data.get("uuid"):
            return
        answer = QMessageBox.question(
            self,
            "Remove plugin",
            f"Remove {item.text()}?\n\nIts files are deleted. Buttons using it will stop working.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            marketplace.uninstall_bundle(data["uuid"])
        except marketplace.MarketplaceError as error:
            QMessageBox.warning(self, "Remove plugin", str(error))
            return
        self._reload_service()
        self._populate_installed()

    # --- Catalogue ----------------------------------------------------------------------

    def _build_catalogue_tab(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)

        top = QHBoxLayout()
        self.search_box = QLineEdit(panel)
        self.search_box.setPlaceholderText("Search plugins")
        self.search_box.textChanged.connect(lambda *_: self._populate_catalogue_list())
        top.addWidget(self.search_box, 1)
        self.reload_button = QPushButton("Reload catalogue", panel)
        self.reload_button.clicked.connect(self._load_catalogue)
        top.addWidget(self.reload_button)
        layout.addLayout(top)

        self.catalogue_status = QLabel(
            "The catalogue lists open source plugins and links to their repositories.", panel
        )
        self.catalogue_status.setWordWrap(True)
        layout.addWidget(self.catalogue_status)

        self.catalogue_list = QListWidget(panel)
        self.catalogue_list.currentItemChanged.connect(lambda *_: self._update_install_button())
        layout.addWidget(self.catalogue_list, 1)

        self.catalogue_details = QTextBrowser(panel)
        self.catalogue_details.setOpenExternalLinks(True)
        self.catalogue_details.setMaximumHeight(140)
        layout.addWidget(self.catalogue_details)

        bottom = QHBoxLayout()
        self.progress = QProgressBar(panel)
        self.progress.setVisible(False)
        bottom.addWidget(self.progress)
        self.package_combo = QComboBox(panel)
        self.package_combo.setVisible(False)
        bottom.addWidget(self.package_combo, 1)
        self.install_button = QPushButton("Install", panel)
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self._install_selected)
        bottom.addWidget(self.install_button)
        layout.addLayout(bottom)
        return panel

    def _tab_changed(self, index: int) -> None:
        if index == 1 and not self._catalogue and not self._loading:
            self._load_catalogue()

    def _load_catalogue(self) -> None:
        self.catalogue_status.setText("Loading the catalogue…")
        self.reload_button.setEnabled(False)
        loader = _CatalogueLoader()
        self._catalogue_loader = loader
        self._loading = True
        loader.signals.progress.connect(self._catalogue_progress)
        loader.signals.loaded.connect(self._catalogue_ready)
        loader.signals.failed.connect(self._catalogue_failed)
        self._pool.start(loader)

    def _catalogue_progress(self, done: int, total: int) -> None:
        self.catalogue_status.setText(f"Checking which plugins run on this platform… {done}/{total}")

    def _catalogue_ready(self, entries: dict) -> None:
        self._loading = False
        self._catalogue = entries
        filtered = self._catalogue_loader.filtered_out_count() if self._catalogue_loader else 0
        self.reload_button.setEnabled(True)
        suffix = f" {filtered} plugins that do not run on this platform are hidden." if filtered else ""
        self.catalogue_status.setText(
            f"{len(entries)} plugins in the open catalogue. Third party code: review it.{suffix}"
        )
        self._populate_catalogue_list()

    def _catalogue_failed(self, message: str) -> None:
        self._loading = False
        self.reload_button.setEnabled(True)
        self.catalogue_status.setText(f"The catalogue could not be loaded: {message}")

    def _populate_catalogue_list(self) -> None:
        needle = self.search_box.text().strip().lower()
        self.catalogue_list.clear()
        for entry in sorted(self._catalogue.values(), key=lambda item: item.name.lower()):
            if needle and needle not in f"{entry.name} {entry.author} {entry.uuid}".lower():
                continue
            item = QListWidgetItem(f"{entry.name} — {entry.author}")
            item.setData(Qt.ItemDataRole.UserRole, entry.uuid)
            self.catalogue_list.addItem(item)

    def _selected_entry(self) -> marketplace.CatalogueEntry | None:
        item = self.catalogue_list.currentItem()
        uuid = item.data(Qt.ItemDataRole.UserRole) if item else None
        return self._catalogue.get(uuid) if uuid else None

    def _update_install_button(self, *_) -> None:
        entry = self._selected_entry()
        self.install_button.setEnabled(False)
        self.package_combo.clear()
        self.package_combo.setVisible(False)
        if entry is None:
            self.catalogue_details.setPlainText("")
            return
        lines = [f"{entry.name} — by {entry.author}", f"ID: {entry.uuid}"]
        if entry.description:
            lines.append(entry.description)
        if entry.repository:
            lines.append(f'Repository: <a href="{entry.repository}">{entry.repository}</a>')
        lines.append(
            "This plugin comes from a third party. Its code runs with your user's rights; install it only if you trust it."
        )
        self.catalogue_details.setHtml("<br>".join(lines))
        if entry.uuid in _installed_uuids():
            self.catalogue_status.setText(f"{entry.name} is already installed. Use Refresh/Remove first.")
            return
        self.catalogue_status.setText(
            f"{entry.name} by {entry.author}. Source: {entry.repository or 'not stated'}. "
            "Third party code runs with your user's rights."
        )
        assets = marketplace.find_bundle_assets(entry, session=_TokenHolder(github_token()))
        if not assets:
            self.catalogue_status.setText(
                f"{entry.name} publishes no downloadable package (or GitHub refused to list its releases). "
                f"Get it from {entry.repository or 'its repository'} and copy the .sdPlugin folder into the plugins directory."
            )
            return
        for asset in assets:
            self.package_combo.addItem(f"{asset.filename} ({asset.tag})", userData=asset)
        self.package_combo.setVisible(True)
        self.install_button.setEnabled(True)

    def _install_selected(self) -> None:
        entry = self._selected_entry()
        asset = self.package_combo.currentData()
        if entry is None or asset is None:
            return
        answer = QMessageBox.question(
            self,
            "Install plugin",
            f"Download and install {entry.name}?\n\nFrom: {asset.url}\n\n"
            "The code comes from a third party. Install it only if you trust it.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.install_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)

        worker_obj = _InstallWorker(asset, entry)
        self._installing = True
        worker_obj.signals.done.connect(self._install_done)
        worker_obj.signals.failed.connect(self._install_failed)
        self._pool.start(worker_obj)

    def _install_done(self, uuid: str) -> None:
        self._installing = False
        self.progress.setVisible(False)
        self.install_button.setEnabled(True)
        self._reload_service()
        self.catalogue_status.setText(f"Installed {uuid}.")
        self._populate_installed()

    def _install_failed(self, message: str) -> None:
        self._installing = False
        self.progress.setVisible(False)
        self.install_button.setEnabled(True)
        QMessageBox.warning(self, "Install plugin", message)

    def _reload_service(self) -> None:
        if self.service is not None and hasattr(self.service, "reload_plugins"):
            self.service.reload_plugins()


def _installed_uuids() -> set[str]:
    found = set()
    for root in discover_plugin_directories(marketplace.INSTALL_DIRECTORY):
        try:
            found.add(load_plugin_manifest(root).uuid)
        except Exception:  # noqa: BLE001
            continue
    return found


def _inside_any(path, directories) -> bool:
    import pathlib

    directory = pathlib.Path(str(directories[0])) if directories else marketplace.INSTALL_DIRECTORY
    return marketplace._inside(pathlib.Path(str(path)), directory)
