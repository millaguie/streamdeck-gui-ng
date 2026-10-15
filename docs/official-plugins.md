# Official plugin support and the plugin API

This document explains how the fork runs plugins in the official `.sdPlugin` format on Linux, what
the plugin API looks like from a plugin's point of view, how the whole thing was built
(clean-room discipline), and how our own `net.millaguie.minino.*` bundles reimplement a set of
everyday plugins natively. For the statement about what is and is not vendored in this repository,
see [PROVENANCE.md](../PROVENANCE.md).

## Why this exists: interoperability

The `.sdPlugin` format is what the plugin ecosystem for these devices actually speaks: bundles are
published in this format, users already have them installed, and every property inspector, manifest
and handshake in the wild is written against it. A Linux UI that cannot load those bundles leaves
its users with a closed garden.

So the purpose of everything described here is **interoperability**: to speak the same public
plugin protocol the ecosystem speaks, so the bundles a user already has keep working under this
fork. Interoperability sets the boundaries of the work too — the goal is compatibility with the
format and its protocol, not the product that defined them; everything the fork needed to know is
in the interfaces a bundle publishes, and everything the fork ships was written from scratch for
that purpose. Our own `net.millaguie.minino.*` bundles exist to prove the implementation from both
sides of the socket.

The protocol is not a private interface, either: it is multiply implemented in public. On the host
side, [OpenDeck](https://github.com/nekename/OpenDeck) runs the same bundles on Linux, and
[stream-deck-to-dock](https://github.com/vnoiram/stream-deck-to-dock) adapts them to other
hardware. On the plugin side, the [OpenAction](https://github.com/OpenActionAPI/docs) SDK
(backwards-compatible with this format), the official JavaScript SDK and community ones such as
`@rweich/streamdeck-ts` all speak the same handshake and events this host implements.

## What it does

- Any installed executable bundle (a directory with a `manifest.json`, code and images) shows up in
  the button form under *Official Plugin*. A compatible one can be bound to any key, like the
  built-in commands.
- The bundle runs as its own process and talks to the application over a loopback WebSocket, the
  way the plugin format intends. A broken bundle costs one key, never the page and never the app.
- Images a plugin sends become overlays in the display pipeline, so dimming, rotation and the
  display lock keep working on top of them.

## The pieces

| Module | Responsibility |
| --- | --- |
| `streamdeck_ui/elgato_plugin/manifest.py` | Reads and validates `manifest.json`: uuid, code paths per platform, actions and their states. Never executes anything. |
| `streamdeck_ui/elgato_plugin/launcher.py` | Spawns the plugin process and builds the handshake command line in both dialects. |
| `streamdeck_ui/elgato_plugin/protocol.py` | The message vocabulary and shapes, and the rules for what to do with unknown ones. |
| `streamdeck_ui/elgato_plugin/runtime.py` | Sessions: maps session uuids to plugins, keys to actions, applies what plugins ask for. |
| `streamdeck_ui/elgato_plugin/deck_sink.py` | Turns plugin images into what a deck accepts: alpha flattened onto black, converted and scaled. |
| `streamdeck_ui/elgato_plugin/server.py`, `service.py` | The loopback WebSocket server (a Qt object) and the application-facing half: page sync, settings, repaints. |
| `streamdeck_ui/gui.py`, `api.py`, `model.py`, `config.py` | The button mode: dropdown of compatible actions, binding and settings persisted in the usual config file. |

## The plugin API, end to end

1. **Start and handshake.** The host starts the bundle's program with the port of the WebSocket
   host, a fresh session uuid and the registration event name. The format hands these over
   positionally; the OpenAction library some bundles are built on looks for
   `-port`, `-pluginUuid` and `-registerEvent` instead, so the launcher satisfies both readers with
   one command line.
2. **Registration.** The plugin connects back and sends the registration event with its uuid. The
   session uuid from the command line is what replies are routed to; the plugin's own uuid is the
   identity used for settings.
3. **Placement.** Only keys on the currently visible page are told about themselves
   (`willAppear`/`willDisappear`), which is why a bundle does not need to track key indices.
4. **Events the host sends** include `keyDown`, `keyUp`, `willAppear`, `willDisappear`,
   `dialDown`, `dialUp`, `dialRotate`, `touchTap`, `deviceDidConnect`/`deviceDidDisconnect`,
   `didReceiveSettings`, `titleParametersDidChange` and the property inspector relay events.
5. **Events a plugin sends** include `setImage`, `setXYWHImage`, `setTitle`, `setState`, `showOk`,
   `showAlert`, `getSettings`/`setSettings`, `getGlobalSettings`/`setGlobalSettings`, `openUrl`,
   `logMessage` and `switchToProfile`.
6. **Images** travel as base64 PNG. The deck sink flattens the alpha onto black (a black key is a
   key that is not lit), converts to the format the key wants and scales when the sizes differ.
7. **Tolerance rules.** An event the host does not know is logged and dropped; a program a bundle
   names but does not ship is one dead key, not a failed page sync; a page sync that cannot find a
   placement tells the plugin it went away instead of throwing across the pipeline.

`deviceDidConnect` deserves a note: the two dialects in the wild describe a deck differently (one
puts rows and columns straight in `deviceInfo` under a `deviceIdentifier`, the other wants a nested
`deviceInfo.size`). Both ignore fields they do not know, so the host sends one message that
satisfies both.

## How it was built: clean-room discipline

The support was written like a clean-room exercise, with the specification kept apart from the
implementation:

1. **The specification is the observable interface.** The handshake entry point
   (`connectElgatoStreamDeckSocket`), the event names and the message shapes were written down from
   three inputs, all of them observable without anyone else's source code: the files a bundle
   itself ships (its manifest and the JavaScript its property inspector uses), the traffic bundles
   produce while talking to our own host on `127.0.0.1`, and the OpenAction reference
   implementation for the second dialect. OpenDeck — the other libre host for these bundles — was
   written independently and was not consulted; its public source, compared against after the
   fact, corroborates the same contract.
2. **The implementation only ever saw the specification.** The host code in
   `streamdeck_ui/elgato_plugin/` was written against that written-down contract, and the tests are
   the contract in executable form: `tests/test_elgato_protocol.py` pins every message shape,
   `tests/test_elgato_service.py` pins the page-sync rules, and `tests/test_elgato_runtime.py`
   runs a real plugin process over a real WebSocket against a recorded deck.
3. **Nothing from anyone else's tree enters this one.** No code, assets, profiles or binaries from
   any other product are inputs to the build. That claim is checkable, and the check script lives
   in [PROVENANCE.md](../PROVENANCE.md).

This is a working discipline, not a certified legal process. If a formally certified clean room is
ever needed (separate people writing specification and implementation), the codebase is already
shaped for it: the protocol tests are the specification, and a fresh implementation can be written
from them alone.

## Our own bundles: the reimplementations

The `net.millaguie.minino.*` bundles are this project's own, from-scratch implementations of a set
of everyday plugins, Linux-native and web-view free:

| Bundle | What it does |
| --- | --- |
| `net.millaguie.minino.clock` | Analog clock, redrawn every minute. |
| `net.millaguie.minino.monitor` | Key-side system monitor. |
| `net.millaguie.minino.mute` | Microphone mute toggle with per-state artwork. |
| `net.millaguie.minino.volume` | Volume up and volume down as two actions of one bundle. |

They matter beyond their function: they are the executable documentation of the API. A new plugin
starts from the same two files these use:

- `plugins/src/minino_runtime.py` — everything a bundle needs to talk to the host: it reads both
  handshake dialects and implements the WebSocket client with the standard library alone, so a
  bundle never depends on which Python packages the user happens to have installed.
- `plugins/build_minino.py` — assembles a bundle directory (`manifest.json`, code, icons), draws
  the artwork with Pillow at the 144 px keys expect, and installs it into the plugins root under
  the state directory (`~/.streamdeck_ui/elgato_plugins` by default).

Every bundle is verified the same way the host is tested: started as a real process, connected over
a real WebSocket, and checked against what it puts on the keys. `tests/test_minino_build.py` keeps
the bundles loadable, self-contained and honest about their images.

## Getting plugins: installing, browsing, and where each one came from

There are two ways a bundle ends up in `~/.streamdeck_ui/elgato_plugins`: you copy an
already-unpacked `.sdPlugin` folder there yourself, or you use the **Plugins** window (the
`P` button next to the gear on the main window). The window has two tabs:

- *Installed* lists every bundle the app can see — ours and third party ones alike — with a
  provenance note saying where each came from: `installed here` for bundles copied into our
  directory, `OpenAction catalogue vN (<url>)` for ones downloaded through the window,
  `shared directory (~/.config/opendeck/plugins)` for bundles another launcher on this machine
  put there, or `external location`. Bundles are read-only listings: nothing in that tab runs.
- *Browse the catalogue* reads the open catalogue JSON that the community publishes
  (`openactionapi.github.io/plugins/catalogue.json`, the same one the marketplace website renders)
  and offers to download bundles published as release files of each plugin's repository. Bundles
  arrive either as `<uuid>.zip` or in the `.streamDeckPlugin` package format, which is a zip with
  the same folder inside.

### What installing does, and what it cannot promise

A third-party bundle runs with your user's rights. The install path is deliberately boring:

1. The download goes to a temporary folder over HTTPS. When the publisher states a SHA-256 digest
   on the release file, the downloaded bytes must match it before anything else happens.
2. The archive is unpacked as data only: entries that would write outside the bundle folder, and
   symbolic links, are refused; the size and file-count budgets abort anything outlandish.
3. `manifest.json` inside the bundle must parse and its UUID must match what is being installed —
   checked once in a scratch folder and once more while staging next to the target under a dot
   name that discovery ignores. Only then is the real `.sdPlugin` folder swapped into place, with
   the old one kept until the swap succeeded. A same-named old bundle is replaced only after an
   explicit confirmation.
4. Where it came from (url, tag, digest) is recorded next to the plugin in
   `~/.streamdeck_ui/elgato_plugins/.marketplace.json`, and Remove deletes only bundles that
   belong to our own directory and whose manifest parses.

The honest limits: no publisher signs these packages, so there is nothing to verify beyond the
optional digest — installing a catalogue entry means trusting its repository. A plugin whose
program is already running keeps running until the app restarts, even if you replaced or removed
the bundle underneath it. And plugins that need a web view still cannot run here: the list tells
you so rather than pretending otherwise.

## Trying it

1. Build and install the bundles: `python3 plugins/build_minino.py` (they land in
   `~/.streamdeck_ui/elgato_plugins`).
2. Restart the app; the actions appear under *Official Plugin* in the button form.
3. The unit tests do not need a deck attached:
   `QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_elgato_protocol.py tests/test_elgato_service.py tests/test_elgato_runtime.py -q`.
