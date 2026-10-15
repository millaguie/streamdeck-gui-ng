"""The application facing half of the official plugin host.

Three rules shape this module:

* Only buttons on the page that is currently shown are placed. That is what the protocol means
  by ``willAppear``/``willDisappear``, and it is why the page does not have to
  travel with a key index: a placed key is always a key of the visible page.
* Plugins never touch the hardware. Their image becomes an overlay that the display pipeline
  composes like any other filter, so dimming, rotation and the display lock keep working and
  nothing fights over a key.
* The WebSocket server is a Qt object and the app builds its server object before
  ``QApplication`` exists, so the host is created on ``start()``. Deck and key events reach us
  from the device monitor thread, so they hop over on a Qt signal and are handled on the
  thread that owns the sockets.
"""

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Iterable, Optional

from PySide6.QtCore import QObject, Qt, Signal

from streamdeck_ui.elgato_plugin.manifest import PluginManifest, discover_plugin_directories, load_plugin_manifest
from streamdeck_ui.elgato_plugin.runtime import PluginRuntime
from streamdeck_ui.elgato_plugin.server import ElgatoPluginHost

logger = logging.getLogger(__name__)

DEFAULT_PLUGIN_DIRECTORY = os.path.expanduser(os.path.join("~", ".streamdeck_ui", "elgato_plugins"))
# Official format bundles other launchers already put on this machine. Ours comes first, so a
# bundle we installed shadows a same named one found here.
SHARED_PLUGIN_DIRECTORY = os.path.expanduser(os.path.join("~", ".config", "opendeck", "plugins"))
DEFAULT_SETTINGS_FILE = os.path.expanduser(os.path.join("~", ".streamdeck_ui", "elgato_plugins.json"))


@dataclass(frozen=True)
class ButtonRef:
    """A button the app asked a plugin to own."""

    serial: str
    page: int
    button: int


@dataclass(frozen=True)
class Binding:
    """What a button says about the official action it runs."""

    button: int
    plugin_uuid: str
    action_uuid: str
    settings: dict = field(default_factory=dict)

    def to_map(self) -> dict:
        return {
            "button": self.button,
            "plugin_uuid": self.plugin_uuid,
            "action_uuid": self.action_uuid,
            "settings": self.settings,
        }

    @staticmethod
    def from_map(data: dict) -> "Binding":
        return Binding(
            button=data["button"],
            plugin_uuid=data["plugin_uuid"],
            action_uuid=data["action_uuid"],
            settings=dict(data.get("settings") or {}),
        )


@dataclass
class Overlay:
    """What a plugin currently wants a key to show."""

    image: Optional[str] = None
    title: Optional[str] = None


class _Bridge(QObject):
    """Carries work onto the thread that owns the plugin sockets."""

    work = Signal(str, "QVariantMap")

    def __init__(self, service: "ElgatoPluginService"):
        super().__init__()
        self.service = service
        self.work.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, kind: str, payload: dict) -> None:
        handler = getattr(self.service, "_do_" + kind, None)
        if handler is None:
            logger.warning("Nobody knows how to handle plugin work %s", kind)
            return
        handler(**{key: value for key, value in payload.items()})


class ElgatoPluginService:
    """Runs official format plugins on behalf of `StreamDeckServer`."""

    def __init__(
        self,
        repaint: Optional[Callable[[str, int, int], None]] = None,
        url_opener: Optional[Callable[[str], None]] = None,
        on_settings: Optional[Callable[[str, int, int, dict], None]] = None,
        plugin_directories: Optional[Iterable[str]] = None,
        settings_file: str = DEFAULT_SETTINGS_FILE,
        application_version: str = "",
        host: Optional[ElgatoPluginHost] = None,
        runtime: Optional[PluginRuntime] = None,
    ):
        self.repaint = repaint
        self.url_opener = url_opener
        self.on_settings = on_settings
        self.plugin_directories = [
            Path(item) for item in (plugin_directories or [DEFAULT_PLUGIN_DIRECTORY, SHARED_PLUGIN_DIRECTORY])
        ]
        self.settings_file = Path(settings_file)
        self.application_version = application_version

        self.plugins: dict[str, PluginManifest] = {}
        self.plugin_settings: dict[str, dict[str, Any]] = {}
        self.overlays: dict[str, dict[int, Overlay]] = {}
        self.pages: dict[str, int] = {}

        self._signatures: dict[ButtonRef, tuple] = {}
        self._contexts: dict[ButtonRef, str] = {}
        self._image_files: dict[tuple[str, int], str] = {}
        self._scratch = Path(tempfile.mkdtemp(prefix="streamdeck-elgato-"))

        self.sink = _ServiceSink(self)
        self.host = host
        self.runtime = runtime
        self.bridge: Optional[_Bridge] = None
        self.load_settings()
        self.load_plugins()

    @property
    def port(self) -> int:
        return self.runtime.port if self.runtime is not None else 0

    def start(self) -> int:
        """Creates the Qt pieces and starts listening. Call once, after QApplication exists."""
        if self.runtime is None:
            self.host = ElgatoPluginHost()
            runtime = PluginRuntime(
                self.host,
                self.sink,
                application_version=self.application_version,
                persist=self.save_settings,
                on_settings=self.plugin_settings_changed,
            )
            runtime.plugins.update(self.plugins)
            runtime.plugin_settings = self.plugin_settings
            self.runtime = runtime
        if self.bridge is None:
            self.bridge = _Bridge(self)
        return self.runtime.start()

    def stop(self) -> None:
        if self.runtime is not None:
            self.runtime.stop()
        self.overlays.clear()
        self._contexts.clear()
        self._signatures.clear()

    def load_plugins(self) -> list[PluginManifest]:
        loaded: list[PluginManifest] = []
        for directory in self.plugin_directories:
            if not directory.is_dir():
                continue
            for root in discover_plugin_directories(directory):
                try:
                    manifest = load_plugin_manifest(root)
                except Exception as error:
                    logger.warning("Ignoring %s: %s", root, error)
                    continue
                if manifest.uuid in self.plugins:
                    logger.info("Ignoring %s because %s is already loaded", root, manifest.uuid)
                    continue
                self.plugins[manifest.uuid] = manifest
                loaded.append(manifest)
        if loaded:
            logger.info("Official format plugins available: %s", ", ".join(item.uuid for item in loaded))
        if self.runtime is not None:
            self.runtime.plugins.update(self.plugins)
        return loaded

    def reload_plugins(self) -> list:
        """Forgets what was loaded and reads the plugin directories again.

        The launcher only starts a plugin when its page is shown, so refreshing this table right
        after an install or uninstall takes effect the next time a button using it appears.
        Bundles that stayed running keep running until they are stopped normally; a bundle whose
        program is still alive under the same uuid would clash with a fresh start of its
        replacement, and the user can restart the application to be sure everything is clean.
        """
        self.plugins.clear()
        if self.runtime is not None:
            self.runtime.plugins.clear()
        return self.load_plugins()

    def actions_for(self, plugin_uuid: str) -> list:
        manifest = self.plugins.get(plugin_uuid)
        return list(manifest.actions) if manifest is not None else []

    def button_options(self) -> list[tuple[str, str, str]]:
        """Every (plugin uuid, action uuid, label) the user can put on a key."""
        options = []
        for manifest in self.plugins.values():
            for action in manifest.actions:
                options.append((manifest.uuid, action.uuid, action.name or action.uuid))
        return sorted(options)

    def deck_opened(self, serial: str, device_info: dict) -> None:
        self._post("deck", action="open", serial=serial, info=device_info)

    def deck_closed(self, serial: str) -> None:
        self._post("deck", action="close", serial=serial, info={})

    def sync_page(self, serial: str, page: int, bindings: Iterable[Binding]) -> None:
        self._post(
            "page",
            serial=serial,
            page=page,
            bindings=[binding.to_map() for binding in bindings],
        )

    def key_pressed(self, serial: str, button: int, pressed: bool) -> None:
        self._post("key", serial=serial, button=button, pressed=pressed)

    def dial_turned(self, serial: str, button: int, ticks: int) -> None:
        self._post("dial", serial=serial, button=button, ticks=ticks)

    def overlay_for(self, serial: str, page: int, button: int) -> Optional[Overlay]:
        """What the display pipeline should draw instead of the button's own look."""
        if self.pages.get(serial) != page:
            return None
        return self.overlays.get(serial, {}).get(button)

    def _post(self, kind: str, **payload) -> None:
        if self.bridge is None:
            getattr(self, "_do_" + kind)(**payload)
        else:
            self.bridge.work.emit(kind, payload)

    def _do_deck(self, action: str, serial: str, info: dict) -> None:
        if self.runtime is None:
            return
        if action == "open":
            self.overlays.setdefault(serial, {})
            self.runtime.device_connected(serial, dict(info))
            return
        for ref in [item for item in self._contexts if item.serial == serial]:
            self._unplace(ref)
        self.overlays.pop(serial, None)
        self.pages.pop(serial, None)
        self.runtime.device_disconnected(serial)

    def _do_page(self, serial: str, page: int, bindings: list) -> None:
        if self.runtime is None:
            return
        self.pages[serial] = page
        wanted = {binding.button: binding for binding in (Binding.from_map(item) for item in bindings)}

        for ref in [item for item in self._contexts if item.serial == serial and item.page != page]:
            self._unplace(ref)

        for ref in [item for item in self._contexts if item.serial == serial and item.page == page]:
            if ref.button not in wanted or self._signature(wanted[ref.button]) != self._signatures.get(ref):
                self._unplace(ref)

        for button, binding in wanted.items():
            ref = ButtonRef(serial, page, button)
            if ref not in self._contexts:
                self._place(ref, binding)

    def _do_key(self, serial: str, button: int, pressed: bool) -> None:
        if self.runtime is None:
            return
        context = self._context_for(serial, button)
        if context is None:
            return
        if pressed:
            self.runtime.press(context)
        else:
            self.runtime.release(context)

    def _do_dial(self, serial: str, button: int, ticks: int) -> None:
        if self.runtime is None:
            return
        context = self._context_for(serial, button)
        if context is not None:
            self.runtime.rotate(context, ticks)

    def plugin_settings_changed(self, context: str, settings: dict) -> None:
        """Hands a plugin's own settings back to whoever owns the button it sits on."""
        if self.on_settings is None:
            return
        for ref, known in self._contexts.items():
            if known == context:
                self.on_settings(ref.serial, ref.page, ref.button, dict(settings))
                return
        logger.warning("Settings arrived for %s which is not on any button", context)

    def _do_paint(self, serial: str, page: int, button: int) -> None:
        if self.repaint is not None:
            self.repaint(serial, page, button)

    def _context_for(self, serial: str, button: int) -> Optional[str]:
        page = self.pages.get(serial)
        if page is None:
            return None
        return self._contexts.get(ButtonRef(serial, page, button))

    def _place(self, ref: ButtonRef, binding: Binding) -> None:
        if self.runtime is None or binding.plugin_uuid not in self.plugins:
            logger.warning("Button %s runs %s which is not installed", ref, binding.plugin_uuid)
            return
        try:
            placement = self.runtime.place(
                binding.plugin_uuid,
                binding.action_uuid,
                ref.serial,
                ref.button,
                settings=binding.settings,
            )
        except KeyError as error:
            logger.warning("Cannot bind %s: %s", ref, error)
            return
        self._contexts[ref] = placement.context
        self._signatures[ref] = self._signature(binding)

    def _unplace(self, ref: ButtonRef) -> None:
        context = self._contexts.pop(ref, None)
        self._signatures.pop(ref, None)
        if context is None:
            return
        self.runtime.unplace(context)
        self.overlays.get(ref.serial, {}).pop(ref.button, None)
        self._repaint(ref)

    def _signature(self, binding: Binding) -> tuple:
        return (binding.plugin_uuid, binding.action_uuid, json.dumps(binding.settings, sort_keys=True, default=str))

    def _repaint(self, ref: ButtonRef) -> None:
        self._post("paint", serial=ref.serial, page=ref.page, button=ref.button)

    def set_image(self, serial: str, button: int, image: Optional[bytes]) -> None:
        page = self.pages.get(serial)
        if page is None:
            logger.warning("Plugin painted %s key %s before the page was known", serial, button)
            return
        overlay = self.overlays.setdefault(serial, {}).setdefault(button, Overlay())
        if image is None:
            overlay.image = None
        else:
            path = self._image_file(serial, button)
            try:
                Path(path).write_bytes(image)
            except OSError as error:
                logger.warning("Could not store the plugin image for %s key %s: %s", serial, button, error)
                return
            overlay.image = path
        self._repaint(ButtonRef(serial, page, button))

    def set_title(self, serial: str, button: int, title: str) -> None:
        page = self.pages.get(serial)
        if page is None:
            return
        overlay = self.overlays.setdefault(serial, {}).setdefault(button, Overlay())
        overlay.title = title
        self._repaint(ButtonRef(serial, page, button))

    def _image_file(self, serial: str, button: int) -> str:
        existing = self._image_files.get((serial, button))
        if existing is not None:
            return existing
        path = str(self._scratch / f"{serial}-{button}.png")
        self._image_files[(serial, button)] = path
        return path

    def open_url(self, url: str) -> None:
        if self.url_opener is not None:
            self.url_opener(url)
            return
        import webbrowser

        webbrowser.open(url)

    def load_settings(self) -> None:
        if not self.settings_file.is_file():
            return
        try:
            document = json.loads(self.settings_file.read_text())
        except (OSError, ValueError) as error:
            logger.warning("Ignoring %s: %s", self.settings_file, error)
            return
        stored = document.get("global_settings", {})
        if isinstance(stored, dict):
            self.plugin_settings.update({key: value for key, value in stored.items() if isinstance(value, dict)})

    def save_settings(self) -> None:
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            document = {"global_settings": self.plugin_settings}
            temporary = self.settings_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(document, indent=4))
            temporary.replace(self.settings_file)
        except OSError as error:
            logger.warning("Cannot store plugin settings: %s", error)


class _ServiceSink:
    """Turns plugin requests into overlays and app level actions."""

    def __init__(self, service: ElgatoPluginService):
        self.service = service

    def set_key_image(self, device_id: str, key: int, image: Optional[bytes]) -> None:
        self.service.set_image(device_id, key, image)

    def set_key_title(self, device_id: str, key: int, title: Optional[str]) -> None:
        self.service.set_title(device_id, key, title or "")

    def show_ok(self, device_id: str, key: int) -> None:
        logger.info("Plugin confirmed %s key %s", device_id, key)

    def show_alert(self, device_id: str, key: int) -> None:
        logger.warning("Plugin alerted on %s key %s", device_id, key)

    def open_url(self, url: str) -> None:
        self.service.open_url(url)
