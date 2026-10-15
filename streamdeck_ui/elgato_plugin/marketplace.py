"""Browsing and installing plugin bundles from the open catalogue.

The OpenAction ecosystem publishes a catalogue that lists community plugins by uuid, with a
repository link for each one. The catalogue itself is plain JSON; the bundles live as release
files in the repositories it points at. This module only fetches, checks and unpacks archives:
nothing downloaded here is ever run.

Downloads are third party code without any signature we could check, so installing stays an
explicit choice the user makes per plugin, never automatic.
"""

import json
import logging
import os
import re
import shutil
import stat
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from streamdeck_ui.elgato_plugin.manifest import PLUGIN_SUFFIX, ManifestError, load_plugin_manifest

logger = logging.getLogger(__name__)

CATALOGUE_URL = "https://openactionapi.github.io/plugins/catalogue.json"
ICON_URL_TEMPLATE = "https://openactionapi.github.io/plugins/icons/{uuid}.png"
INSTALL_DIRECTORY = Path.home() / ".streamdeck_ui" / "elgato_plugins"

CATALOGUE_CACHE_TTL_SECONDS = 3600
DOWNLOAD_TIMEOUT_SECONDS = 30
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024

# Release files can be named like the bundle they contain, which is how the ecosystem ships them.
_BUNDLE_SUFFIXES = (PLUGIN_SUFFIX.lower(), ".streamdeckplugin", ".zip")


class MarketplaceError(Exception):
    """The catalogue or a plugin package could not be used."""


@dataclass(frozen=True)
class CatalogueEntry:
    """One row of the online catalogue."""

    uuid: str
    name: str
    author: str
    repository: str
    description: str = ""
    download_url: str = ""

    @property
    def icon_url(self) -> str:
        return ICON_URL_TEMPLATE.format(uuid=self.uuid)


@dataclass(frozen=True)
class BundleAsset:
    """A downloadable release file that holds a bundle."""

    url: str
    tag: str
    filename: str
    size: int = 0
    sha256: str = ""


@dataclass
class InstallResult:
    """Where an installed bundle landed and what it turned out to be."""

    uuid: str
    path: Path
    name: str
    version: str
    source_url: str
    sha256: str = ""
    tag: str = ""
    repository: str = ""
    catalogue: bool = False
    errors: list[str] = field(default_factory=list)


def install_from_asset(
    asset: BundleAsset,
    entry: Optional[CatalogueEntry] = None,
    destination: Optional[Path] = None,
    overwrite: bool = False,
) -> InstallResult:
    """Downloads one release file and installs the bundle it holds. Nothing is run."""
    staging = Path(tempfile.mkdtemp(prefix="sddownload-"))
    try:
        archive = download_bundle(asset, staging / asset.filename)
        if asset.sha256:
            digest = sha256_of(archive)
            if digest != asset.sha256:
                raise MarketplaceError(
                    f"{asset.filename} does not match its published digest ({digest[:12]}… != {asset.sha256[:12]}…)"
                )
        result = install_bundle(archive, destination=destination, overwrite=overwrite)
        result.tag = asset.tag
        result.source_url = asset.url
        if entry is not None:
            result.repository = entry.repository
            result.catalogue = True
        return result
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def fetch_catalogue(cache_path: Optional[Path] = None, force_refresh: bool = False) -> dict[str, CatalogueEntry]:
    """Returns the online catalogue, from a short lived cache when possible."""
    if cache_path is None:
        cache_path = INSTALL_DIRECTORY / "catalogue-cache.json"
    if not force_refresh and cache_path.is_file():
        age = time.time() - cache_path.stat().st_mtime
        try:
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
            if 0 <= age < CATALOGUE_CACHE_TTL_SECONDS:
                return _parse_catalogue(raw)
        except (OSError, ValueError):
            pass

    raw = _get_json(CATALOGUE_URL)
    entries = _parse_catalogue(raw)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(raw), encoding="utf-8")
    except OSError as error:
        logger.warning("Could not cache the plugin catalogue: %s", error)
    return entries


def _parse_catalogue(raw: object) -> dict[str, CatalogueEntry]:
    if not isinstance(raw, dict):
        raise MarketplaceError("The plugin catalogue is not a JSON object")
    entries = {}
    for uuid, item in raw.items():
        if not isinstance(item, dict):
            continue
        entries[uuid] = CatalogueEntry(
            uuid=uuid,
            name=str(item.get("name") or uuid),
            author=str(item.get("author") or ""),
            repository=str(item.get("repository") or ""),
            description=str(item.get("description") or ""),
            download_url=str(item.get("download_url") or ""),
        )
    return entries


def github_repo(repository_url: str) -> Optional[tuple[str, str]]:
    """The (owner, name) of a GitHub repository URL, or None for anything else."""
    match = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", (repository_url or "").strip())
    return (match.group(1), match.group(2)) if match else None


def find_bundle_assets(entry: CatalogueEntry, session: Optional[object] = None) -> list[BundleAsset]:
    """Release files of the plugin repository that plausibly hold its bundle.

    Newest releases first. Files whose name says they are something else (profiles, icons, docs)
    are left out; a bare ``*.zip`` is kept because some publishers do not name their package.
    """
    repo = github_repo(entry.repository)
    if repo is None:
        return []
    owner, name = repo
    url = f"https://api.github.com/repos/{owner}/{name}/releases?per_page=10"
    try:
        releases = _get_json(url, token=getattr(session, "token", None))
    except MarketplaceError as error:
        logger.info("Could not list releases of %s/%s: %s", owner, name, error)
        return []
    if not isinstance(releases, list):
        return []

    assets: list[BundleAsset] = []
    for release in releases:
        if not isinstance(release, dict):
            continue
        for asset in release.get("assets") or []:
            filename = str(asset.get("name") or "")
            if not _could_hold_bundle(filename, entry.uuid):
                continue
            digest = str(asset.get("digest") or "")
            assets.append(
                BundleAsset(
                    url=str(asset.get("browser_download_url") or ""),
                    tag=str(release.get("tag_name") or ""),
                    filename=filename,
                    size=int(asset.get("size") or 0),
                    sha256=digest.removeprefix("sha256:"),
                )
            )
    return assets


def _could_hold_bundle(filename: str, uuid: str) -> bool:
    lowered = filename.lower()
    if not lowered.endswith(_BUNDLE_SUFFIXES):
        return False
    stem = lowered
    for suffix in (PLUGIN_SUFFIX.lower() + ".streamdeckplugin", PLUGIN_SUFFIX.lower(), ".streamdeckplugin", ".zip"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    # A package named after a uuid is only ours when that uuid is ours. Bare names are kept.
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]*[a-z0-9](?:\.[a-z0-9]+)+", stem) and stem != uuid.lower():
        return False
    return True


def download_bundle(asset: BundleAsset, destination: Path) -> Path:
    """Fetches one archive into ``destination`` and returns its path."""
    if asset.size and asset.size > MAX_ARCHIVE_BYTES:
        raise MarketplaceError(f"{asset.filename} is bigger than the {MAX_ARCHIVE_BYTES} byte limit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".part")
    try:
        request = urllib.request.Request(asset.url, headers={"User-Agent": "streamdeck-linux-gui"})
        written = 0
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response, open(temp, "wb") as out:
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_ARCHIVE_BYTES:
                    raise MarketplaceError(f"{asset.filename} is bigger than the {MAX_ARCHIVE_BYTES} byte limit")
                out.write(chunk)
        os.replace(temp, destination)
    except urllib.error.URLError as error:
        raise MarketplaceError(f"Could not download {asset.url}: {error}") from error
    finally:
        temp.unlink(missing_ok=True)
    return destination


def sha256_of(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_bundle(
    archive_path: Path,
    expected_uuid: Optional[str] = None,
    destination: Optional[Path] = None,
    overwrite: bool = False,
) -> InstallResult:
    """Unpacks one downloaded bundle into the plugin directory after checking it.

    Nothing is run: the archive is opened as data only, path escapes and odd file types are
    refused, and the unpacked manifest must parse and match the expected uuid before the bundle
    takes its final place.
    """
    destination = Path(destination or INSTALL_DIRECTORY)
    digest = sha256_of(archive_path)

    workdir = Path(tempfile.mkdtemp(prefix="sdplugin-"))
    try:
        root = _safe_extract(Path(archive_path), workdir)
        manifest = load_plugin_manifest(root)
        if expected_uuid and manifest.uuid != expected_uuid:
            raise MarketplaceError(f"The package holds {manifest.uuid}, not {expected_uuid}")

        target_name = f"{manifest.uuid}{PLUGIN_SUFFIX}"
        target = destination / target_name
        if target.exists() and not overwrite:
            raise MarketplaceError(f"{target} already exists. Choose replace to install over it.")

        # The staging folder must carry the real bundle suffix: a manifest with no UUID takes
        # its identity from the folder name, so checking it under any other name would fail.
        staging_root = destination / f".installing-{manifest.uuid}"
        if staging_root.exists():
            shutil.rmtree(staging_root)
        destination.mkdir(parents=True, exist_ok=True)
        staging = staging_root / target_name
        shutil.copytree(root, staging)
        load_plugin_manifest(staging)  # final check before the swap is visible to anything else

        backup = None
        if target.exists():
            backup = destination / f".replaced-{manifest.uuid}"
            shutil.rmtree(backup, ignore_errors=True)
            os.replace(target, backup)
        try:
            staging.rename(target)
            staging_root.rmdir()
        except OSError:
            if backup is not None and not target.exists():
                os.replace(backup, target)
            raise
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)

        return InstallResult(
            uuid=manifest.uuid,
            path=target,
            name=manifest.name,
            version=manifest.version,
            source_url=str(archive_path),
            sha256=digest,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _safe_extract(archive_path: Path, workdir: Path) -> Path:
    """Unpacks an archive refusing path escapes and non regular entries; returns the bundle root."""
    try:
        archive = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, OSError) as error:
        raise MarketplaceError(f"{archive_path.name} is not a readable zip archive: {error}") from error

    with archive:
        infos = archive.infolist()
        if len(infos) > 50_000:
            raise MarketplaceError("That archive holds too many files")
        total = sum(info.file_size for info in infos)
        if total > MAX_ARCHIVE_BYTES * 4:
            raise MarketplaceError("That archive unpacks to too much data")

        extracted_root: Optional[Path] = None
        for info in infos:
            name = info.filename
            target = (workdir / name).resolve()
            try:
                inside = target.is_relative_to(workdir.resolve())
            except AttributeError:  # Python without is_relative_to
                inside = str(target).startswith(str(workdir.resolve()) + os.sep)
            if not inside or Path(name).is_absolute():
                raise MarketplaceError(f"That archive writes outside its folder: {name}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise MarketplaceError(f"That archive holds a symbolic link: {name}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as out:
                shutil.copyfileobj(source, out, length=1 << 20)

        roots = sorted({part.split("/")[0] for part in (i.filename for i in infos) if part and not part.endswith("/")})
        candidates = [workdir / part for part in roots] + [workdir]
        bundle_root = next((c for c in candidates if c.is_dir() and c.name.lower().endswith(PLUGIN_SUFFIX)), None)
        if bundle_root is None:
            manifest_dirs = [c for c in candidates if (c / "manifest.json").is_file()]
            if manifest_dirs:
                bundle_root = manifest_dirs[0]
        if bundle_root is None:
            raise MarketplaceError("That archive does not hold a .sdPlugin folder")
        extracted_root = bundle_root

    return extracted_root


def uninstall_bundle(plugin_uuid: str, directory: Optional[Path] = None) -> Path:
    """Deletes an installed bundle from our plugin directory and returns what it removed."""
    directory = Path(directory or INSTALL_DIRECTORY)
    target = directory / f"{plugin_uuid}{PLUGIN_SUFFIX}"
    if not target.is_dir():
        raise MarketplaceError(f"{target} is not installed here")
    manifest_ok = False
    try:
        load_plugin_manifest(target)
        manifest_ok = True
    except ManifestError as error:
        logger.warning("Removing %s even though its manifest does not parse: %s", target, error)
    if not manifest_ok and plugin_uuid.lower() != target.stem.lower():
        raise MarketplaceError(f"{target} is not named like a bundle; refusing to remove it")
    shutil.rmtree(target)
    return target


def record_installation(result: InstallResult, metadata_path: Optional[Path] = None) -> None:
    """Keeps a small note next to the bundle saying where it came from."""
    metadata_path = Path(metadata_path or INSTALL_DIRECTORY / ".marketplace.json")
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[result.uuid] = {
        "source": result.source_url,
        "sha256": result.sha256,
        "tag": result.tag,
        "repository": result.repository,
        "catalogue": result.catalogue,
        "installed_at": int(time.time()),
    }
    try:
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError as error:
        logger.warning("Could not record where %s came from: %s", result.uuid, error)


def provenance_of(plugin_uuid: str, metadata_path: Optional[Path] = None) -> Optional[dict]:
    """Where we installed this plugin from ourselves, when we did."""
    metadata_path = Path(metadata_path or INSTALL_DIRECTORY / ".marketplace.json")
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    item = data.get(plugin_uuid) if isinstance(data, dict) else None
    return item if isinstance(item, dict) else None


COMPATIBILITY_CACHE_SUFFIX = "compatibility-cache.json"


def _read_compatibility_cache(path: Path) -> dict[str, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): float(v) for k, v in data.items()}
    except (OSError, ValueError, TypeError):
        pass
    return {}


def compatibility_cache_path(directory: Optional[Path] = None) -> Path:
    return Path(directory or INSTALL_DIRECTORY) / COMPATIBILITY_CACHE_SUFFIX


def filter_compatible_entries(
    entries: dict[str, CatalogueEntry],
    on_progress: Optional[Callable[[str, Optional[bool]], None]] = None,
    cache_path: Optional[Path] = None,
    token: Optional[str] = None,
    max_workers: int = 6,
    refresh_days: float = 30.0,
) -> dict[str, CatalogueEntry]:
    """Keeps the catalogue rows whose package could run on this platform.

    A row is judged by the manifest inside its downloadable bundle: no code for our platform
    or a web code path means we could never launch it. Results are cached because judging all
    two hundred packages takes minutes, and unknown stays listed: absence of evidence about
    someone else's plugin is not proof it does not work here.
    """
    cache_path = Path(cache_path or compatibility_cache_path())
    cache = _read_compatibility_cache(cache_path)
    now = time.time()

    def judge(uuid: str, entry: CatalogueEntry) -> Optional[bool]:
        stamp = cache.get(uuid)
        marker = cache.get(f"#runs:{uuid}")
        fresh = stamp is not None and 0 <= now - stamp < refresh_days * 86400
        if marker is not None:
            # A decided verdict (True/False) stands until its TTL; an unknown one gets a new try.
            if marker >= 0 and fresh:
                return bool(marker)
        elif fresh:
            return None
        try:
            raw = _peek_bundle_manifest(entry, token=token)
        except MarketplaceError:
            raw = None
        verdict = _manifest_runs_here(raw) if isinstance(raw, dict) else None
        cache[uuid] = now
        cache[f"#runs:{uuid}"] = float(verdict) if verdict is not None else -1.0
        return verdict

    verdicts: dict[str, Optional[bool]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(judge, uuid, entry): uuid for uuid, entry in entries.items()}
        for future in as_completed(futures):
            uuid = futures[future]
            try:
                verdicts[uuid] = future.result()
            except Exception:  # noqa: BLE001 - a failing probe leaves the row unknown
                verdicts[uuid] = None
            if on_progress is not None:
                on_progress(uuid, verdicts[uuid])
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
    except OSError as error:
        logger.warning("Could not write the compatibility cache: %s", error)

    return {uuid: entry for uuid, entry in entries.items() if verdicts.get(uuid) is not False}


class _AssetToken:
    """Carries the GitHub token to the release lookup without exposing it further."""

    def __init__(self, token: Optional[str]):
        self.token = token


def _peek_bundle_manifest(entry: CatalogueEntry, token: Optional[str] = None) -> Optional[dict]:
    """Downloads one package and reads its manifest.json as data only."""
    url = entry.download_url or ""
    if not url:
        assets = find_bundle_assets(entry, session=_AssetToken(token))
        url = assets[0].url if assets else ""
    if not url:
        return None
    try:
        raw = _read_package_bytes(url)
    except (urllib.error.URLError, OSError) as error:
        raise MarketplaceError(f"Could not download {url}: {error}") from error
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise MarketplaceError(f"{url} is bigger than the {MAX_ARCHIVE_BYTES} byte limit")
    import io

    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError):
        return None  # a single file bundle downloads as plain json, not an archive
    names = [name for name in archive.namelist() if name.lower().endswith("manifest.json")]
    if not names:
        return None
    try:
        manifest = json.loads(archive.read(names[0]).decode("utf-8"))
    except (ValueError, KeyError):
        return None
    return manifest if isinstance(manifest, dict) else None


def _read_package_bytes(url: str) -> bytes:
    """Fetch one package over the network or read it straight from a local path."""
    if url.startswith(("http://", "https://")):
        request = urllib.request.Request(url, headers={"User-Agent": "streamdeck-linux-gui"})
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            return response.read(MAX_ARCHIVE_BYTES + 1)
    path = Path(url.removeprefix("file://"))
    if not path.is_file():
        raise MarketplaceError(f"{url} is not readable")
    size = path.stat().st_size
    if size > MAX_ARCHIVE_BYTES:
        raise MarketplaceError(f"{url} is bigger than the {MAX_ARCHIVE_BYTES} byte limit")
    return path.read_bytes()


def _manifest_runs_here(manifest: dict) -> bool:
    """The manifest side of PluginManifest.runs_on, without needing an unpacked bundle."""
    from streamdeck_ui.elgato_plugin.manifest import PLATFORM_CODE_KEYS

    platforms = [
        item["Platform"]
        for item in manifest.get("OS") or []
        if isinstance(item, dict) and isinstance(item.get("Platform"), str)
    ]
    override = next(
        (
            item["CodePath"]
            for item in manifest.get("OS") or []
            if isinstance(item, dict) and item.get("Platform") == "linux" and item.get("CodePath")
        ),
        "",
    )
    path = str(manifest.get(PLATFORM_CODE_KEYS["linux"]) or override or manifest.get("CodePath") or "")
    if not path:
        return False
    if Path(path).suffix.lower() in {".html", ".htm", ".js"}:
        return False
    return not platforms or "linux" in platforms


def describe_provenance(root: Path, installed_directories: Optional[Iterable[Path]] = None) -> str:
    """A short human note saying where an installed bundle came from.

    Bundles we downloaded through the catalogue carry a sidecar record; bundles found in the
    shared directory belong to another launcher on this machine; anything else simply sits in
    our plugin folder, which covers bundles copied there by hand and the ones shipped here.
    """
    root = Path(root)
    directories = [Path(directory) for directory in (installed_directories or [])]
    if directories:
        ours = directories[0]
        shared = directories[1:]
    else:
        ours, shared = INSTALL_DIRECTORY, []

    inside_shared = next((d for d in shared if _inside(root, d)), None)
    if inside_shared is not None:
        return f"shared directory ({inside_shared})"

    record = provenance_of(root.name[: -len(PLUGIN_SUFFIX)] if root.name.endswith(PLUGIN_SUFFIX) else root.name)
    if record and record.get("source"):
        source = str(record["source"])
        tag = f" {record['tag']}" if record.get("tag") else ""
        return f"OpenAction catalogue{tag} ({source})"

    if _inside(root, ours):
        return "installed here"
    return "external location"


def _inside(path: Path, directory: Path) -> bool:
    try:
        path = path.resolve()
        directory = directory.resolve()
        try:
            return path.is_relative_to(directory)
        except AttributeError:
            return str(path).startswith(str(directory) + os.sep) or path == directory
    except OSError:
        return False


def _get_json(url: str, token: Optional[str] = None) -> object:
    headers = {"User-Agent": "streamdeck-linux-gui", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError) as error:
        raise MarketplaceError(f"Could not read {url}: {error}") from error
