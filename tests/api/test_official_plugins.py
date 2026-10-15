"""Tests that the server hands official format plugins the keys they own."""

from types import SimpleNamespace

import pytest

from tests.api.helpers import assert_display_handler_used
from tests.common import STREAMDECK_SERIAL


class RecordingRuntime:
    """Stands in for the WebSocket runtime, so these tests do not need Qt or processes."""

    def __init__(self):
        self.plugin_settings = {}
        self.placements = {}
        self.events = []
        self._next = 0

    def start(self):
        return 51000

    def stop(self):
        self.events.append("stop")

    def device_connected(self, serial, device_info):
        self.events.append(("connected", serial, device_info))

    def device_disconnected(self, serial):
        self.events.append(("disconnected", serial))

    def place(self, plugin_uuid, action_uuid, serial, key, settings=None):
        self._next += 1
        context = f"context-{self._next}"
        self.events.append(("place", context, plugin_uuid, action_uuid, key, settings))
        self.placements[context] = (serial, key)
        return SimpleNamespace(context=context)

    def unplace(self, context):
        self.events.append(("unplace", context))
        self.placements.pop(context, None)

    def press(self, context):
        self.events.append(("press", context))

    def release(self, context):
        self.events.append(("release", context))

    def of(self, kind):
        return [event for event in self.events if isinstance(event, tuple) and event[0] == kind]

    def context_at(self, key):
        for context, (serial, placed_key) in self.placements.items():
            if serial == STREAMDECK_SERIAL and placed_key == key:
                return context
        return None


@pytest.fixture
def runtime(api_server):
    service = api_server.elgato_plugins
    service.runtime = RecordingRuntime()
    service.plugins = {"com.test.plugin": SimpleNamespace(uuid="com.test.plugin", actions=[])}
    return service.runtime


def bind(api_server, button, plugin="com.test.plugin", action="com.test.plugin.action"):
    api_server.set_button_official_binding(STREAMDECK_SERIAL, 0, button, plugin, action)


def test_binding_a_button_places_an_instance_for_the_visible_page(api_server, runtime):
    bind(api_server, 3)

    assert [(event[2], event[3], event[4]) for event in runtime.of("place")] == [
        ("com.test.plugin", "com.test.plugin.action", 3)
    ]


def test_the_deck_is_described_to_the_plugins(api_server, runtime):
    deck = api_server.decks_by_serial[STREAMDECK_SERIAL]
    deck.deck_type.return_value = "Stream Deck"

    api_server.elgato_plugins.deck_opened(STREAMDECK_SERIAL, api_server._official_device_info(deck, STREAMDECK_SERIAL))

    connected = [event for event in runtime.events if event[0] == "connected"]
    assert connected[0][1] == STREAMDECK_SERIAL
    assert connected[0][2]["name"] == "Stream Deck"
    assert connected[0][2]["size"] == {"columns": 1, "rows": 1}


def test_a_bound_key_talks_to_the_plugin_and_an_unbound_one_does_not(api_server, runtime):
    bind(api_server, 2)

    api_server.notify_button_press(STREAMDECK_SERIAL, 0, 2, True)
    api_server.notify_button_press(STREAMDECK_SERIAL, 0, 2, False)
    api_server.notify_button_press(STREAMDECK_SERIAL, 0, 5, True)

    assert [event[1] for event in runtime.of("press")] == [runtime.context_at(2)]
    assert [event[1] for event in runtime.of("release")] == [runtime.context_at(2)]


def test_leaving_the_page_stops_the_instances_that_were_running_on_it(api_server, runtime):
    bind(api_server, 1)
    placed = runtime.context_at(1)

    api_server.set_page(STREAMDECK_SERIAL, 1)

    assert [event[1] for event in runtime.of("unplace")] == [placed]
    assert runtime.placements == {}


def test_unbinding_a_button_stops_its_instance(api_server, runtime):
    bind(api_server, 1)
    placed = runtime.context_at(1)

    bind(api_server, 1, action="com.test.plugin.other")

    assert [event[1] for event in runtime.of("unplace")] == [placed]
    assert runtime.context_at(1) is not None


def test_closing_the_deck_stops_its_instances(api_server, runtime):
    bind(api_server, 1)
    placed = runtime.context_at(1)
    api_server.decks_map_id_to_serial["deck-id"] = STREAMDECK_SERIAL

    api_server._cleanup("deck-id", STREAMDECK_SERIAL)

    assert [event[1] for event in runtime.of("unplace")] == [placed]
    assert ("disconnected", STREAMDECK_SERIAL) in runtime.events


def test_a_plugin_image_is_drawn_instead_of_the_button_icon(api_server, runtime, mock_filters):
    bind(api_server, 4)
    api_server.elgato_plugins.sink.set_key_image(STREAMDECK_SERIAL, 4, b"\x89PNG the plugin's art")

    image_filter = mock_filters["streamdeck_ui.api.ImageFilter"]
    drawn = image_filter.call_args[0][0]
    assert open(drawn, "rb").read() == b"\x89PNG the plugin's art"
    assert_display_handler_used(api_server, STREAMDECK_SERIAL)


def test_a_plugin_title_is_drawn_instead_of_the_button_text(api_server, runtime, mock_filters):
    api_server.set_button_text(STREAMDECK_SERIAL, 0, 4, "my own text")
    bind(api_server, 4)
    api_server.elgato_plugins.sink.set_key_title(STREAMDECK_SERIAL, 4, "ON AIR")

    text_filter = mock_filters["streamdeck_ui.api.TextFilter"]
    assert text_filter.call_args[0][0] == "ON AIR"


def test_a_key_without_a_plugin_keeps_its_own_look(api_server, runtime, mock_filters):
    api_server.set_button_text(STREAMDECK_SERIAL, 0, 6, "my own text")

    api_server._update_button_filters(STREAMDECK_SERIAL, 0, 6)

    text_filter = mock_filters["streamdeck_ui.api.TextFilter"]
    assert text_filter.call_args[0][0] == "my own text"


def test_changing_a_button_settings_restarts_the_instance(api_server, runtime):
    bind(api_server, 1)
    context = runtime.context_at(1)

    api_server.set_button_official_settings(STREAMDECK_SERIAL, 0, 1, {"volume": 9})

    assert api_server.get_button_official_settings(STREAMDECK_SERIAL, 0, 1) == {"volume": 9}
    assert [event[1] for event in runtime.of("unplace")] == [context]
    assert runtime.context_at(1) != context


def test_a_plugin_saving_its_own_settings_does_not_restart_itself(api_server, runtime):
    bind(api_server, 1)
    context = runtime.context_at(1)

    api_server.elgato_plugins.plugin_settings_changed(context, {"volume": 9})

    assert api_server.get_button_official_settings(STREAMDECK_SERIAL, 0, 1) == {"volume": 9}
    assert not runtime.of("unplace")
    assert [event[1] for event in runtime.of("place")] == [context]
    api_server.expose_save_state().assert_called()


def test_rebinding_a_button_forgets_the_settings_of_the_instance_that_ran(api_server, runtime):
    bind(api_server, 1)
    api_server.elgato_plugins.plugin_settings_changed(runtime.context_at(1), {"volume": 9})
    assert api_server.get_button_official_settings(STREAMDECK_SERIAL, 0, 1) == {"volume": 9}

    bind(api_server, 1, action="com.test.plugin.other")

    assert api_server.get_button_official_settings(STREAMDECK_SERIAL, 0, 1) == {}


def test_the_binding_is_written_to_the_saved_state(api_server, runtime):
    bind(api_server, 1)

    state = api_server.get_button_state_object(
        STREAMDECK_SERIAL, 0, 1, api_server.get_button_state(STREAMDECK_SERIAL, 0, 1)
    )
    assert state.elgato_plugin_id == "com.test.plugin"
    assert state.elgato_action_id == "com.test.plugin.action"
    api_server.expose_save_state().assert_called()


def test_an_official_binding_belongs_to_one_state_of_the_button(api_server, runtime):
    """The button form is one tab per state: reading the binding without the state would show
    the active state's plugin in every tab and write over it from any of them."""
    api_server.set_button_official_binding(STREAMDECK_SERIAL, 0, 4, "com.test.plugin", "com.test.plugin.action", 1)

    assert api_server.get_button_official_binding(STREAMDECK_SERIAL, 0, 4, 1) == (
        "com.test.plugin",
        "com.test.plugin.action",
    )
    assert api_server.get_button_official_binding(STREAMDECK_SERIAL, 0, 4, 0) == ("", "")
