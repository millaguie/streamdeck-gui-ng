"""Glue between the plugin host, the plugin processes and the hardware.

The host starts the plugin process and the process connects back and registers, so a plugin is
launched when one of its actions is placed, never when it registers. Each
launch gets a fresh session uuid, which is what the process sends back in ``registerPlugin``
and what we route replies to; the plugin's own UUID stays the identity used for settings.

Everything that touches hardware or configuration goes through a sink object, so this is
testable without a deck attached.
"""

from dataclasses import dataclass, field
import base64
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from streamdeck_ui.elgato_plugin.launcher import (
    PluginProcess,
    app_info,
    launch_plugin,
    new_connection_uuid,
)
from streamdeck_ui.elgato_plugin.manifest import (
    PLATFORM_LINUX,
    PluginManifest,
    discover_plugin_directories,
    load_plugin_manifest,
)
from streamdeck_ui.elgato_plugin.protocol import Connection, HostActions
from streamdeck_ui.elgato_plugin.server import ElgatoPluginHost

logger = logging.getLogger(__name__)


@dataclass
class Placement:
    """One instance of a plugin action living on a key of a device."""

    context: str
    plugin_uuid: str
    action_uuid: str
    device_id: str
    key: int
    controller: str = "Keypad"
    settings: dict[str, Any] = field(default_factory=dict)
    state: int = 0
    title: Optional[str] = None


class PluginRuntime:
    """Runs official format plugins and applies what they ask for."""

    def __init__(
        self,
        host: ElgatoPluginHost,
        sink,
        language: str = "en",
        platform: str = PLATFORM_LINUX,
        application_version: str = "",
        plugin_version: str = "",
        persist: Optional[Callable[[], None]] = None,
        on_settings: Optional[Callable[[str, dict], None]] = None,
        launcher: Callable[..., PluginProcess] = launch_plugin,
    ):
        self.host = host
        self.sink = sink
        self.language = language
        self.platform = platform
        self.application_version = application_version
        self.plugin_version = plugin_version
        self.persist = persist
        self.on_settings = on_settings
        self._launch = launcher

        self.plugins: dict[str, PluginManifest] = {}
        self.processes: dict[str, PluginProcess] = {}
        self.placements: dict[str, Placement] = {}
        self.plugin_settings: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, list[dict]] = {}
        self._connections: dict[str, str] = {}
        self._sessions: dict[str, str] = {}
        self._failed_to_start: set[str] = set()

        host.protocol.actions = self._actions()
        host.protocol.on_register = self._on_register
        host.protocol.on_disconnect = self._on_disconnect

    @property
    def port(self) -> int:
        return self.host.port

    @property
    def connected_plugins(self) -> list[str]:
        return sorted(self._connections)

    def start(self) -> int:
        return self.host.start()

    def stop(self) -> None:
        for plugin_uuid, process in list(self.processes.items()):
            logger.info("Plugin %s stopped with code %s", plugin_uuid, process.stop())
        self.processes.clear()
        self.host.stop()

    def add_plugin(self, manifest: PluginManifest) -> None:
        self.plugins[manifest.uuid] = manifest

    def load_plugins_from(self, *directories: Path) -> list[PluginManifest]:
        loaded: list[PluginManifest] = []
        for directory in directories:
            for root in discover_plugin_directories(Path(directory)):
                try:
                    manifest = load_plugin_manifest(root)
                except Exception as error:
                    logger.warning("Ignoring %s: %s", root, error)
                    continue
                self.add_plugin(manifest)
                loaded.append(manifest)
        return loaded

    def place(
        self,
        plugin_uuid: str,
        action_uuid: str,
        device_id: str,
        key: int,
        controller: str = "Keypad",
        settings: Optional[dict] = None,
        positions: Optional[dict] = None,
        coordinates: Optional[dict] = None,
    ) -> Placement:
        """Puts an action on a key, starting its plugin, which then gets a willAppear."""
        manifest = self.plugins.get(plugin_uuid)
        if manifest is None:
            raise KeyError(f"unknown plugin {plugin_uuid}")
        if manifest.action(action_uuid) is None:
            raise KeyError(f"{plugin_uuid} has no action {action_uuid}")

        placement = Placement(
            context=new_connection_uuid(),
            plugin_uuid=plugin_uuid,
            action_uuid=action_uuid,
            device_id=device_id,
            key=key,
            controller=controller,
            settings=dict(settings or {}),
        )
        self.placements[placement.context] = placement
        self.host.protocol.bind_context(
            placement.context,
            connection_id=self._connections.get(plugin_uuid, ""),
            action=action_uuid,
            device_id=device_id,
            controller=controller,
            coordinates=coordinates,
            positions=positions,
            settings=placement.settings,
        )
        self._queue(plugin_uuid, self.host.protocol.will_appear(placement.context))
        self._ensure_running(manifest, placement)
        return placement

    def unplace(self, context: str) -> None:
        placement = self.placements.pop(context, None)
        if placement is None:
            return
        connection_id = self._connections.get(placement.plugin_uuid)
        message = self.host.protocol.will_disappear(context) if context in self.host.protocol.contexts else None
        self.host.protocol.unbind_context(context)
        if connection_id is not None and message is not None:
            self.host.send(connection_id, message)

    def press(self, context: str) -> None:
        self._to_placement(context, lambda: self.host.protocol.key_event("keyDown", context))

    def release(self, context: str) -> None:
        self._to_placement(context, lambda: self.host.protocol.key_event("keyUp", context))

    def tap(self, context: str) -> None:
        self._to_placement(context, lambda: self.host.protocol.key_event("touchTap", context))

    def rotate(self, context: str, ticks: int) -> None:
        self._to_placement(context, lambda: self.host.protocol.dial_rotate(context, ticks))

    def device_connected(self, device_id: str, device_info: dict) -> None:
        message = self.host.protocol.device_did_connect(device_id, device_info)
        self.host.broadcast(message)
        for plugin_uuid in self.plugins:
            if plugin_uuid not in self._connections:
                self._pending.setdefault(plugin_uuid, []).append(message)

    def device_disconnected(self, device_id: str) -> None:
        self.host.broadcast(self.host.protocol.device_did_disconnect(device_id))

    def plugin_of(self, session_uuid: str) -> str:
        """The manifest uuid a session uuid was handed out for."""
        return self._sessions.get(session_uuid, session_uuid)

    def _queue(self, plugin_uuid: str, message: dict) -> None:
        connection_id = self._connections.get(plugin_uuid)
        if connection_id:
            self.host.send(connection_id, message)
        else:
            self._pending.setdefault(plugin_uuid, []).append(message)

    def _to_placement(self, context: str, build: Callable[[], dict]) -> None:
        """Sends an event about a key, but only while that key still holds the action."""
        if context not in self.placements:
            return
        self.host.send_to_context(context, build())

    def _on_register(self, connection: Connection) -> None:
        if connection.kind != "plugin":
            return
        plugin_uuid = self.plugin_of(connection.uuid)
        self._connections[plugin_uuid] = connection.connection_id
        for placement in self.placements.values():
            if placement.plugin_uuid != plugin_uuid:
                continue
            binding = self.host.protocol.contexts.get(placement.context)
            if binding is not None:
                binding.connection_id = connection.connection_id
        for message in self._pending.pop(plugin_uuid, []):
            self.host.send(connection.connection_id, message)

    def _on_disconnect(self, connection: Connection) -> None:
        plugin_uuid = self.plugin_of(connection.uuid)
        if self._connections.get(plugin_uuid) == connection.connection_id:
            self._connections.pop(plugin_uuid, None)
            for binding in self.host.protocol.contexts.values():
                if binding.connection_id == connection.connection_id:
                    binding.connection_id = ""

    def _ensure_running(self, manifest: PluginManifest, placement: Placement) -> None:
        if manifest.uuid in self.processes and self.processes[manifest.uuid].running:
            return
        if manifest.uuid in self._failed_to_start:
            return
        if manifest.code_kind != "executable":
            logger.warning(
                "Plugin %s is a web plugin (%s); hosting it needs a web view we do not have yet",
                manifest.uuid,
                manifest.code_path,
            )
            return

        session_uuid = new_connection_uuid()
        self._sessions[session_uuid] = manifest.uuid
        try:
            process = self._launch(
                manifest,
                self.host.port,
                session_uuid,
                app_info(
                    manifest,
                    platform=self.platform,
                    language=self.language,
                    application_version=self.application_version,
                    plugin_version=self.plugin_version,
                ),
                action_information(manifest, placement),
                platform=self.platform,
                log=self._plugin_log(manifest.uuid),
                on_exit=lambda code, uuid=manifest.uuid: logger.info("Plugin %s exited with %s", uuid, code),
            )
        except (OSError, ValueError) as error:
            # A bundle can name a program that is not in it: a CodePathLin pointing at a build
            # that was never shipped for Linux. That is one dead key, not one dead page, and it
            # must not come out as a traceback in the middle of a page sync.
            self._sessions.pop(session_uuid, None)
            self._failed_to_start.add(manifest.uuid)
            logger.warning("Plugin %s cannot start: %s", manifest.uuid, error)
            return
        self.processes[manifest.uuid] = process
        logger.info("Started plugin %s (%s)", manifest.uuid, process.arguments[0])

    def _plugin_log(self, plugin_uuid: str) -> Callable[[str, str], None]:
        def log(level: str, message: str) -> None:
            sink_log = getattr(self.sink, "log", None)
            if sink_log is not None:
                sink_log(plugin_uuid, level, message)
            getattr(logger, level if level in ("debug", "info", "warning", "error") else "info")(
                "[plugin %s] %s", plugin_uuid, message
            )

        return log

    def _actions(self) -> HostActions:
        return HostActions(
            set_image=self._set_image,
            set_xywh_image=self._set_xywh_image,
            set_title=self._set_title,
            set_state=self._set_state,
            show_ok=self._feedback("show_ok"),
            show_alert=self._feedback("show_alert"),
            set_settings=self._set_settings,
            set_global_settings=self._set_global_settings,
            get_settings=self._get_settings,
            get_global_settings=self._get_global_settings,
            open_url=self._open_url,
            log_message=self._log_message,
        )

    def _placement(self, context: str) -> Optional[Placement]:
        return self.placements.get(context)

    def _set_image(self, context: str, action: str, image: Optional[str], target: int) -> None:
        placement = self._placement(context)
        setter = getattr(self.sink, "set_key_image", None)
        if placement is None or setter is None:
            return
        setter(placement.device_id, placement.key, decode_data_url(image) if image else None)

    def _set_xywh_image(self, context: str, action: str, payload: dict) -> None:
        placement = self._placement(context)
        setter = getattr(self.sink, "set_xywh_image", None)
        if placement is not None and setter is not None:
            setter(placement.device_id, placement.key, payload)

    def _set_title(self, context: str, action: str, title: Optional[str], target: int) -> None:
        placement = self._placement(context)
        setter = getattr(self.sink, "set_key_title", None)
        if placement is None:
            return
        placement.title = title
        if setter is not None:
            setter(placement.device_id, placement.key, title)

    def _set_state(self, context: str, action: str, state: int) -> None:
        placement = self._placement(context)
        if placement is not None:
            placement.state = state

    def _feedback(self, name: str) -> Callable[..., None]:
        def show(context: str, action: str) -> None:
            placement = self._placement(context)
            setter = getattr(self.sink, name, None)
            if placement is not None and setter is not None:
                setter(placement.device_id, placement.key)

        return show

    def _get_settings(self, context: str) -> dict:
        placement = self._placement(context)
        return dict(placement.settings) if placement is not None else {}

    def _set_settings(self, context: str, action: str, settings: dict) -> None:
        placement = self._placement(context)
        if placement is None:
            return
        placement.settings = dict(settings)
        self._save()
        if self.on_settings is not None:
            try:
                self.on_settings(context, dict(placement.settings))
            except Exception as error:  # noqa: BLE001 - a plugin must not take the app down
                logger.warning("Nobody could store the settings of %s: %s", context, error)

    def _get_global_settings(self, uuid: str) -> dict:
        return dict(self.plugin_settings.get(self.plugin_of(uuid), {}))

    def _set_global_settings(self, uuid: str, settings: dict) -> None:
        self.plugin_settings[self.plugin_of(uuid)] = dict(settings)
        self._save()

    def _open_url(self, url: Optional[str]) -> None:
        opener = getattr(self.sink, "open_url", None)
        if opener is not None and url:
            opener(url)

    def _log_message(self, message: str) -> None:
        logger.info("[plugin message] %s", message)

    def _save(self) -> None:
        if self.persist is not None:
            self.persist()


def action_information(manifest: PluginManifest, placement: Placement) -> dict:
    return {
        "action": placement.action_uuid,
        "context": placement.context,
        "payload": {"settings": placement.settings},
    }


def decode_data_url(value: Optional[str]) -> Optional[bytes]:
    """Official plugins send images as ``data:image/jpeg;base64,....``."""
    if not value:
        return None
    _, _, encoded = value.partition(",")
    try:
        return base64.b64decode(encoded)
    except (ValueError, TypeError):
        logger.warning("Plugin sent image data we cannot decode")
        return None
