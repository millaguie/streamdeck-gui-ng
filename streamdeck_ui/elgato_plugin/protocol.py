"""Elgato Stream Deck plugin protocol, transport agnostic.

The message shapes and the event vocabulary are the ones a bundle's own property inspector
library sends and expects (``registerPlugin``, ``willAppear``, ``setImage`` ...), cross checked
against the traffic a bundle produces while it talks to this host. Where the protocol leaves a
detail open, the choice made here is called out in a comment.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

REGISTER_PLUGIN = "registerPlugin"
REGISTER_PROPERTY_INSPECTOR = "registerPropertyInspector"

PLUGIN_TO_HOST_EVENTS = {
    "getSettings",
    "setSettings",
    "getGlobalSettings",
    "setGlobalSettings",
    "setImage",
    "setXYWHImage",
    "setTitle",
    "setState",
    "showOk",
    "showAlert",
    "sendToPropertyInspector",
    "sendToPlugin",
    "openUrl",
    "logMessage",
    "switchToProfile",
}

HOST_TO_PLUGIN_EVENTS = {
    "deviceDidConnect",
    "deviceDidDisconnect",
    "applicationDidLaunch",
    "applicationDidTerminate",
    "systemDidWakeUp",
    "willAppear",
    "willDisappear",
    "keyDown",
    "keyUp",
    "dialDown",
    "dialUp",
    "dialRotate",
    "touchTap",
    "titleParametersDidChange",
    "propertyInspectorDidAppear",
    "propertyInspectorDidDisappear",
    "didReceiveSettings",
    "didReceiveGlobalSettings",
    "sendToPlugin",
    "sendToPropertyInspector",
}

HARDWARE_AND_SOFTWARE = 0
HARDWARE_ONLY = 1
SOFTWARE_ONLY = 2


class ProtocolError(Exception):
    """Raised when a peer sends something we cannot make sense of."""


@dataclass
class ContextBinding:
    """An action instance on a deck, identified by the context the host created."""

    context: str
    connection_id: str
    action: str
    device_id: str
    controller: str = "Keypad"
    coordinates: dict[str, int] = field(default_factory=dict)
    positions: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    state: int = 0
    title: Optional[str] = None
    property_inspector: Optional[str] = None


@dataclass
class Connection:
    connection_id: str
    kind: str
    uuid: str


@dataclass
class HostActions:
    """What the host does when a plugin asks for something.

    Every entry is optional so a caller can wire only what it needs; unwired actions are
    logged instead of failing silently.
    """

    set_image: Optional[Callable[..., None]] = None
    set_xywh_image: Optional[Callable[..., None]] = None
    set_title: Optional[Callable[..., None]] = None
    set_state: Optional[Callable[..., None]] = None
    show_ok: Optional[Callable[..., None]] = None
    show_alert: Optional[Callable[..., None]] = None
    set_settings: Optional[Callable[..., None]] = None
    set_global_settings: Optional[Callable[..., None]] = None
    get_settings: Optional[Callable[..., dict]] = None
    get_global_settings: Optional[Callable[..., dict]] = None
    send_to_property_inspector: Optional[Callable[..., None]] = None
    send_to_plugin: Optional[Callable[..., None]] = None
    open_url: Optional[Callable[..., None]] = None
    log_message: Optional[Callable[..., None]] = None
    switch_to_profile: Optional[Callable[..., None]] = None


@dataclass
class Envelope:
    event: str
    context: Optional[str] = None
    action: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)
    target_context: Optional[str] = None
    uuid: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def context_or_uuid(self) -> Optional[str]:
        return self.context or self.uuid


@dataclass
class Reply:
    """A message the host must write back to one connection."""

    connection_id: str
    message: dict[str, Any]


class ElgatoProtocol:
    """Owns the state of every peer speaking the official plugin protocol."""

    def __init__(
        self,
        actions: Optional[HostActions] = None,
        logger: Optional[Callable[..., None]] = None,
        on_register: Optional[Callable[["Connection"], None]] = None,
        on_disconnect: Optional[Callable[["Connection"], None]] = None,
    ):
        self.actions = actions or HostActions()
        self.on_register = on_register
        self.on_disconnect = on_disconnect
        self._log = logger or (lambda *args: None)
        self.connections: dict[str, Connection] = {}
        self.contexts: dict[str, ContextBinding] = {}
        self.global_settings: dict[str, dict[str, Any]] = {}

    def close(self, connection_id: str) -> None:
        """Forgets everything known about a peer that went away."""
        connection = self.connections.pop(connection_id, None)
        if connection is not None and self.on_disconnect is not None:
            self.on_disconnect(connection)
        for context in [c for c, binding in self.contexts.items() if binding.connection_id == connection_id]:
            del self.contexts[context]

    def bind_context(
        self,
        context: str,
        connection_id: str,
        action: str,
        device_id: str,
        controller: str = "Keypad",
        coordinates: Optional[dict[str, int]] = None,
        positions: Optional[dict[str, Any]] = None,
        settings: Optional[dict[str, Any]] = None,
    ) -> ContextBinding:
        binding = ContextBinding(
            context=context,
            connection_id=connection_id,
            action=action,
            device_id=device_id,
            controller=controller,
            coordinates=coordinates or {},
            positions=positions or {},
            settings=settings or {},
        )
        self.contexts[context] = binding
        return binding

    def unbind_context(self, context: str) -> None:
        self.contexts.pop(context, None)

    def attach_property_inspector(self, context: str, pi_connection_id: str) -> None:
        binding = self.contexts.get(context)
        if binding is None:
            raise ProtocolError(f"unknown context {context}")
        binding.property_inspector = pi_connection_id

    def parse(self, raw: str) -> Envelope:
        try:
            message = json.loads(raw)
        except ValueError as error:
            raise ProtocolError(f"not valid JSON: {error}") from error
        if not isinstance(message, dict):
            raise ProtocolError("message is not a JSON object")
        event = message.get("event")
        if not isinstance(event, str) or not event:
            raise ProtocolError("message has no event")
        reserved = ("event", "context", "action", "payload", "targetContext", "uuid")
        payload = message.get("payload")
        return Envelope(
            event=event,
            context=message.get("context"),
            action=message.get("action"),
            payload=payload if isinstance(payload, dict) else {},
            target_context=message.get("targetContext"),
            uuid=message.get("uuid"),
            extra={key: value for key, value in message.items() if key not in reserved},
        )

    def handle(self, connection_id: str, raw: str) -> list[Reply]:
        """Feeds one peer message in and returns what must be written back."""
        envelope = self.parse(raw)

        if envelope.event == "logMessage":
            self._dispatch("log_message", message=envelope.payload.get("message", ""))
            return []

        if envelope.event in (REGISTER_PLUGIN, REGISTER_PROPERTY_INSPECTOR):
            return self._register(connection_id, envelope)

        connection = self.connections.get(connection_id)
        if connection is None:
            raise ProtocolError(f"connection {connection_id} is not registered")

        if envelope.event not in PLUGIN_TO_HOST_EVENTS:
            # Nothing in the protocol rules out a peer inventing a newer event, and one
            # must not take the whole host down.
            self._log("warning", f"ignoring unsupported event {envelope.event} from {connection.uuid}")
            return []

        handler = getattr(self, f"_on_{snake(envelope.event)}", None)
        if handler is None:
            self._log("warning", f"no handler for event {envelope.event}")
            return []
        return handler(connection, envelope)

    def event(self, name: str, context: Optional[str] = None, action: Optional[str] = None, **extra: Any) -> dict:
        if name not in HOST_TO_PLUGIN_EVENTS:
            raise ProtocolError(f"{name} is not a host to plugin event")
        message: dict[str, Any] = {"event": name}
        if context is not None:
            message["context"] = context
        if action is not None:
            message["action"] = action
        message.update(extra)
        return message

    def device_did_connect(self, device_id: str, device_info: dict[str, Any]) -> dict:
        """Two dialects want the same device described differently: this one names the deck
        ``deviceIdentifier`` and puts rows and columns straight in ``deviceInfo``, OpenAction names
        it ``device`` and wants ``deviceInfo.id`` plus a nested ``deviceInfo.size``. Both ignore
        fields they do not know, so one message feeds both.
        """
        size = device_info.get("size") or {}
        rows = size.get("rows", device_info.get("rows", 0))
        columns = size.get("columns", device_info.get("columns", 0))
        information = {
            **device_info,
            "id": device_info.get("id", device_id),
            "rows": rows,
            "columns": columns,
            "type": device_info.get("type", 1),
            "size": {"rows": rows, "columns": columns},
        }
        return self.event("deviceDidConnect", device=device_id, deviceIdentifier=device_id, deviceInfo=information)

    def device_did_disconnect(self, device_id: str) -> dict:
        return self.event("deviceDidDisconnect", device=device_id, deviceIdentifier=device_id)

    def _instance_payload(self, binding: ContextBinding) -> dict:
        """The payload every action event carries; OpenAction rejects the event without it."""
        return {
            "settings": binding.settings,
            "positions": binding.positions,
            "coordinates": binding.coordinates,
            "controller": binding.controller,
            "state": binding.state,
            "isInMultiAction": False,
        }

    def will_appear(self, context: str) -> dict:
        binding = self._binding(context)
        return self.event(
            "willAppear",
            context=context,
            action=binding.action,
            device=binding.device_id,
            payload=self._instance_payload(binding),
        )

    def will_disappear(self, context: str) -> dict:
        binding = self._binding(context)
        return self.event(
            "willDisappear",
            context=context,
            action=binding.action,
            device=binding.device_id,
            payload=self._instance_payload(binding),
        )

    def key_event(self, name: str, context: str) -> dict:
        binding = self._binding(context)
        return self.event(
            name,
            context=context,
            action=binding.action,
            device=binding.device_id,
            payload=self._instance_payload(binding),
        )

    def dial_rotate(self, context: str, ticks: int) -> dict:
        message = self.key_event("dialRotate", context)
        message["payload"]["ticks"] = ticks
        return message

    def did_receive_settings(self, context: str) -> dict:
        binding = self._binding(context)
        return self.event(
            "didReceiveSettings", context=context, action=binding.action, payload={"settings": binding.settings}
        )

    def did_receive_global_settings(self, uuid: str) -> dict:
        return self.event("didReceiveGlobalSettings", payload={"settings": self.global_settings.get(uuid, {})})

    def _register(self, connection_id: str, envelope: Envelope) -> list[Reply]:
        if not envelope.uuid:
            raise ProtocolError("registration without a uuid")
        kind = "plugin" if envelope.event == REGISTER_PLUGIN else "property-inspector"
        connection = Connection(connection_id, kind, envelope.uuid)
        self.connections[connection_id] = connection
        if self.on_register is not None:
            self.on_register(connection)
        return []

    def _peer_connections(self, uuid: str) -> list[Connection]:
        return [connection for connection in self.connections.values() if connection.uuid == uuid]

    def _binding(self, context: Optional[str]) -> ContextBinding:
        binding = self.contexts.get(context or "")
        if binding is None:
            raise ProtocolError(f"unknown context {context}")
        return binding

    def _dispatch(self, action_name: str, **kwargs: Any) -> None:
        action = getattr(self.actions, action_name, None)
        if action is None:
            self._log("warning", f"host action {action_name} is not wired")
            return
        action(**kwargs)

    def _targets(self, envelope: Envelope) -> list[str]:
        """Connections that must receive a reply for this message."""
        binding = self.contexts.get(envelope.context or "")
        targets = [connection.connection_id for connection in self._peer_connections(envelope.context_or_uuid or "")]
        if binding is not None and binding.property_inspector:
            targets.append(binding.property_inspector)
        return list(dict.fromkeys(targets))

    def _settings_target(self, envelope: Envelope) -> ContextBinding:
        if envelope.target_context:
            return self._binding(envelope.target_context)
        return self._binding(envelope.context)

    def _on_get_settings(self, connection: Connection, envelope: Envelope) -> list[Reply]:
        binding = self._binding(envelope.context_or_uuid)
        settings = self.actions.get_settings and self.actions.get_settings(context=binding.context)
        if isinstance(settings, dict):
            binding.settings = settings
        return [Reply(connection.connection_id, self.did_receive_settings(binding.context))]

    def _on_set_settings(self, connection: Connection, envelope: Envelope) -> list[Reply]:
        binding = self._settings_target(envelope)
        binding.settings = envelope.payload
        self._dispatch("set_settings", context=binding.context, action=binding.action, settings=binding.settings)
        return [Reply(target, self.did_receive_settings(binding.context)) for target in self._connections_for(binding)]

    def _on_get_global_settings(self, connection: Connection, envelope: Envelope) -> list[Reply]:
        settings = self.actions.get_global_settings and self.actions.get_global_settings(uuid=connection.uuid)
        if isinstance(settings, dict):
            self.global_settings[connection.uuid] = settings
        return [Reply(connection.connection_id, self.did_receive_global_settings(connection.uuid))]

    def _on_set_global_settings(self, connection: Connection, envelope: Envelope) -> list[Reply]:
        self.global_settings[connection.uuid] = envelope.payload
        self._dispatch("set_global_settings", uuid=connection.uuid, settings=self.global_settings[connection.uuid])
        return [
            Reply(peer.connection_id, self.did_receive_global_settings(connection.uuid))
            for peer in self._peer_connections(connection.uuid)
        ]

    def _on_set_image(self, connection: Connection, envelope: Envelope) -> list[Reply]:
        binding = self._binding(envelope.context)
        self._dispatch(
            "set_image",
            context=binding.context,
            action=binding.action,
            image=envelope.payload.get("image"),
            target=envelope.payload.get("target", HARDWARE_AND_SOFTWARE),
        )
        return []

    def _on_set_xywh_image(self, connection: Connection, envelope: Envelope) -> list[Reply]:
        binding = self._binding(envelope.context)
        self._dispatch("set_xywh_image", context=binding.context, action=binding.action, payload=envelope.payload)
        return []

    def _on_set_title(self, connection: Connection, envelope: Envelope) -> list[Reply]:
        binding = self._binding(envelope.context)
        title = envelope.payload.get("title")
        binding.title = title
        self._dispatch(
            "set_title",
            context=binding.context,
            action=binding.action,
            title=title,
            target=envelope.payload.get("target", HARDWARE_AND_SOFTWARE),
        )
        return []

    def _on_set_state(self, connection: Connection, envelope: Envelope) -> list[Reply]:
        binding = self._binding(envelope.context)
        state = int(envelope.payload.get("state", 0) or 0)
        binding.state = state
        self._dispatch("set_state", context=binding.context, action=binding.action, state=state)
        return []

    def _on_show_ok(self, connection: Connection, envelope: Envelope) -> list[Reply]:
        binding = self._binding(envelope.context)
        self._dispatch("show_ok", context=binding.context, action=binding.action)
        return []

    def _on_show_alert(self, connection: Connection, envelope: Envelope) -> list[Reply]:
        binding = self._binding(envelope.context)
        self._dispatch("show_alert", context=binding.context, action=binding.action)
        return []

    def _on_send_to_property_inspector(self, connection: Connection, envelope: Envelope) -> list[Reply]:
        binding = self._settings_target(envelope)
        self._dispatch(
            "send_to_property_inspector",
            context=binding.context,
            action=binding.action,
            payload=envelope.payload,
        )
        if binding.property_inspector is None:
            return []
        return [
            Reply(
                binding.property_inspector,
                self.event(
                    "sendToPropertyInspector",
                    context=binding.context,
                    action=binding.action,
                    payload=envelope.payload,
                ),
            )
        ]

    def _on_send_to_plugin(self, connection: Connection, envelope: Envelope) -> list[Reply]:
        binding = self._settings_target(envelope)
        self._dispatch(
            "send_to_plugin",
            context=binding.context,
            action=binding.action,
            payload=envelope.payload,
        )
        return [
            Reply(
                binding.connection_id,
                self.event(
                    "sendToPlugin",
                    context=binding.context,
                    action=binding.action,
                    payload=envelope.payload,
                ),
            )
        ]

    def _on_open_url(self, connection: Connection, envelope: Envelope) -> list[Reply]:
        self._dispatch("open_url", url=envelope.payload.get("url"))
        return []

    def _on_switch_to_profile(self, connection: Connection, envelope: Envelope) -> list[Reply]:
        self._dispatch(
            "switch_to_profile",
            device=envelope.extra.get("device"),
            profile=envelope.payload.get("profile"),
        )
        return []

    def _connections_for(self, binding: ContextBinding) -> list[str]:
        targets = [binding.connection_id]
        if binding.property_inspector:
            targets.append(binding.property_inspector)
        return targets


def snake(event: str) -> str:
    expanded = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", event)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", expanded).lower()
