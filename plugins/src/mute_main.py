"""Silenciar micrófono en Linux: net.millaguie.minino.mute, con pactl."""

import subprocess

from artwork import icon_mic
from minino_runtime import Plugin, run

SINK = "@DEFAULT_SOURCE@"
KEY_SIZE = 144  # la tecla de un Original/MK2 es cuadrada de 144
MUTED = 1
UNMUTED = 0


def _run(*arguments):
    try:
        completed = subprocess.run(arguments, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (completed.stdout or "") + (completed.stderr or "")


def is_muted(source: str = SINK) -> bool:
    return "yes" in _run("pactl", "get-source-mute", source).lower()


def toggle(source: str = SINK) -> bool:
    _run("pactl", "set-source-mute", source, "toggle")
    return is_muted(source)


class MicrophoneMutePlugin(Plugin):
    def on_started(self, handshake):
        self.art = {UNMUTED: icon_mic(False, KEY_SIZE), MUTED: icon_mic(True, KEY_SIZE)}

    def on_appear(self, message):
        self._paint(message["context"], is_muted(self._source(message)))

    def on_press(self, message):
        if message.get("event") != "keyUp":
            return
        context = message.get("context", "")
        source = self._source(message)
        try:
            self._paint(context, toggle(source))
        except Exception as error:  # pactl puede no estar en la máquina
            self.log(f"pactl falló: {error}", "warning")
            self.show_alert(context)

    def _source(self, message) -> str:
        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
        settings = payload.get("settings") or self.contexts.get(message.get("context", ""), {}).get("settings", {})
        return str(settings.get("source") or SINK)

    def _paint(self, context, muted):
        state = MUTED if muted else UNMUTED
        self.set_image(context, self.art[state])
        self.set_title(context, "")
        if context in self.contexts:
            self.contexts[context]["state"] = state


if __name__ == "__main__":
    run(MicrophoneMutePlugin())
