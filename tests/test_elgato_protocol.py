import json

import pytest

from streamdeck_ui.elgato_plugin.protocol import (
    HARDWARE_AND_SOFTWARE,
    ElgatoProtocol,
    HostActions,
    ProtocolError,
    snake,
)

PLUGIN_UUID = "com.example.plugin"
CONTEXT = "a1b2c3d4e5f6"


@pytest.fixture
def calls():
    return []


@pytest.fixture
def actions(calls):
    def record(name):
        def handler(**kwargs):
            calls.append((name, kwargs))

        return handler

    return HostActions(
        set_image=record("set_image"),
        set_xywh_image=record("set_xywh_image"),
        set_title=record("set_title"),
        set_state=record("set_state"),
        show_ok=record("show_ok"),
        show_alert=record("show_alert"),
        set_settings=record("set_settings"),
        set_global_settings=record("set_global_settings"),
        get_settings=record("get_settings"),
        get_global_settings=record("get_global_settings"),
        send_to_property_inspector=record("send_to_property_inspector"),
        send_to_plugin=record("send_to_plugin"),
        open_url=record("open_url"),
        log_message=record("log_message"),
        switch_to_profile=record("switch_to_profile"),
    )


@pytest.fixture
def protocol(actions, calls):
    host = ElgatoProtocol(actions=actions, logger=lambda level, message: calls.append((level, message)))
    host.handle("plugin-socket", json.dumps({"event": "registerPlugin", "uuid": PLUGIN_UUID}))
    host.bind_context(
        CONTEXT,
        connection_id="plugin-socket",
        action="com.example.plugin.action",
        device_id="DEV1",
        coordinates={"row": 1, "column": 2},
        settings={"greeting": "hola"},
    )
    return host


def send(protocol, connection_id, **message):
    return protocol.handle(connection_id, json.dumps(message))


def payloads(replies):
    return [(reply.connection_id, reply.message) for reply in replies]


def test_plugin_must_register_first(protocol):
    with pytest.raises(ProtocolError):
        send(protocol, "stranger", event="setImage", context=CONTEXT, payload={"image": "x"})


def test_registration_without_uuid_is_rejected(protocol):
    with pytest.raises(ProtocolError):
        send(protocol, "stranger", event="registerPlugin")


def test_property_inspector_registers_separately(protocol):
    send(protocol, "pi-socket", event="registerPropertyInspector", uuid=PLUGIN_UUID)

    assert protocol.connections["pi-socket"].kind == "property-inspector"
    assert protocol.connections["plugin-socket"].kind == "plugin"


def test_log_message_works_before_registration(protocol, calls):
    replies = send(protocol, "stranger", event="logMessage", payload={"message": "hola"})

    assert replies == []
    assert ("log_message", {"message": "hola"}) in calls


def test_get_settings_answers_with_did_receive_settings(protocol):
    replies = payloads(send(protocol, "plugin-socket", event="getSettings", context=CONTEXT))

    assert len(replies) == 1
    connection_id, message = replies[0]
    assert connection_id == "plugin-socket"
    assert message["event"] == "didReceiveSettings"
    assert message["context"] == CONTEXT
    assert message["payload"]["settings"] == {"greeting": "hola"}


def test_get_settings_asks_the_host_first(protocol, calls):
    protocol.actions.get_settings = lambda context: {"refrescado": True}

    _, message = payloads(send(protocol, "plugin-socket", event="getSettings", context=CONTEXT))[0]

    assert message["payload"]["settings"] == {"refrescado": True}


def test_set_settings_from_property_inspector_reaches_the_plugin(protocol, calls):
    send(protocol, "pi-socket", event="registerPropertyInspector", uuid=PLUGIN_UUID)
    protocol.attach_property_inspector(CONTEXT, "pi-socket")

    replies = payloads(
        send(
            protocol,
            "pi-socket",
            event="setSettings",
            context=PLUGIN_UUID,
            action="com.example.plugin.action",
            targetContext=CONTEXT,
            payload={"greeting": "adios"},
        )
    )

    assert protocol.contexts[CONTEXT].settings == {"greeting": "adios"}
    assert {connection_id for connection_id, _ in replies} == {"plugin-socket", "pi-socket"}
    assert all(message["event"] == "didReceiveSettings" for _, message in replies)
    assert (
        "set_settings",
        {"context": CONTEXT, "action": "com.example.plugin.action", "settings": {"greeting": "adios"}},
    ) in calls


def test_global_settings_fan_out_to_every_connection_of_the_plugin(protocol):
    send(protocol, "pi-socket", event="registerPropertyInspector", uuid=PLUGIN_UUID)

    replies = payloads(
        send(protocol, "plugin-socket", event="setGlobalSettings", context=PLUGIN_UUID, payload={"theme": "dark"})
    )

    assert {connection_id for connection_id, _ in replies} == {"plugin-socket", "pi-socket"}
    assert all(message["payload"]["settings"] == {"theme": "dark"} for _, message in replies)


def test_get_global_settings_replies_only_to_the_requester(protocol):
    send(protocol, "pi-socket", event="registerPropertyInspector", uuid=PLUGIN_UUID)
    protocol.global_settings[PLUGIN_UUID] = {"theme": "dark"}

    replies = payloads(send(protocol, "plugin-socket", event="getGlobalSettings", context=PLUGIN_UUID))

    assert replies == [
        ("plugin-socket", {"event": "didReceiveGlobalSettings", "payload": {"settings": {"theme": "dark"}}})
    ]


def test_set_image_forwards_image_and_target(protocol, calls):
    send(protocol, "plugin-socket", event="setImage", context=CONTEXT, payload={"image": "data:image/jpeg;base64,AAA"})

    assert calls == [
        (
            "set_image",
            {
                "context": CONTEXT,
                "action": "com.example.plugin.action",
                "image": "data:image/jpeg;base64,AAA",
                "target": 0,
            },
        )
    ]


def test_set_image_defaults_to_hardware_and_software(protocol, calls):
    send(protocol, "plugin-socket", event="setImage", context=CONTEXT, payload={"image": "x"})

    assert calls[0][1]["target"] is HARDWARE_AND_SOFTWARE


def test_set_title_and_state_update_the_binding(protocol, calls):
    send(protocol, "plugin-socket", event="setTitle", context=CONTEXT, payload={"title": "Hola", "target": 2})
    send(protocol, "plugin-socket", event="setState", context=CONTEXT, payload={"state": 1})

    assert protocol.contexts[CONTEXT].title == "Hola"
    assert protocol.contexts[CONTEXT].state == 1
    assert calls[0][1]["target"] == 2
    assert calls[1] == ("set_state", {"context": CONTEXT, "action": "com.example.plugin.action", "state": 1})


def test_send_to_property_inspector_only_reaches_a_bound_inspector(protocol):
    replies = send(
        protocol,
        "plugin-socket",
        event="sendToPropertyInspector",
        context=CONTEXT,
        action="com.example.plugin.action",
        payload={"hello": 1},
    )

    assert replies == []

    send(protocol, "pi-socket", event="registerPropertyInspector", uuid=PLUGIN_UUID)
    protocol.attach_property_inspector(CONTEXT, "pi-socket")

    forwarded = payloads(
        send(
            protocol,
            "plugin-socket",
            event="sendToPropertyInspector",
            context=CONTEXT,
            action="com.example.plugin.action",
            payload={"hello": 1},
        )
    )

    assert forwarded == [
        (
            "pi-socket",
            {
                "event": "sendToPropertyInspector",
                "context": CONTEXT,
                "action": "com.example.plugin.action",
                "payload": {"hello": 1},
            },
        )
    ]


def test_send_to_plugin_from_inspector_is_routed_to_the_key(protocol):
    send(protocol, "pi-socket", event="registerPropertyInspector", uuid=PLUGIN_UUID)

    replies = payloads(
        send(
            protocol,
            "pi-socket",
            event="sendToPlugin",
            context=PLUGIN_UUID,
            action="com.example.plugin.action",
            targetContext=CONTEXT,
            payload={"cmd": "reload"},
        )
    )

    assert replies[0][0] == "plugin-socket"
    assert replies[0][1]["payload"] == {"cmd": "reload"}


def test_open_url_and_switch_to_profile_reach_the_host(protocol, calls):
    send(protocol, "plugin-socket", event="openUrl", context=PLUGIN_UUID, payload={"url": "https://example.test"})
    send(
        protocol,
        "plugin-socket",
        event="switchToProfile",
        context=PLUGIN_UUID,
        device="DEV1",
        payload={"profile": "Default"},
    )

    assert ("open_url", {"url": "https://example.test"}) in calls
    assert ("switch_to_profile", {"device": "DEV1", "profile": "Default"}) in calls


def test_unknown_events_are_ignored_without_killing_the_connection(protocol, calls):
    replies = send(protocol, "plugin-socket", event="someFutureEvent", context=CONTEXT)

    assert replies == []
    assert ("warning", "ignoring unsupported event someFutureEvent from com.example.plugin") in calls


def test_unwired_host_action_is_reported_not_raised(calls):
    host = ElgatoProtocol(logger=lambda level, message: calls.append((level, message)))
    host.handle("plugin-socket", json.dumps({"event": "registerPlugin", "uuid": PLUGIN_UUID}))
    host.bind_context(CONTEXT, "plugin-socket", "com.example.plugin.action", "DEV1")

    assert host.handle("plugin-socket", json.dumps({"event": "showAlert", "context": CONTEXT})) == []
    assert ("warning", "host action show_alert is not wired") in calls


@pytest.mark.parametrize("raw", ["no es json", '{"payload":{}}', '"una cadena"', '{"event":""}'])
def test_malformed_messages_are_protocol_errors(protocol, raw):
    with pytest.raises(ProtocolError):
        protocol.handle("plugin-socket", raw)


def test_image_on_an_unknown_context_is_a_protocol_error(protocol):
    with pytest.raises(ProtocolError):
        send(protocol, "plugin-socket", event="setImage", context="noexiste", payload={"image": "x"})


def test_will_appear_payload_matches_the_official_shape(protocol):
    message = protocol.will_appear(CONTEXT)

    assert message == {
        "event": "willAppear",
        "context": CONTEXT,
        "action": "com.example.plugin.action",
        "device": "DEV1",
        "payload": {
            "settings": {"greeting": "hola"},
            "positions": {},
            "coordinates": {"row": 1, "column": 2},
            "controller": "Keypad",
            "state": 0,
            "isInMultiAction": False,
        },
    }


def test_key_and_dial_events_carry_coordinates(protocol):
    message = protocol.key_event("keyDown", CONTEXT)

    assert message["payload"]["coordinates"] == {"row": 1, "column": 2}
    assert message["payload"]["settings"] == {"greeting": "hola"}

    dial = protocol.dial_rotate(CONTEXT, -1)
    assert dial["event"] == "dialRotate"
    assert dial["payload"]["ticks"] == -1


def test_device_events_feed_both_the_official_and_the_openaction_reader(protocol):
    """Elgato reads deviceIdentifier and flat rows/columns, OpenAction reads device, deviceInfo.id
    and deviceInfo.size. Its serde structs reject a message missing any of them."""
    message = protocol.device_did_connect("DEV1", {"name": "Stream Deck MK.2", "size": {"columns": 5, "rows": 3}})

    assert message["event"] == "deviceDidConnect"
    assert message["device"] == "DEV1"
    assert message["deviceIdentifier"] == "DEV1"
    assert message["deviceInfo"]["id"] == "DEV1"
    assert message["deviceInfo"]["rows"] == 3
    assert message["deviceInfo"]["columns"] == 5
    assert message["deviceInfo"]["size"] == {"rows": 3, "columns": 5}
    assert message["deviceInfo"]["type"] == 1

    gone = protocol.device_did_disconnect("DEV1")
    assert gone["device"] == "DEV1"
    assert gone["deviceIdentifier"] == "DEV1"


def test_event_rejects_names_the_host_does_not_send(protocol):
    with pytest.raises(ProtocolError):
        protocol.event("setImage")


def test_closing_a_connection_forgets_its_contexts(protocol):
    protocol.close("plugin-socket")

    assert protocol.connections == {}
    assert protocol.contexts == {}


def test_top_level_fields_outside_the_payload_are_kept(protocol):
    envelope = protocol.parse('{"event":"switchToProfile","device":"DEV1","payload":{"profile":"P"}}')

    assert envelope.extra == {"device": "DEV1"}
    assert envelope.payload == {"profile": "P"}


@pytest.mark.parametrize(
    "event,expected",
    [("setImage", "set_image"), ("setXYWHImage", "set_xywh_image"), ("getGlobalSettings", "get_global_settings")],
)
def test_snake_maps_official_event_names(event, expected):
    assert snake(event) == expected
