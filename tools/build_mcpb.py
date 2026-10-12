"""Package the md-mcp server as an MCPB bundle: one file, no Python needed to install it.

    python tools/build_mcpb.py              # -> dist/md-mcp-<version>.mcpb
    python tools/build_mcpb.py --relock     # re-resolve packaging/mcpb/requirements.txt

An MCPB is a zip with a ``manifest.json`` that Claude Desktop (and other MCPB hosts)
unpacks and launches as a stdio MCP server. The point of one is that the user needs
nothing installed first -- so the bundle has to carry its own interpreter.

**Why the bundle carries CPython rather than asking for one.** MCPB's ``python`` server
type runs whatever Python the user has, and md-mcp's dependencies include compiled
extensions (``pydantic_core``, ``rpds-py``, ``cryptography``, ``pywin32``) built for
one minor version: vendored for 3.14, they fail to import on 3.13. The v0.4 ``uv`` type
has the host install Python and the dependencies, which needs the network on first
launch and a host that implements it. So this bundles CPython's official Windows
embeddable distribution, pinned and hashed, with the locked wheels installed beside it.
MD is Windows-only, so the bundle is too.

What it refuses, each of which would otherwise surface as an extension that installs and
then does nothing:

* **A runtime or wheel whose hash is not the pinned one.** The embeddable zip is checked
  against ``EMBED_SHA256``; the wheels are installed with ``--require-hashes`` from
  ``packaging/mcpb/requirements.txt``.
* **A package that is not the checkout.** The version in ``src/md_mcp/__init__.py`` must
  be pyproject's, and the tool list in the manifest must come from this source tree.
* **A manifest the MCPB v0.4 schema rejects**, checked against the copy in
  ``packaging/mcpb`` so the build does not depend on GitHub being up.
* **A bundle that does not start.** The finished ``.mcpb`` is unpacked into a folder
  whose path has a space in it (Claude Desktop's own does), launched exactly as the
  manifest says, and asked over stdio for its tools and for ``md_guide``. That needs
  Windows; elsewhere the build fails unless ``--no-smoke`` says to skip it.

Rebuilt from the same commit and lock it produces a byte-identical bundle: fixed member
order and timestamps, no ``.pyc`` and no pip launcher executables, which would carry the
building interpreter's path.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging" / "mcpb"
LOCK = PACKAGING / "requirements.txt"
SCHEMA = PACKAGING / "mcpb-manifest-v0.4.schema.json"
SCHEMA_URL = (
    "https://raw.githubusercontent.com/anthropics/mcpb/main/schemas/"
    "mcpb-manifest-v0.4.schema.json"
)

#: The interpreter the bundle carries. 3.14 is what this repo's venv and CI run; the
#: 3.11 MD embeds is irrelevant here, because md-mcp runs in its own process.
PYTHON_VERSION = "3.14.8"
PYTHON_TAG = "314"
EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
    f"python-{PYTHON_VERSION}-embed-amd64.zip"
)
#: Measured 2026-10-12 from python.org's file. A new PYTHON_VERSION needs a new hash.
EMBED_SHA256 = "a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310"

#: Where pip should look for wheels: the bundle's platform, not the building one.
PIP_PLATFORM = [
    "--only-binary=:all:",
    "--platform", "win_amd64",
    "--python-version", PYTHON_VERSION.rsplit(".", 1)[0],
    "--implementation", "cp",
]

#: Lines of the embeddable interpreter's ``._pth``, which replaces sys.path wholesale
#: and makes it ignore PYTHONPATH. Paths are relative to the ``._pth`` file.
#: ``import site`` is what processes ``pywin32.pth``, without which win32api does not
#: import.
PTH_LINES = [f"python{PYTHON_TAG}.zip", ".", r"Lib\site-packages", r"..\server", "import site"]

#: How the host launches the server. ``-I`` keeps the current directory -- which the
#: host chooses -- off sys.path, so nothing lying in it can stand in for a module.
COMMAND = r"${__dirname}\python\python.exe"
ARGS = ["-I", "-m", "md_mcp.server"]

REPOSITORY = "https://github.com/matty/marvelous-designer-plugins"


class BuildError(RuntimeError):
    """The bundle cannot be built as asked, and the reason is in the message."""


def _load_build_plugin():
    """``tools/build_plugin.py``, which builds the listener the bundle also carries."""
    spec = importlib.util.spec_from_file_location(
        "build_plugin", pathlib.Path(__file__).with_name("build_plugin.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_plugin = _load_build_plugin()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pip(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "--disable-pip-version-check", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BuildError(f"pip {args[0]} failed:\n{result.stdout}\n{result.stderr}")


def fetch_runtime(cache: pathlib.Path) -> pathlib.Path:
    """The embeddable CPython zip, downloaded once and checked every time."""
    path = cache / pathlib.PurePosixPath(EMBED_URL).name
    if not path.is_file():
        cache.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(EMBED_URL, timeout=120) as response:
            path.write_bytes(response.read())
    digest = _sha256(path.read_bytes())
    if digest != EMBED_SHA256:
        path.unlink()
        raise BuildError(
            f"{EMBED_URL} has SHA-256 {digest}, not the pinned {EMBED_SHA256}. The bad "
            "copy has been deleted. If PYTHON_VERSION was changed on purpose, pin the "
            "new file's hash in EMBED_SHA256."
        )
    return path


def fetch_wheels(cache: pathlib.Path) -> pathlib.Path:
    """Every locked wheel, for the bundle's platform, each checked against the lock."""
    wheels = cache / "wheels"
    _pip(
        "download", "--quiet", "--no-deps", "--require-hashes",
        "-r", str(LOCK), "-d", str(wheels), *PIP_PLATFORM,
    )
    return wheels


def relock(cache: pathlib.Path) -> None:
    """Re-resolve the dependencies for the bundle's platform and rewrite the lock.

    Run when pyproject's dependencies change, or to take newer versions. The lock pins
    what pip resolves today for the bundle's platform, with hashes.
    """
    with tempfile.TemporaryDirectory(dir=cache if cache.is_dir() else None) as scratch:
        requirements = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
        _pip("download", "--quiet", "-d", scratch, *PIP_PLATFORM,
             *requirements["project"]["dependencies"])
        lines = [
            "# The md-mcp server's dependencies as bundled in the .mcpb: win_amd64,",
            f"# CPython {PYTHON_VERSION}. Written by `python tools/build_mcpb.py --relock`;",
            "# edit pyproject.toml and relock rather than editing this by hand.",
        ]
        for wheel in sorted(pathlib.Path(scratch).glob("*.whl"), key=lambda p: p.name.lower()):
            name, version = wheel.name.split("-")[:2]
            name = re.sub(r"[-_.]+", "-", name).lower()
            lines.append(f"{name}=={version} \\")
            lines.append(f"    --hash=sha256:{_sha256(wheel.read_bytes())}")
    LOCK.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def tool_list() -> list[dict]:
    """The tools this checkout's server registers, for the manifest.

    Imported from the source tree, not trusted from whatever ``md_mcp`` the build
    environment happens to hold: a stale install would publish yesterday's tools.
    """
    import anyio
    from mcp import Client

    import md_mcp
    from md_mcp.server import build_server

    source = ROOT / "src" / "md_mcp"
    if pathlib.Path(md_mcp.__file__).resolve().parent != source.resolve():
        raise BuildError(
            f"md_mcp was imported from {md_mcp.__file__}, not {source}. Install this "
            "checkout (pip install -e .) so the manifest describes the code it ships."
        )

    async def listed():
        async with Client(build_server()) as client:
            return (await client.list_tools()).tools

    return [
        {"name": tool.name, "description": (tool.description or "").strip().split("\n\n")[0]
         .replace("\n", " ")}
        for tool in anyio.run(listed)
    ]


def manifest(version: str, tools: list[dict]) -> dict:
    return {
        "$schema": SCHEMA_URL,
        "manifest_version": "0.4",
        "name": "md-mcp",
        "display_name": "Marvelous Designer",
        "version": version,
        "description": (
            "Drives a running Marvelous Designer through its whole Python API, and "
            "reads the scene back."
        ),
        "long_description": (
            "md-mcp gives Claude every call in Marvelous Designer's Python API -- "
            "searched with md_api, called with md_call, or run as a script with "
            "run_md_python -- plus tools that read the result back: seams, 3D bounds, "
            "snapshots and checkpoints.\n\n"
            "**Marvelous Designer needs the md-mcp plugin as well.** This bundle "
            "carries it: with MD open, ask Claude for md_status, and it gives you two "
            "lines to paste once into MD's Main Menu > Plugins > Python Editor. After "
            "that, Start/Stop MCP in MD's Plugin tab starts and stops the bridge.\n\n"
            "run_md_python runs arbitrary Python inside MD, over a loopback port that "
            "is open only while the bridge is started. MD's API has no undo."
        ),
        "author": {"name": "matty", "url": "https://github.com/matty"},
        "repository": {"type": "git", "url": REPOSITORY},
        "homepage": REPOSITORY,
        "documentation": f"{REPOSITORY}#readme",
        "support": f"{REPOSITORY}/issues",
        "keywords": ["marvelous designer", "clothing", "garment", "3d", "simulation"],
        "server": {
            "type": "python",
            "entry_point": "server/md_mcp/server.py",
            "mcp_config": {
                "command": COMMAND,
                "args": ARGS,
                "env": {
                    "MD_MCP_PORT": "${user_config.port}",
                    "MD_MCP_CHECKPOINTS": "${user_config.checkpoints}",
                },
            },
        },
        "tools": tools,
        "tools_generated": False,
        "user_config": {
            "port": {
                "type": "number",
                "title": "Bridge port",
                "description": (
                    "The loopback port the plugin inside Marvelous Designer listens on. "
                    "Change it only if 5121 is taken, and then set the MD_MCP_PORT "
                    "environment variable to the same number for Marvelous Designer too."
                ),
                "default": 5121,
                "min": 1024,
                "max": 65535,
                "required": False,
            },
            "checkpoints": {
                "type": "directory",
                "title": "Checkpoint folder",
                "description": (
                    "Where md_checkpoint saves garments (.zpac) before risky changes. "
                    "The newest 30 are kept."
                ),
                "default": "${HOME}\\AppData\\Local\\md-mcp\\checkpoints",
                "required": False,
            },
        },
        "compatibility": {"platforms": ["win32"]},
    }


def validate(data: dict) -> None:
    """Refuse a manifest the MCPB v0.4 schema rejects."""
    import jsonschema

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(
        jsonschema.Draft7Validator(schema).iter_errors(data), key=lambda e: list(e.path)
    )
    if errors:
        raise BuildError(
            "manifest.json does not match the MCPB v0.4 schema:\n"
            + "\n".join(f"  {'/'.join(map(str, e.path)) or '(root)'}: {e.message}"
                        for e in errors)
        )


def stage(
    staging: pathlib.Path, runtime: pathlib.Path, wheels: pathlib.Path,
    version: str, commit: str,
) -> None:
    """Lay the bundle out in ``staging``: python/, server/, plugin/."""
    python = staging / "python"
    with zipfile.ZipFile(runtime) as archive:
        archive.extractall(python)
    pth = python / f"python{PYTHON_TAG}._pth"
    if not pth.is_file():
        raise BuildError(f"{runtime.name} has no {pth.name}; is it an embeddable build?")
    pth.write_text("\n".join(PTH_LINES) + "\n", encoding="utf-8", newline="\n")

    site_packages = python / "Lib" / "site-packages"
    _pip(
        "install", "--quiet", "--no-deps", "--no-compile", "--no-index",
        "--require-hashes", "--find-links", str(wheels), "--target", str(site_packages),
        "-r", str(LOCK), *PIP_PLATFORM,
    )
    # Console-script launchers: .exe files with the *building* interpreter's path in
    # them, useless in the bundle and different on every build machine.
    # The RECORDs list them too, with hashes that differ per build: drop those lines,
    # or two builds of one commit are different files.
    shutil.rmtree(site_packages / "bin", ignore_errors=True)
    for record in sorted(site_packages.glob("*.dist-info/RECORD")):
        lines = record.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [line for line in lines if not line.startswith("../../bin/")]
        if kept != lines:
            record.write_text("".join(kept), encoding="utf-8", newline="")

    package = staging / "server" / "md_mcp"
    package.mkdir(parents=True)
    for source in sorted((ROOT / "src" / "md_mcp").iterdir()):
        if source.suffix in {".py", ".md"}:
            package.joinpath(source.name).write_bytes(
                source.read_bytes().replace(b"\r\n", b"\n")
            )

    # The MD half, stamped as the plugin zip's is. bridge.listener_script() finds it at
    # <bundle>/plugin, so md_status can say exactly what to paste into MD.
    plugin = staging / "plugin"
    plugin.mkdir()
    for name, data in build_plugin.contents(version, commit).items():
        (plugin / name).write_bytes(data)


def write_mcpb(path: pathlib.Path, staging: pathlib.Path, manifest_bytes: bytes) -> None:
    """Zip ``staging`` with ``manifest.json`` at the root, deterministically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    members = {"manifest.json": manifest_bytes}
    for file in staging.rglob("*"):
        if file.is_file():
            members[file.relative_to(staging).as_posix()] = file.read_bytes()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)


def smoke(bundle: pathlib.Path) -> dict:
    """Unpack the finished bundle and talk to it the way a host would.

    Returns what it saw, for the build log. Raises :class:`BuildError` if the server
    does not start, lists other tools than the manifest names, or cannot serve
    md_guide -- a pure tool, so this needs no Marvelous Designer.
    """
    if sys.platform != "win32":
        raise BuildError(
            "the smoke test launches the bundled python.exe, which needs Windows. "
            "Build on Windows, or pass --no-smoke to ship it untested."
        )
    import anyio
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    with tempfile.TemporaryDirectory(prefix="md-mcp mcpb ") as scratch:
        root = pathlib.Path(scratch) / "Claude Extensions" / "md-mcp"
        with zipfile.ZipFile(bundle) as archive:
            archive.extractall(root)
        data = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        config = data["server"]["mcp_config"]
        command = config["command"].replace("${__dirname}", str(root))

        imports = subprocess.run(
            [command, "-I", "-c", "import md_mcp.server, win32api, pydantic_core; "
             "print(md_mcp.__version__)"],
            capture_output=True, text=True, timeout=120,
        )
        if imports.returncode != 0:
            raise BuildError(f"the bundled interpreter cannot import md-mcp:\n{imports.stderr}")

        env = {
            key: value.replace("${user_config.port}", "5121").replace(
                "${user_config.checkpoints}", str(pathlib.Path(scratch) / "checkpoints"))
            for key, value in config.get("env", {}).items()
        }

        async def talk():
            params = StdioServerParameters(command=command, args=config["args"], env=env)
            async with Client(params) as client:
                tools = (await client.list_tools()).tools
                guide = await client.call_tool("md_guide", {})
                return [tool.name for tool in tools], guide

        names, guide = anyio.run(talk)

    expected = [tool["name"] for tool in data["tools"]]
    if sorted(names) != sorted(expected):
        raise BuildError(f"the bundle serves {sorted(names)}, the manifest names {sorted(expected)}")
    if guide.is_error:
        raise BuildError(f"md_guide failed inside the bundle: {guide.content[0].text}")
    return {"version": imports.stdout.strip(), "tools": len(names)}


def build(
    out_dir: pathlib.Path,
    cache: pathlib.Path,
    commit: str | None = None,
    run_smoke: bool = True,
) -> pathlib.Path:
    """Build ``dist/md-mcp-<version>.mcpb``. Returns its path."""
    version = build_plugin.project_version(ROOT)
    commit = commit if commit is not None else build_plugin.git_commit(ROOT)
    init = (ROOT / "src" / "md_mcp" / "__init__.py").read_text(encoding="utf-8")
    if f'__version__ = "{version}"' not in init:
        raise BuildError(
            f"src/md_mcp/__init__.py does not say __version__ = \"{version}\", which "
            "pyproject.toml does. Both halves report a version; they must agree."
        )

    data = manifest(version, tool_list())
    validate(data)
    manifest_bytes = (json.dumps(data, indent=2) + "\n").encode("utf-8")

    runtime = fetch_runtime(cache)
    wheels = fetch_wheels(cache)
    bundle = out_dir / f"md-mcp-{version}.mcpb"
    with tempfile.TemporaryDirectory() as scratch:
        staging = pathlib.Path(scratch) / "bundle"
        stage(staging, runtime, wheels, version, commit)
        write_mcpb(bundle, staging, manifest_bytes)

    (out_dir / f"{bundle.name}.sha256").write_text(
        f"{_sha256(bundle.read_bytes())}  {bundle.name}\n", encoding="utf-8", newline="\n"
    )
    if run_smoke:
        smoke(bundle)
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=pathlib.Path, default=ROOT / "dist",
                        help="where to write the bundle (default: dist/)")
    parser.add_argument("--cache", type=pathlib.Path, default=ROOT / "build" / "mcpb-cache",
                        help="where downloads are kept between builds")
    parser.add_argument("--commit", help="override the commit stamped into the listener")
    parser.add_argument("--no-smoke", action="store_true",
                        help="skip launching the finished bundle (needed off Windows)")
    parser.add_argument("--relock", action="store_true",
                        help="re-resolve packaging/mcpb/requirements.txt and stop")
    args = parser.parse_args(argv)

    try:
        if args.relock:
            args.cache.mkdir(parents=True, exist_ok=True)
            relock(args.cache)
            print(f"wrote {LOCK}")
            return 0
        bundle = build(args.out, args.cache, commit=args.commit, run_smoke=not args.no_smoke)
    except (BuildError, build_plugin.BuildError) as exc:
        print(f"build_mcpb: {exc}", file=sys.stderr)
        return 1

    size = bundle.stat().st_size / 1e6
    print(f"{bundle}  ({size:.1f} MB)\n  sha256 {_sha256(bundle.read_bytes())}")
    print("  smoke test: skipped" if args.no_smoke else "  smoke test: started, listed its tools, served md_guide")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
