"""Package the listener as something Marvelous Designer can be given once and keep.

    python tools/build_plugin.py            # -> dist/md-mcp-plugin-<version>.zip

MD's plugin format is a `.py` file and nothing else: no manifest, no bundle, no
entry point. So "packaging" here is not a transformation, it is a promise -- that the
file MD ends up holding is a known version of a known commit, checkable after the fact
by anyone who has the zip. The build stamps the version and the commit into the file,
records a SHA-256 of everything, and refuses to ship a listener that would not run.

What it refuses, and why each one is worth a failed build rather than a bug report from
inside MD:

* **Syntax MD's interpreter cannot parse.** MD 2026.0.315 embeds CPython 3.11.8, and
  this build script may well be run on something newer. A 3.12-ism gets through every
  test on the build machine and fails at the only moment that matters -- in MD, in
  front of a user, with the traceback in a window they have to go and find.
* **An import of anything that is not stdlib or MD's own modules.** The listener is
  handed to MD as one file; there is nowhere for a dependency to come from. A
  ``import requests`` here would install fine and die on click.
* **A missing stamp.** If the placeholders are gone, the zip would carry a listener
  claiming to be a source checkout, and ``md_status`` would report that forever.

Rebuilt from the same commit it produces a byte-identical zip -- no timestamps, no
build host, fixed member order -- so the SHA-256 in a release can be reproduced rather
than trusted.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import pathlib
import subprocess
import sys
import tomllib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: MD's own embedded interpreter, measured on 2026.0.315. The listener is parsed
#: against this grammar rather than the one running the build.
MD_PYTHON = (3, 11)

#: Modules Marvelous Designer injects. The listener may import these and nothing else
#: outside the standard library -- see the module docstring.
MD_HOST_MODULES = frozenset(
    {"pattern_api", "utility_api", "import_api", "export_api", "fabric_api", "ApiTypes"}
)

#: The lines ``build`` rewrites, and what it puts in their place. Matched whole and
#: exactly once each: a near-match that silently did nothing would ship an unstamped
#: listener, which is the failure this file exists to prevent.
VERSION_PLACEHOLDER = '__version__ = "0.0.0+source"'
BUILD_PLACEHOLDER = 'BUILD = "source checkout"'

#: Everything that goes in the zip, as {name in the package: file in the repo}. The
#: listener is stamped on the way through; the rest are copied verbatim.
PAYLOAD = {
    "md_mcp_listener.py": "plugin/md_mcp_listener.py",
    "install.ps1": "plugin/install.ps1",
    "README.md": "plugin/README.md",
}

ENTRY = "md_mcp_listener.py"


class BuildError(RuntimeError):
    """The plugin cannot be packaged as asked, and the reason is in the message."""


def project_version(root: pathlib.Path = ROOT) -> str:
    """The version in pyproject.toml — the one the MCP server half reports too.

    Both halves carry the same number on purpose. They are two ends of one protocol,
    and the first question about a bridge that half-works is which two things are
    talking.
    """
    with (root / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def git_commit(root: pathlib.Path = ROOT) -> str:
    """The commit being packaged, or ``"unknown"`` outside a git checkout.

    Never fatal: a zip built from an exported tree is still a usable zip, and a build
    that fails because ``git`` is missing fails for a reason that has nothing to do
    with the plugin.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def stamp(source: str, version: str, commit: str) -> str:
    """Replace the listener's placeholders with what is actually being shipped."""
    for placeholder, replacement in (
        (VERSION_PLACEHOLDER, f'__version__ = "{version}"'),
        (BUILD_PLACEHOLDER, f'BUILD = "{commit}"'),
    ):
        found = source.count(placeholder)
        if found != 1:
            raise BuildError(
                f"expected exactly one {placeholder!r} in the listener, found {found}. "
                "tools/build_plugin.py stamps that line; without it the packaged "
                "plugin would report a version nobody built."
            )
        source = source.replace(placeholder, replacement)
    return source


def check_runs_in_md(source: str, name: str) -> None:
    """Refuse a listener MD could not parse, or could not find the imports for."""
    try:
        tree = ast.parse(source, filename=name, feature_version=MD_PYTHON)
    except SyntaxError as exc:
        raise BuildError(
            f"{name} is not valid Python "
            f"{MD_PYTHON[0]}.{MD_PYTHON[1]}, which is what Marvelous Designer embeds: "
            f"{exc}. It may well parse under the interpreter running this build; MD is "
            "the one that has to run it."
        ) from exc

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    outside = sorted(imported - sys.stdlib_module_names - MD_HOST_MODULES)
    if outside:
        raise BuildError(
            f"{name} imports {outside}, which is neither standard library nor a module "
            "MD injects. MD is handed this one file and nothing else, so there is "
            "nowhere for that to come from -- it would install cleanly and fail on the "
            "first click. This is deliberately absolute: an optional import, even one "
            "in a try/except that falls back, is a thing that behaves differently on "
            "somebody else's machine."
        )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def contents(
    version: str, commit: str, root: pathlib.Path = ROOT
) -> dict[str, bytes]:
    """Every file that goes in the zip, built in memory and checked before it lands."""
    files: dict[str, bytes] = {}
    for name, relative in PAYLOAD.items():
        path = root / relative
        if not path.is_file():
            raise BuildError(f"{relative} is missing from the checkout")
        text = path.read_text(encoding="utf-8")
        if name == ENTRY:
            text = stamp(text, version, commit)
            check_runs_in_md(text, name)
        # LF regardless of the platform building it: MD reads the file as text, and a
        # zip whose bytes depend on the build host cannot be checked against a
        # published hash.
        files[name] = text.replace("\r\n", "\n").encode("utf-8")

    manifest = {
        "name": "md-mcp-plugin",
        "version": version,
        "commit": commit,
        "entry": ENTRY,
        # Not a compatibility range: this is the one build any of it was measured
        # against. MD's API has moved under this repo's feet before.
        "measured_against_md": "2026.0.315",
        "md_python": "{}.{}".format(*MD_PYTHON),
        "files": {name: _sha256(data) for name, data in sorted(files.items())},
    }
    files["manifest.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return files


def write_zip(path: pathlib.Path, folder: str, files: dict[str, bytes]) -> None:
    """Write the package, deterministically.

    Fixed member order, a fixed timestamp and fixed permissions, because the point of
    publishing a hash is that somebody else can arrive at the same one. Python's
    default ``ZipFile.write`` would stamp each member with its mtime and make every
    rebuild a different file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(f"{folder}/{name}", date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)


def build(
    out_dir: pathlib.Path,
    version: str | None = None,
    commit: str | None = None,
    root: pathlib.Path = ROOT,
) -> pathlib.Path:
    """Package the plugin into ``out_dir``. Returns the path of the zip."""
    version = version or project_version(root)
    commit = commit if commit is not None else git_commit(root)
    folder = f"md-mcp-plugin-{version}"
    files = contents(version, commit, root)

    archive = out_dir / f"{folder}.zip"
    write_zip(archive, folder, files)
    digest = _sha256(archive.read_bytes())
    # Beside the zip and named after it, which is what `sha256sum -c` expects to read.
    # newline="\n" on purpose: on Windows this would otherwise be written CRLF, and
    # `sha256sum -c` reads the carriage return as part of the filename to look for.
    (out_dir / f"{folder}.zip.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8", newline="\n"
    )

    # Also unpacked, so the listener can be pointed at straight from the build
    # directory without anyone unzipping anything to try it.
    staged = out_dir / folder
    staged.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (staged / name).write_bytes(data)
    return archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=ROOT / "dist",
        help="where to write the package (default: dist/)",
    )
    parser.add_argument(
        "--version",
        help="override the version; defaults to the one in pyproject.toml",
    )
    parser.add_argument(
        "--commit",
        help="override the commit stamped into the listener; defaults to git HEAD",
    )
    args = parser.parse_args(argv)

    try:
        archive = build(args.out, version=args.version, commit=args.commit)
    except BuildError as exc:
        print(f"build_plugin: {exc}", file=sys.stderr)
        return 1

    digest = _sha256(archive.read_bytes())
    print(f"{archive}\n  sha256 {digest}")
    print(f"  unpacked alongside it in {archive.with_suffix('')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
