"""Los bundles minino tienen que cargar por el parser de manifests y poder arrancar solos."""

import compileall
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins"))

import build_minino  # noqa: E402

from streamdeck_ui.elgato_plugin.manifest import load_plugin_manifest  # noqa: E402


@pytest.fixture(scope="module")
def bundles(tmp_path_factory):
    destination = tmp_path_factory.mktemp("minino")
    return {
        definition["uuid"]: build_minino.write_bundle(definition, destination) for definition in build_minino.PLUGINS
    }


def test_every_minino_plugin_loads_as_an_elgato_bundle(bundles):
    assert len(bundles) >= 4
    for uuid, root in bundles.items():
        manifest = load_plugin_manifest(root)
        assert manifest.uuid == uuid
        assert manifest.needs_web_view is False
        assert manifest.executable() is not None, f"{uuid} has no program to run"
        assert manifest.actions, f"{uuid} offers nothing to put on a key"


def test_the_images_a_manifest_points_at_are_really_in_the_bundle(bundles):
    for uuid, root in bundles.items():
        declared = json.loads((root / "manifest.json").read_text())
        images = {declared["Icon"], declared["CategoryIcon"]}
        for action in declared["Actions"]:
            images.add(action["Icon"])
            images.update(state["Image"] for state in action["States"])
        for image in images:
            assert (root / f"{image}.png").exists(), f"{uuid} points at a missing icons/{image}.png"


def test_the_bundled_programs_import_without_the_repository(bundles):
    """Each bundle has to be self-contained: no module of plugins/src left behind."""
    shared = {path.stem for path in (build_minino.SRC).glob("*.py")} - {"build_minino"}

    for uuid, root in bundles.items():
        assert compileall.compile_dir(root, quiet=2, force=True), f"{uuid} does not compile"
        declared = json.loads((root / "manifest.json").read_text())
        programs = [root / declared["CodePath"]] + [root / f"{module}.py" for module in declared.get("Modules", [])]
        for program in programs:
            wanted = {
                line.split()[1].split(".")[0]
                for line in program.read_text().splitlines()
                if line.startswith(("from ", "import ")) and len(line.split()) > 1
            } & shared
            missing = {name for name in wanted if not (root / f"{name}.py").exists()}
            assert not missing, f"{uuid} imports {sorted(missing)} which was not put in the bundle"
