#!/usr/bin/env python3
"""Fast, dependency-free release consistency checker."""

from __future__ import annotations

import configparser
import re
import stat
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []
WARNINGS: list[str] = []


def error(message: str) -> None:
    ERRORS.append(message)


def warning(message: str) -> None:
    WARNINGS.append(message)


def text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        error(f"cannot read {path.relative_to(ROOT)}: {exc}")
        return ""


required = [
    "README.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "COPYING",
    "pyproject.toml",
    "usr/bin/big-remote-play",
    "tools/release/install_private_wheel.py",
    "docs/README.md",
    "docs/AGENTS.md",
    "docs/user-guide.md",
    "docs/development.md",
    "docs/maintainer-guide.md",
    "docs/architecture.md",
    "docs/release-testing.md",
    "docs/translations.md",
    "docs/troubleshooting.md",
]
for name in required:
    if not (ROOT / name).is_file():
        error(f"missing required file: {name}")

try:
    metadata = tomllib.loads(text(ROOT / "pyproject.toml"))
    project = metadata["project"]
    version = str(project["version"])
except Exception as exc:
    error(f"invalid pyproject.toml/project metadata: {exc}")
    project = {}
    version = ""

readme_ref = project.get("readme")
if isinstance(readme_ref, str) and not (ROOT / readme_ref).is_file():
    error(f"project.readme does not exist: {readme_ref}")

# Canonical version sources.
version_sources: dict[str, str] = {}
init = ROOT / "src" / "big_remote_play" / "__init__.py"
if init.is_file():
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)', text(init), re.MULTILINE)
    if m:
        version_sources[str(init.relative_to(ROOT))] = m.group(1)
    else:
        error("__version__ missing from src/big_remote_play/__init__.py")

nix = ROOT / "default.nix"
if nix.is_file():
    m = re.search(r'^\s*version\s*=\s*"([^"]+)";', text(nix), re.MULTILINE)
    if m:
        version_sources["default.nix"] = m.group(1)

pkgbuild = ROOT / "pkgbuild" / "PKGBUILD"
if pkgbuild.is_file():
    pkgbuild_text = text(pkgbuild)
    m = re.search(r"^pkgver=(.+)$", pkgbuild_text, re.MULTILINE)
    if m is None:
        error("pkgbuild/PKGBUILD: no pkgver assignment")
    else:
        declared = m.group(1).strip().strip("\"'")
        if re.fullmatch(r"[\d.]+", declared):
            # A hand-pinned literal must still agree with the checkout.
            version_sources["pkgbuild/PKGBUILD"] = declared
        elif "+%y.%m.%d" not in declared:
            error(f"pkgbuild/PKGBUILD: pkgver is neither a literal nor the build date: {declared}")
        else:
            # Same inputs as tools/release/build_version.py, so the wheel and the
            # native package built from one tree carry the same version.
            for variable in ("BRP_BUILD_VERSION", "SOURCE_DATE_EPOCH"):
                if variable not in pkgbuild_text:
                    error(f"pkgbuild/PKGBUILD: build-date pkgver ignores {variable}")
            if re.search(r"^pkgver\(\)", pkgbuild_text, re.MULTILINE):
                # makepkg rewrites the pkgver= line whenever this function
                # exists, corrupting the command substitution above it.
                error("pkgbuild/PKGBUILD: a pkgver() function cannot coexist with a computed pkgver=")

metainfo_files = sorted((ROOT / "usr" / "share" / "metainfo").glob("*.xml"))
for path in metainfo_files:
    try:
        tree = ET.parse(path)
        component = tree.getroot()
        if component.tag != "component":
            error(f"{path.relative_to(ROOT)}: root element is not component")
        component_id = component.findtext("id")
        if not component_id:
            error(f"{path.relative_to(ROOT)}: missing component/id")
        launchable = component.find("launchable[@type='desktop-id']")
        if launchable is None or not (launchable.text or "").strip():
            error(f"{path.relative_to(ROOT)}: missing desktop-id launchable")
        release = component.find("releases/release")
        if release is None or not release.attrib.get("version"):
            error(f"{path.relative_to(ROOT)}: missing latest release version")
        else:
            version_sources[str(path.relative_to(ROOT))] = release.attrib["version"]
    except ET.ParseError as exc:
        error(f"invalid AppStream XML {path.relative_to(ROOT)}: {exc}")
if not metainfo_files:
    error("no AppStream metainfo file found")

for source, found in version_sources.items():
    if version and found != version:
        error(f"version mismatch: pyproject={version}, {source}={found}")

# Desktop entry structure.
desktop_files = sorted((ROOT / "usr" / "share" / "applications").glob("*.desktop"))
for path in desktop_files:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read_string(text(path))
        entry = parser["Desktop Entry"]
        for key in ("Type", "Name", "Exec", "Icon", "Categories"):
            if not entry.get(key, "").strip():
                error(f"{path.relative_to(ROOT)}: missing {key}")
        if entry.get("Type") != "Application":
            error(f"{path.relative_to(ROOT)}: Type must be Application")
    except Exception as exc:
        error(f"invalid desktop entry {path.relative_to(ROOT)}: {exc}")
if not desktop_files:
    error("no desktop entry found")

# Native rolling-release launcher and package layout.
launcher = ROOT / "usr/bin/big-remote-play"
if launcher.is_file():
    launcher_text = text(launcher)
    first_line = launcher_text.splitlines()[0] if launcher_text.splitlines() else ""
    if first_line != "#!/usr/bin/python3 -I":
        error("usr/bin/big-remote-play must use the direct isolated Python launcher")
    if not (launcher.stat().st_mode & stat.S_IXUSR):
        error("usr/bin/big-remote-play is not executable")
    for forbidden in ("/usr/lib/python3.", "python3.*", "exec env PYTHONPATH", 'glob("/usr/lib/python'):
        if forbidden in launcher_text:
            error(f"usr/bin/big-remote-play scans or trusts a versioned Python path: {forbidden}")
    if 'prefix / "lib" / _PRIVATE_DIRECTORY' not in launcher_text:
        error("usr/bin/big-remote-play does not derive its private lib directory from the install prefix")

if pkgbuild.is_file():
    pkgbuild_text = text(pkgbuild)
    if "tools/release/install_private_wheel.py" not in pkgbuild_text:
        error("pkgbuild/PKGBUILD does not install the wheel into the private application root")
    if '"${pkgdir}/usr/lib/${pkgname}"' not in pkgbuild_text:
        error("pkgbuild/PKGBUILD is missing /usr/lib/${pkgname} private installation")
    for forbidden in ("python -m installer", "python-installer", "/usr/lib/python3."):
        if forbidden in pkgbuild_text:
            error(f"pkgbuild/PKGBUILD retains a Python-minor-bound installation: {forbidden}")

# Local Markdown links in primary project documentation.
link_rx = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
markdown_paths = [
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "SECURITY.md",
    *sorted((ROOT / "docs").glob("*.md")),
    *sorted((ROOT / ".github").glob("*.md")),
]
for path in markdown_paths:
    if not path.is_file():
        continue
    for target in link_rx.findall(text(path)):
        target = target.strip().split()[0].strip("<>")
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = target.split("#", 1)[0]
        if not target:
            continue
        resolved = (path.parent / target).resolve()
        try:
            resolved.relative_to(ROOT.resolve())
        except ValueError:
            error(f"{path.relative_to(ROOT)}: link escapes project: {target}")
            continue
        if not resolved.exists():
            error(f"{path.relative_to(ROOT)}: broken local link: {target}")

# Translation inventory.
locale_dir = ROOT / "locale"
linguas_path = locale_dir / "LINGUAS"
if linguas_path.is_file():
    languages = [line.strip() for line in text(linguas_path).splitlines() if line.strip() and not line.lstrip().startswith("#")]
    po_languages = {p.stem for p in locale_dir.glob("*.po")}
    missing_po = sorted(set(languages) - po_languages)
    unlisted_po = sorted(po_languages - set(languages))
    if missing_po:
        error("LINGUAS entries without PO files: " + ", ".join(missing_po))
    if unlisted_po:
        error("PO files absent from LINGUAS: " + ", ".join(unlisted_po))
else:
    error("locale/LINGUAS is missing")

# User-facing launchers must be executable and have an interpreter.
for path in sorted((ROOT / "usr/bin").glob("*")):
    if not path.is_file():
        continue
    rel = path.relative_to(ROOT)
    content = text(path)
    if not content.startswith("#!"):
        error(f"{rel}: missing shebang")
    if not (path.stat().st_mode & stat.S_IXUSR):
        error(f"{rel}: not executable")

# Shell helpers must be executable and syntactically recognizable.
for path in sorted(ROOT.rglob("*.sh")):
    rel = path.relative_to(ROOT)
    content = text(path)
    if not content.startswith("#!"):
        error(f"{rel}: missing shebang")
    if not (path.stat().st_mode & stat.S_IXUSR):
        error(f"{rel}: not executable")

# Release tree hygiene.
for path in ROOT.rglob("*"):
    rel = path.relative_to(ROOT)
    parts = set(rel.parts)
    if parts & {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".venv", "build", "dist"}:
        error(f"generated path in source tree: {rel}")
    if path.is_file() and (path.suffix in {".pyc", ".pyo", ".swp"} or path.name in {".coverage"}):
        error(f"generated file in source tree: {rel}")
    if path.is_file() and len(rel.parts) == 1 and path.name.endswith((".tar.gz", ".zip", ".patch", ".diff")):
        error(f"release artifact nested in source root: {rel}")

# Guard against accidental build-machine paths in shipped text/configuration.
for path in ROOT.rglob("*"):
    if not path.is_file() or path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".mo", ".gz", ".zip"}:
        continue
    # This checker necessarily contains the literal path patterns it searches
    # for; do not report its own guard strings as a build-machine leak.
    if path.resolve() == Path(__file__).resolve():
        continue
    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    if "/mnt/data/" in content or "/tmp/" in content and path.suffix not in {".py"}:
        warning(f"possible build-machine path in {path.relative_to(ROOT)}")

for item in WARNINGS:
    print(f"WARNING: {item}")
for item in ERRORS:
    print(f"ERROR: {item}")
print(f"release-check: {len(ERRORS)} error(s), {len(WARNINGS)} warning(s), version {version or 'unknown'}")
raise SystemExit(1 if ERRORS else 0)
