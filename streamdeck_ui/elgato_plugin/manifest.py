"""Reading of ``.sdPlugin`` manifests in the official format.

The field names are the ones the bundles out there use. Nothing here runs a plugin or unpacks
anything: a caller hands over an already unpacked plugin directory.

Plenty of bundles declare ``OS`` entries for mac and windows only. On Linux that must not be a
reason to refuse a plugin, so platform filtering is opt in.
"""

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Optional

MANIFEST_NAME = "manifest.json"
PLUGIN_SUFFIX = ".sdPlugin"
ICON_PACK_SUFFIX = ".sdIconPack"

WEB_CODE_SUFFIXES = {".html", ".htm", ".js"}

PLATFORM_MAC = "mac"
PLATFORM_WINDOWS = "windows"
PLATFORM_LINUX = "linux"

# A bundle can name its code three ways: one CodePath for everybody, a CodePathWin,
# CodePathMac or CodePathLin per platform, or an override inside its OS entries.
PLATFORM_CODE_KEYS = {
    PLATFORM_WINDOWS: "CodePathWin",
    PLATFORM_MAC: "CodePathMac",
    PLATFORM_LINUX: "CodePathLin",
}


class ManifestError(Exception):
    """Raised when a manifest cannot be used at all."""


@dataclass
class ActionManifest:
    """One action (key, dial or touch control) offered by a plugin."""

    uuid: str
    name: str = ""
    tooltip: str = ""
    icon: str = ""
    ui_category: str = ""
    visible_in_actions_list: bool = True
    no_multi_action: bool = False
    states: list[dict[str, Any]] = field(default_factory=list)
    controllers: dict[str, dict[str, Any]] = field(default_factory=dict)
    supported_keys: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def controllers_offered(self) -> list[str]:
        return sorted(self.controllers)

    def property_inspector_path(self, controller: str = "Keypad") -> Optional[str]:
        """The PI path for a controller, falling back to the action then plugin default."""
        for source in (self.controllers.get(controller), self.raw):
            if isinstance(source, dict):
                path = source.get("PropertyInspectorPath")
                if isinstance(path, str) and path:
                    return path
        return None

    def state_images(self, controller: str = "Keypad") -> list[str]:
        states = self.controllers.get(controller, {}).get("States") if controller in self.controllers else None
        entries = states if isinstance(states, list) else self.states
        return [entry["Image"] for entry in entries if isinstance(entry, dict) and isinstance(entry.get("Image"), str)]


@dataclass
class PluginManifest:
    """A plugin bundle as described by its manifest."""

    root: Path
    uuid: str
    name: str
    version: str
    actions: list[ActionManifest] = field(default_factory=list)
    plugin_version: Optional[int] = None
    sdk_version: str = ""
    software_version: str = ""
    category: str = ""
    category_icon: str = ""
    description: str = ""
    icon: str = ""
    url: str = ""
    author: str = ""
    code_path: Optional[str] = None
    property_inspector_path: Optional[str] = None
    controllers: dict[str, dict[str, Any]] = field(default_factory=dict)
    platforms: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def action_uuids(self) -> list[str]:
        return [action.uuid for action in self.actions]

    @property
    def code_kind(self) -> str:
        """``web`` when the host must load the code in a view, ``executable`` to spawn it."""
        if not self.code_path:
            return "none"
        return "web" if Path(self.code_path).suffix.lower() in WEB_CODE_SUFFIXES else "executable"

    @property
    def needs_web_view(self) -> bool:
        return self.code_kind == "web"

    def action(self, action_uuid: str) -> Optional[ActionManifest]:
        for action in self.actions:
            if action.uuid == action_uuid:
                return action
        return None

    def code_path_for_platform(self, platform: str = PLATFORM_LINUX) -> Optional[str]:
        """The code a machine of this platform would run, which is not always the same file."""
        return _platform_code_path(self.raw, platform)

    def resolves_on(self, platform: str = PLATFORM_LINUX) -> bool:
        return self.code_path_for_platform(platform) is not None

    def executable(self, platform: str = PLATFORM_LINUX) -> Optional[Path]:
        """The path of the binary to launch, without touching it."""
        code_path = self.code_path_for_platform(platform)
        if self.code_kind != "executable" or not code_path:
            return None
        return (self.root / code_path).resolve()

    def property_inspector_for(self, action_uuid: str, controller: str = "Keypad") -> Optional[str]:
        action = self.action(action_uuid)
        if action is not None:
            path = action.property_inspector_path(controller)
            if path:
                return path
        return self.property_inspector_path

    def declares_platform(self, platform: str) -> bool:
        """Whether the manifest claims this platform.

        Plenty of bundles only ever claim mac and windows, so a False here is a
        reason to warn, never a reason to refuse the plugin on Linux.
        """
        if not self.platforms:
            return True
        return platform in self.platforms  # nothing claimed means nothing excluded

    def runs_on(self, platform: str = PLATFORM_LINUX) -> bool:
        """Whether this host could run the bundle at all.

        A web code path needs a view we do not have, and a manifest whose OS entries only name
        other platforms ships no code for ours. This is stricter than declares_platform because
        it describes what the launcher can actually spawn, not what the author advertised.
        """
        paths = PLATFORM_CODE_KEYS.get(platform)
        specific = _text(self.raw, paths) if isinstance(paths, str) else ""
        override = next(
            (
                _text(entry, "CodePath")
                for entry in _dict_list(self.raw.get("OS"))
                if entry.get("Platform") == platform and _text(entry, "CodePath")
            ),
            "",
        )
        path = specific or override or self.code_path_for_platform(platform) or ""
        if not path:
            return False  # the bundle ships no code for this platform
        if Path(path).suffix.lower() in WEB_CODE_SUFFIXES:
            return False  # the host would have to show a web view we do not have
        return self.declares_platform(platform)


def load_plugin_manifest(root: Path) -> PluginManifest:
    """Parses ``manifest.json`` inside an unpacked plugin directory."""
    manifest_path = Path(root) / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ManifestError(f"{root} has no {MANIFEST_NAME}")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ManifestError(f"{manifest_path} is not valid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise ManifestError(f"{manifest_path} does not hold a JSON object")

    uuid = _text(raw, "UUID") or bundle_uuid(Path(root))
    if not uuid:
        raise ManifestError(f"{manifest_path} has no UUID and {root} is not named like a bundle")

    actions = [_action(entry, manifest_path) for entry in _dict_list(raw.get("Actions"))]
    controllers = _controllers(raw.get("Controllers"))
    platforms = [entry["Platform"] for entry in _dict_list(raw.get("OS")) if isinstance(entry.get("Platform"), str)]

    return PluginManifest(
        root=Path(root),
        uuid=uuid,
        name=_text(raw, "Name"),
        version=_text(raw, "Version"),
        actions=actions,
        plugin_version=_integer(raw.get("PluginVersion")),
        sdk_version=_text(raw, "SDKVersion"),
        software_version=_text(raw, "SoftwareVersion"),
        category=_text(raw, "Category"),
        category_icon=_text(raw, "CategoryIcon"),
        description=_text(raw, "Description"),
        icon=_text(raw, "Icon"),
        url=_text(raw, "URL"),
        author=_text(raw, "Author"),
        code_path=_platform_code_path(raw, PLATFORM_LINUX),
        property_inspector_path=_text(raw, "PropertyInspectorPath") or None,
        controllers=controllers,
        platforms=platforms,
        raw=raw,
    )


def discover_plugin_directories(*roots: Path) -> list[Path]:
    """Finds unpacked plugin bundles, deepest first being irrelevant: order is stable."""
    found: list[Path] = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        candidates = [root] if root.suffix == PLUGIN_SUFFIX else []
        candidates += [child for child in sorted(root.iterdir()) if child.is_dir() and child.suffix == PLUGIN_SUFFIX]
        found.extend(candidate for candidate in candidates if (candidate / MANIFEST_NAME).is_file())
    return found


def bundle_uuid(root: Path) -> str:
    """The uuid of a bundle whose manifest does not bother to state one.

    Most plugins out there leave ``UUID`` out and let the folder carry the identity, which is
    the convention the format follows, so ``com.example.cpu.sdPlugin`` is ``com.example.cpu``.
    """
    name = Path(root).name
    return name[: -len(PLUGIN_SUFFIX)] if name.endswith(PLUGIN_SUFFIX) else ""


def _platform_code_path(raw: dict, platform: str) -> Optional[str]:
    specific = _text(raw, PLATFORM_CODE_KEYS.get(platform, ""))
    if specific:
        return specific
    for entry in _dict_list(raw.get("OS")):
        if entry.get("Platform") == platform:
            override = _text(entry, "CodePath")
            if override:
                return override
    return _text(raw, "CodePath") or None


def _controllers(value: Any) -> dict[str, dict[str, Any]]:
    """Manifests list controllers two ways: names, or a map holding per controller state."""
    if isinstance(value, dict):
        return {key: item for key, item in value.items() if isinstance(item, dict)}
    if isinstance(value, list):
        return {item: {} for item in value if isinstance(item, str)}
    return {}


def _action(entry: Any, manifest_path: Path) -> ActionManifest:
    if not isinstance(entry, dict):
        raise ManifestError(f"{manifest_path} has an action that is not an object")
    uuid = entry.get("UUID")
    if not isinstance(uuid, str) or not uuid:
        raise ManifestError(f"{manifest_path} has an action without a UUID")
    visible = entry.get("VisibleInActionsList", True)
    controllers = _controllers(entry.get("Controllers"))
    return ActionManifest(
        uuid=uuid,
        name=_text(entry, "Name"),
        tooltip=_text(entry, "Tooltip"),
        icon=_text(entry, "Icon"),
        ui_category=_text(entry, "UICategory"),
        visible_in_actions_list=bool(visible),
        no_multi_action=bool(entry.get("NoMultiAction", False)),
        states=[state for state in _dict_list(entry.get("States"))],
        controllers=controllers,
        supported_keys=[key for key in entry.get("SupportedKeys", []) if isinstance(key, str)],
        raw=entry,
    )


def _text(source: dict, key: str) -> str:
    value = source.get(key)
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # manifests ship SDKVersion as a bare number
        return str(value)
    return ""


def _integer(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _dict_list(value: Any) -> list[dict]:
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []
