#!/usr/bin/env python3
"""Construye los bundles net.millaguie.minino.* a partir de plugins/src.

Cada bundle se autocontiene: lleva su copia de minino_runtime.py (y de artwork.py si lo usa), su
programa con shebang y sus iconos, generados aquí mismo con Pillow. Nada de lo que produce este
script viene de un bundle oficial: la esfera, los iconos y el código son de aquí.

    python3 plugins/build_minino.py                 # instala en ~/.streamdeck_ui/elgato_plugins
    python3 plugins/build_minino.py --dest plugins/dist
"""

import argparse
import json
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
AUTHOR = "millaguie"
URL = "https://github.com/millaguie/streamdeck-linux-gui"

sys.path.insert(0, str(SRC))

from artwork import icon_chip, icon_mic, icon_speaker  # noqa: E402
from clock_main import CLOCK_SIZE, draw_clock  # noqa: E402

SHEBANG = "#!/usr/bin/env python3\n"
CATEGORY = "minino"

PLUGINS = [
    {
        "uuid": "net.millaguie.minino.clock",
        "name": "Clock",
        "program": "clock_main.py",
        "modules": [],
        "category_icon": lambda: draw_clock(datetime.now(), size=96, show_seconds=False),
        "icons": {"clock": lambda: draw_clock(datetime.now(), size=CLOCK_SIZE)},
        "actions": [
            {
                "action": "action",
                "name": "Analog clock",
                "tooltip": "A clock that tells the time, drawn here with Pillow.",
                "states": ["clock"],
            }
        ],
    },
    {
        "uuid": "net.millaguie.minino.monitor",
        "name": "Monitor",
        "program": "monitor_main.py",
        "modules": ["artwork.py"],
        "category_icon": lambda: icon_chip(size=96),
        "icons": {"chip": lambda: icon_chip(size=96)},
        "actions": [
            {
                "action": "monitor",
                "name": "CPU and memory",
                "tooltip": "CPU, memory and load average, read from /proc.",
                "states": ["chip"],
            }
        ],
    },
    {
        "uuid": "net.millaguie.minino.mute",
        "name": "Microphone mute",
        "program": "mute_main.py",
        "modules": ["artwork.py"],
        "category_icon": lambda: icon_mic(False, size=96),
        "icons": {
            "mic_on": lambda: icon_mic(False, size=96),
            "mic_off": lambda: icon_mic(True, size=96),
        },
        "actions": [
            {
                "action": "toggle",
                "name": "Mute the microphone",
                "tooltip": "Toggles the default input with pactl. The key shows whether you are muted.",
                "states": ["mic_on", "mic_off"],
            }
        ],
    },
    {
        "uuid": "net.millaguie.minino.volume",
        "name": "Volume",
        "program": "volume_main.py",
        "modules": ["artwork.py"],
        "category_icon": lambda: icon_speaker(0, size=96),
        "icons": {
            "up": lambda: icon_speaker(1, size=96),
            "down": lambda: icon_speaker(-1, size=96),
        },
        "actions": [
            {
                "action": "up",
                "name": "Volume up",
                "tooltip": "Turns the default output up with wpctl and shows the level.",
                "states": ["up"],
            },
            {
                "action": "down",
                "name": "Volume down",
                "tooltip": "Turns the default output down with wpctl and shows the level.",
                "states": ["down"],
            },
        ],
    },
]


def write_bundle(definition: dict, destination: Path) -> Path:
    root = destination / f"{definition['uuid']}.sdPlugin"
    if root.exists():
        shutil.rmtree(root)
    (root / "icons").mkdir(parents=True)

    shutil.copy(SRC / "minino_runtime.py", root / "minino_runtime.py")
    for module in definition["modules"]:
        shutil.copy(SRC / module, root / module)

    program = root / definition["program"]
    program.write_text(SHEBANG + (SRC / definition["program"]).read_text(encoding="utf-8"), encoding="utf-8")
    program.chmod(program.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    for name, draw in definition["icons"].items():
        (root / "icons" / f"{name}.png").write_bytes(draw())
    (root / "icons" / "category.png").write_bytes(definition["category_icon"]())

    actions = []
    for action in definition["actions"]:
        states = action["states"]
        actions.append(
            {
                "UUID": f"{definition['uuid']}.{action['action']}",
                "Name": action["name"],
                "Tooltip": action["tooltip"],
                "Icon": f"icons/{states[0]}",
                "UICategory": CATEGORY,
                "States": [{"Image": f"icons/{state}"} for state in states],
                "SupportedKeys": ["Key"],
            }
        )

    manifest = {
        "UUID": definition["uuid"],
        "Name": definition["name"],
        "Author": AUTHOR,
        "URL": URL,
        "Version": "1.0.0",
        "Icon": f"icons/{definition['actions'][0]['states'][0]}",
        "Category": CATEGORY,
        "CategoryIcon": "icons/category",
        "SoftwareVersion": "1.0",
        "SDKVersion": "2",
        "PluginVersion": 1,
        "CodePath": definition["program"],
        "OS": [{"Platform": "linux"}],
        "Actions": actions,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return root


def main() -> int:
    default_dest = Path.home() / ".streamdeck_ui" / "elgato_plugins"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=default_dest, help="where to write the bundles")
    parser.add_argument("only", nargs="*", help="build just these plugins (given by name or UUID)")
    arguments = parser.parse_args()
    arguments.dest.mkdir(parents=True, exist_ok=True)

    for definition in PLUGINS:
        short = definition["uuid"].rsplit(".", 1)[-1]
        if arguments.only and short not in arguments.only and definition["uuid"] not in arguments.only:
            continue
        print(f"built {write_bundle(definition, arguments.dest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
