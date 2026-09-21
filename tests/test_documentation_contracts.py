from __future__ import annotations

from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_agents_md_is_the_only_vendor_neutral_instruction_entrypoint() -> None:
    assert (ROOT / "AGENTS.md").is_file()
    assert not (ROOT / "CLAUDE.md").exists()
    assert not (ROOT / "GEMINI.md").exists()
    assert not (ROOT / ".github" / "copilot-instructions.md").exists()


def test_documentation_entrypoints_exist() -> None:
    expected = {
        "README.md",
        "CONTRIBUTING.md",
        "docs/README.md",
        "docs/AGENTS.md",
        "docs/user-guide.md",
        "docs/development.md",
        "docs/maintainer-guide.md",
    }
    missing = sorted(path for path in expected if not (ROOT / path).is_file())
    assert missing == []


def test_root_agents_is_in_source_distribution_inputs() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    included = set(metadata["tool"]["uv"]["build-backend"]["source-include"])
    assert "AGENTS.md" in included
    assert "docs/**" in included
    assert ".github/**" in included


def test_primary_readme_offers_clear_user_and_contributor_paths() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "docs/user-guide.md" in readme
    assert "Sunshine" in readme
    assert "Moonlight" in readme
    assert re.search(r"AGENTS\.md", readme)
    assert re.search(r"CONTRIBUTING\.md", readme)
    assert "0.0.0" in readme


def test_documentation_links_resolve() -> None:
    """A link to a document that was removed sends the reader nowhere."""
    broken: list[str] = []
    for path in [ROOT / "README.md", ROOT / "CONTRIBUTING.md", *(ROOT / "docs").glob("*.md"), *(ROOT / ".github").rglob("*.md")]:
        for target in re.findall(r"\]\(([^)#:]+)\)", path.read_text(encoding="utf-8")):
            if not (path.parent / target).exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")

    assert broken == []


def test_python_package_metadata_exposes_project_resources() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for key in ("Homepage", "Documentation", "Source", "Issues"):
        assert f"{key} =" in pyproject
    assert 'description = "A guided GTK interface' in pyproject
