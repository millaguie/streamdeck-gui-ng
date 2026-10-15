"""Starting and stopping official format plugin processes.

The handshake is positional: the plugin is started with the port of the websocket host, a
connection uuid, the registration event name, the application info and the action info, the
last two as JSON strings. That is the order ``connectElgatoStreamDeckSocket`` hands over.

The keys inside ``info`` are not all pinned down: ``application.language`` is the one a plugin
is known to read, the rest is what the bundles out there ask for and stays unverified until one
complains.
"""

import json
import logging
import os
import subprocess
import threading
import time
import uuid as uuid_module
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from streamdeck_ui.elgato_plugin.manifest import PLATFORM_LINUX, PluginManifest
from streamdeck_ui.elgato_plugin.protocol import REGISTER_PLUGIN

logger = logging.getLogger(__name__)

REGISTER_EVENT = REGISTER_PLUGIN


def new_connection_uuid() -> str:
    return uuid_module.uuid4().hex


def app_info(
    manifest: PluginManifest,
    platform: str = PLATFORM_LINUX,
    language: str = "en",
    theme: str = "dark",
    application_version: str = "",
    plugin_version: str = "",
) -> dict[str, Any]:
    """The ``info`` argument a plugin receives on startup."""
    return {
        "application": {
            "platform": platform,
            "platformVersion": "",
            "pluginVersion": plugin_version,
            "version": application_version,
            "language": language,
            "theme": theme,
        },
        "plugin": {
            "uuid": manifest.uuid,
            "version": manifest.version,
            "name": manifest.name,
        },
    }


def action_info(manifest: PluginManifest, action_uuid: str, context: str = "", payload: Optional[dict] = None) -> dict:
    """The ``actionInfo`` argument; a plugin's own client reads ``action`` from it."""
    return {"action": action_uuid, "context": context, "payload": payload or {}}


@dataclass
class PluginProcess:
    """One running plugin process, with its output pumped into a logger."""

    manifest: PluginManifest
    connection_uuid: str
    process: subprocess.Popen
    arguments: list[str]
    on_exit: Optional[Callable[[int], None]] = None
    _watcher: Optional[threading.Thread] = field(default=None, repr=False)

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    @property
    def exit_code(self) -> Optional[int]:
        return self.process.returncode

    def stop(self, timeout: float = 3.0) -> Optional[int]:
        if not self.running:
            return self.process.returncode
        self.process.terminate()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("Plugin %s ignored SIGTERM, killing it", self.manifest.uuid)
            self.process.kill()
            self.process.wait(timeout=timeout)
        return self.process.returncode


def launch_plugin(
    manifest: PluginManifest,
    port: int,
    connection_uuid: str,
    info: dict,
    action_info_value: dict,
    platform: str = PLATFORM_LINUX,
    log: Optional[Callable[[str, str], None]] = None,
    on_exit: Optional[Callable[[int], None]] = None,
    extra_environment: Optional[dict[str, str]] = None,
) -> PluginProcess:
    """Spawns an executable plugin the way the protocol expects it started."""
    executable = manifest.executable(platform)
    if executable is None:
        raise ValueError(f"{manifest.uuid} has no executable code path for {platform}")
    if not executable.is_file():
        raise FileNotFoundError(f"{manifest.uuid} declares {executable} which is not a file")

    positional_arguments = [
        str(executable),
        str(port),
        connection_uuid,
        REGISTER_EVENT,
        json.dumps(info),
        json.dumps(action_info_value),
    ]
    # The handshake travels as positional arguments. Some plugins are built on the OpenAction
    # library, which looks for the same values as -port/-pluginUUID/-registerEvent flags and
    # panics without them, and Go's flag package stops reading at the first bare argument, so a
    # shared command line cannot carry both readers: only one form can be seen per spawn. Both
    # styles ignore what they do not expect, so a plugin that dies before its handshake gets one
    # more chance in the other form.
    flag_arguments = [
        str(executable),
        "-port",
        str(port),
        "-pluginUUID",
        connection_uuid,
        "-registerEvent",
        REGISTER_EVENT,
    ]

    # Which handshake a plugin understands is only visible in the way it dies, and a Go binary can
    # print nothing at all while doing it. Positional goes first because it is what the protocol
    # itself defines: OpenAction C++ ignores unknown extra arguments after its own, so it survives
    # both forms, while Go's flag package chokes on the bare port number that opens the positional
    # line. A plugin that dies before its handshake gets the flag form; the one that outlived the
    # patience window is remembered per uuid and reused after restarts.
    remembered = KNOWN_HANDSHAKES.get(manifest.uuid)

    environment = dict(os.environ)
    environment.update(extra_environment or {})

    def spawn(command: list[str]) -> subprocess.Popen:
        return subprocess.Popen(
            command,
            cwd=str(manifest.root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )

    handshake_orders = [
        ("positional", positional_arguments),
        ("flags", flag_arguments),
    ]
    if remembered == "flags":
        # The form this plugin proved happy goes first, so no doomed attempt is repeated. A crash
        # with memory still gets its other chance below.
        handshake_orders.reverse()

    process = None
    arguments = handshake_orders[0][1]
    early_output: list[tuple[str, str]] = []

    def record(level: str, message: str) -> None:
        # Plugin output keeps its own identity; only our notes need naming the bundle, and they
        # go through here too because the watcher that usually logs is not wired up yet.
        early_output.append((level, message))

    def note(level: str, message: str) -> None:
        record(level, f"{manifest.uuid} {message}")

    for index, (style, command) in enumerate(handshake_orders):
        process = spawn(command)
        arguments = command
        # Everything the decision needs happens inside this window; past it the plugin is simply
        # running and must never be touched again. Output read during the window belongs to a run
        # that may still be thrown away, so readers hold their lines until we know the verdict.
        started = time.monotonic()
        deadline = started + EARLY_EXIT_SECONDS

        def read_ahead(stream, level: str, pending: list[tuple[str, str]], cutoff: float) -> None:
            if stream is None:
                return

            def emit(text: str, _level: str = level, _pending: list = pending) -> None:
                _pending.append((_level, text))

            # readline() parks on the stream until it gets a line or EOF. A live plugin would hold
            # it forever, so drop these readers once the window closes; whatever the plugin wrote
            # is already in the OS pipe buffer and the watcher picks it up as soon as we return.
            while time.monotonic() < cutoff:
                line = stream.readline()
                if not line:
                    break
                line = line.rstrip()
                if line:
                    emit(line)

        pending: list[tuple[str, str]] = []
        readers = [
            threading.Thread(target=read_ahead, args=(process.stdout, "info", pending, deadline), daemon=True),
            threading.Thread(target=read_ahead, args=(process.stderr, "warning", pending, deadline), daemon=True),
        ]
        for reader in readers:
            reader.start()
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        survived = process.poll() is None
        if not survived:
            for reader in readers:
                reader.join(timeout=EARLY_EXIT_SECONDS)
        died_rejected = not survived and process.returncode != 0
        if died_rejected:
            # This run is about to be replaced. Its lines were read from the pipe and are gone
            # from it, so say where they came from; a Go binary rejected by its parser usually
            # has nothing at all to show for itself anyway.
            for level, line in pending:
                note(level, f"under {style}: {line}")
        if not died_rejected:
            # Outliving the window proves the parser accepted this handshake. An instant exit
            # with nothing to report (rc 0) proves nothing, so it never rewrites the memory.
            if survived:
                KNOWN_HANDSHAKES[manifest.uuid] = style
            for level, line in pending:
                record(level, line)
            break

        note("warning", f"quit right away under the {style} handshake")
        if index + 1 < len(handshake_orders):
            note("info", f"retrying with the {handshake_orders[index + 1][0]} handshake")
    else:
        if process is not None:
            note("warning", f"never stayed up ({exit_description(process)})")

    log_line = log or _default_log
    for level, message in early_output:
        log_line(level, message)

    if process is None:
        raise RuntimeError(f"no handshake spawned a process for {manifest.uuid}")

    plugin = PluginProcess(
        manifest=manifest,
        connection_uuid=connection_uuid,
        process=process,
        arguments=arguments,
        on_exit=on_exit,
    )
    if process.poll() is None:
        # Only a live process keeps streams worth pumping. A run that ended had them drained to
        # EOF during the window; pumping it again would block forever on a closed but empty pipe,
        # and its logs (or lack of them) were already told from inside the window.
        plugin._watcher = _pump_output(plugin, log_line, platform)
    return plugin


def _default_log(level: str, message: str) -> None:
    getattr(logger, level if level in ("debug", "info", "warning", "error") else "info")(
        "[plugin %s] %s", level, message
    )


# How long a launch gets to prove it is alive before its handshake is questioned. A plugin that
# dies here with something to report (or on a signal) was rejected by its own argument parser and
# gets the other form; an exit code of zero inside the window belongs to scripts that simply are
# short, so it is never retried. Past this moment the plugin runs untouched: it may have opened
# files, ports or windows already. Interpreters boot fast, so the decision must be made early.
EARLY_EXIT_SECONDS = 0.1

# Plugin uuid -> handshake style that kept its process alive, learned at launch and reused on
# restart so a plugin known to want flags is not run through a doomed spawn every time.
KNOWN_HANDSHAKES: dict[str, str] = {}


def exit_description(process: subprocess.Popen) -> str:
    """The way a dead child left this world: its code, or the signal that got it."""
    if process.returncode is None:
        return "still running"
    if process.returncode < 0:
        return f"killed by signal {-process.returncode}"
    return f"exit code {process.returncode}"


def _pump_output(plugin: PluginProcess, log: Callable[[str, str], None], platform: str) -> threading.Thread:
    def pump(stream, level: str) -> None:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            line = line.rstrip()
            if line:
                log(level, line)
        stream.close()

    def watch() -> None:
        readers = [
            threading.Thread(target=pump, args=(plugin.process.stdout, "info"), daemon=True),
            threading.Thread(target=pump, args=(plugin.process.stderr, "warning"), daemon=True),
        ]
        for reader in readers:
            reader.start()
        code = plugin.process.wait()
        for reader in readers:
            reader.join(timeout=1.0)
        log("info", f"exited with code {code}")
        if plugin.on_exit is not None:
            plugin.on_exit(code)

    watcher = threading.Thread(target=watch, name=f"plugin-{plugin.manifest.uuid}", daemon=True)
    watcher.start()
    return watcher
