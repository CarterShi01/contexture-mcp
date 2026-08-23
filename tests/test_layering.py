"""Tests that the package layering is real and not merely documented.

The architecture claims each layer may import the ones below it and never the
reverse. A claim like that decays silently — one convenient import inside
`core` and the whole shape is gone with nothing failing. These tests are what
make it fail.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

SOURCE_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = SOURCE_ROOT / "contexture"

#: Directories whose children are layers in their own right, rather than one
#: layer apiece. A directory named here is a namespace of layers: under it,
#: `core/model/role.py` belongs to `core.model` and never merely to `core`.
#:
#: Without this, a layer moved *into* another directory stops being checked and
#: nothing fails — the exact silent decay these tests exist to prevent, arriving
#: through the file tree instead of through an import.
NESTED: frozenset[str] = frozenset({"core"})

#: The layer a module directly inside a nested directory belongs to: shared
#: ground every sibling layer may stand on, and which may itself depend on
#: nothing. Spelled with dunders so it can never collide with a directory name.
BASE = "__base__"

#: The package root itself. It is deliberately not a layer — the facade is
#: allowed to reach every one of them — but it still needs a name, because the
#: rules that apply to it regardless (it may not import the SDK) have to be
#: able to ask what it is.
FACADE = "__facade__"

#: Layer name -> the layers it is allowed to import from.
ALLOWED: dict[str, set[str]] = {
    # Shared ground: errors, types, constants. Everything may stand on it and
    # it stands on nothing, which is what lets the sibling layers below stay
    # independent of each other without duplicating an exception hierarchy.
    "core.__base__": set(),
    # What a capability *is*, where it hangs, and how much of it one call
    # answers with. The reference, the level and the four system entry points
    # all live here: since ADR 014 navigation is part of the kernel rather than
    # a layer above it, so there is no seam left to draw between a node and the
    # call that discloses one.
    "core.model": {"core.__base__"},
    # What this server exposes on each of MCP's three primitives. The
    # load-bearing part of this entry is what it *omits*: `core.model`. The
    # protocol plane must not know the object model — it holds names and
    # reference *strings*, so nothing in it can reach into the forest and
    # nothing in the forest can reach back. It stands on the shared ground
    # like everything else, and it does not import the SDK; see `SDK_LAYERS`.
    "core.mcp_interface": {"core.__base__"},
    # A business application's composition root depends only on declarations
    # and protocol-plane declarations. It is deliberately below `server` so
    # importing `app` does not build a wire object.
    "application": {"core.__base__", "core.model", "core.mcp_interface"},
    "server": {
        "core.__base__",
        "core.model",
        "core.mcp_interface",
        "application",
    },
    # The human-facing inbound adapter. It publishes explicit HTTP addresses
    # over a runtime that core.model already owns; it may not import MCP or the
    # MCP server layer.
    "web": {"core.__base__", "core.model"},
    # The bundled reference application sits above everything and is imported
    # by nothing. It reaches everything a declaration writes with through the
    # public facade — `Role`, `Skill`, `Tool`, `Prompt` and `Resource` all
    # arrive from `contexture` — so the one layer left to name is `server`, to
    # build an app. See LayeringTests.test_the_demo_uses_only_the_public_facade.
    #
    # Named `demo` rather than `examples` because it is one thing with a job:
    # `contexture demo` serves it, `contexture inspect` replays it when there
    # is no project, and the tests drive it. A case study that only wants
    # reading is documentation and lives in `docs/`.
    "demo": {"server"},
    # Replaying the disclosure for a developer to read sits above the server
    # and below the command line. It reaches `server` for the text an agent is
    # given and the budget it has to fit, and never for the wire.
    "inspection": {
        "core.__base__",
        "core.model",
        "core.mcp_interface",
        "server",
    },
    # The command line sits above everything: it scaffolds, inspects, serves.
    "cli": {
        "application",
        "core.__base__",
        "core.model",
        "core.mcp_interface",
        "server",
        "inspection",
    },
}

#: Layers permitted to import the official MCP SDK. The object model is not one
#: of them: `core` must stay describable without a wire protocol in the room.
#:
#: `demo` is not one either, and that is the sharper claim: the bundled
#: reference application is what a business project is copied from, so a
#: permission it never uses is a permission somebody eventually copies. It
#: declares roles and builds an app through the public facade, which is the
#: whole of what a project should need.
SDK_LAYERS = frozenset({"server"})

#: The SDK is two distributions, not one, and a rule that names only the first
#: leaves the second as an unguarded way in. Both are checked everywhere the
#: SDK is checked.
SDK_PACKAGES = frozenset({"mcp", "mcp_types"})


#: Package data that ships in the wheel but is never imported. A project
#: template contains `.py` files on purpose: they are what a generated project
#: starts from, and they belong to no layer of this one.
DATA_DIRECTORIES = frozenset({"templates"})


def _children_of(directory: str) -> frozenset[str]:
    """The sub-layers a nested directory holds, read off the tree itself.

    Discovered rather than listed, so that adding a sub-layer cannot be done
    without `ALLOWED` gaining an entry for it: an undeclared one fails
    `test_every_module_belongs_to_a_known_layer` on its first module.
    """

    root = PACKAGE / directory
    if not root.is_dir():
        return frozenset()
    return frozenset(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir()
        and not entry.name.startswith("__")
        and entry.name not in DATA_DIRECTORIES
    )


NESTED_CHILDREN: dict[str, frozenset[str]] = {
    name: _children_of(name) for name in NESTED
}


def _source_modules(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if not (set(path.relative_to(PACKAGE).parts) & DATA_DIRECTORIES)
    ]


def _module_name(path: Path) -> str:
    """The dotted name this file is imported under."""

    parts = path.relative_to(PACKAGE).parts
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts = (*parts[:-1], parts[-1][: -len(".py")])
    return ".".join(("contexture", *parts))


def _layer_of_module(module: str) -> str | None:
    """Map a dotted module name onto the layer that owns it, or None.

    None means the name is not part of this package — a third-party import,
    or the facade itself, neither of which any layer rule applies to.
    """

    parts = module.split(".")
    if parts[0] != "contexture":
        return None
    if len(parts) == 1:
        return FACADE
    head = parts[1]
    if head not in NESTED:
        return head
    if len(parts) > 2 and parts[2] in NESTED_CHILDREN[head]:
        return f"{head}.{parts[2]}"
    return f"{head}.{BASE}"


def _layer_of(path: Path) -> str:
    layer = _layer_of_module(_module_name(path))
    assert layer is not None, f"{path} is not inside the package"
    return layer


def _absolute(importer: str, is_package: bool, level: int, module: str) -> str:
    """Resolve one relative import against the module that wrote it.

    Relative imports are resolved to full dotted names before being mapped to a
    layer, rather than counted as levels. Counting worked while every layer sat
    one directory deep and stops working the moment one does not — and it
    stops by under-reporting, which is the failure mode that goes unnoticed.
    """

    parts = importer.split(".")
    if not is_package:
        parts = parts[:-1]  # a module's `.` is the package holding it
    if level > 1:
        parts = parts[: len(parts) - (level - 1)]
    return ".".join([*parts, *(module.split(".") if module else [])])


def _imported_layers(path: Path) -> set[str]:
    """Return the layers a module imports, relative imports resolved first."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    importer = _module_name(path)
    is_package = path.name == "__init__.py"
    found: set[str] = set()

    def record(module: str) -> None:
        layer = _layer_of_module(module)
        if layer is None or layer == FACADE:
            # The facade is an entrance, not a layer above anything. Naming it
            # is how a reference application is *supposed* to reach the object
            # model — see `test_examples_use_only_the_public_facade`, which is
            # what actually constrains that import.
            return
        found.add(layer)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            target = (
                node.module or ""
                if node.level == 0
                else _absolute(importer, is_package, node.level, node.module or "")
            )
            if node.module is None:
                # `from .. import disclosure` names its modules in the aliases,
                # and one of them may be a whole sub-layer.
                for alias in node.names:
                    record(f"{target}.{alias.name}")
            else:
                record(target)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                record(alias.name)

    return found


def _contexture_imports(path: Path) -> set[str]:
    """Return every `contexture`-rooted module a file imports, absolute or not."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                found.add("." * node.level + (node.module or ""))
            elif node.module == "contexture" or (
                node.module or ""
            ).startswith("contexture."):
                found.add(node.module or "")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "contexture" or alias.name.startswith("contexture."):
                    found.add(alias.name)
    return found


class LayeringTests(unittest.TestCase):
    def test_every_module_belongs_to_a_known_layer(self) -> None:
        for path in _source_modules(PACKAGE):
            if path.name == "__init__.py" and path.parent == PACKAGE:
                continue
            with self.subTest(module=str(path.relative_to(PACKAGE))):
                self.assertIn(_layer_of(path), ALLOWED)

    def test_no_layer_imports_one_above_it(self) -> None:
        for path in _source_modules(PACKAGE):
            if path.name == "__init__.py" and path.parent == PACKAGE:
                continue  # the facade is allowed to reach every layer
            layer = _layer_of(path)
            permitted = ALLOWED[layer] | {layer}
            for imported in _imported_layers(path):
                with self.subTest(module=str(path.relative_to(PACKAGE))):
                    self.assertIn(
                        imported,
                        permitted,
                        f"{layer} must not import {imported}",
                    )

    def test_only_the_server_layer_imports_the_mcp_sdk(self) -> None:
        """The framework claim, checked rather than asserted in prose.

        Business code declares roles and capabilities; the SDK appears only
        where declarations are projected onto the wire. If `core` ever imports
        `mcp`, the object model has quietly become a protocol binding.
        """

        for path, relative, tree in _modules():
            if _layer_of(path) in SDK_LAYERS:
                continue
            with self.subTest(module=relative):
                self.assertEqual(_imported_packages(tree) & SDK_PACKAGES, set())

    def test_importing_core_loads_no_upper_layer(self) -> None:
        """The strongest form of the claim: check it at runtime, not in the AST.

        What counts as "upper" is derived from `ALLOWED` rather than written out
        again here. A hand-written list of layer names is a second copy of the
        architecture, and a rename or a move silently empties it — the check
        keeps passing while checking nothing.
        """

        inside = {layer for layer in ALLOWED if layer.split(".")[0] == "core"}
        for module in _modules_loaded_by("import contexture.core"):
            layer = _layer_of_module(module)
            if layer is None or layer == FACADE:
                continue  # reaching core at all goes through the facade
            with self.subTest(module=module):
                self.assertIn(
                    layer,
                    inside,
                    f"importing contexture.core pulled in {module} ({layer})",
                )

    def test_importing_core_does_not_load_the_mcp_sdk(self) -> None:
        """A project that only models context should not pay for a transport.

        Both SDK distributions are checked. An indirect import — a module that
        never names the SDK but pulls in something that does — is invisible to
        the AST test above and is exactly what this one is for, so leaving one
        of the two out would leave that path unwatched.
        """

        loaded = {
            module
            for module in _modules_loaded_by("import contexture.core")
            if module.split(".")[0] in SDK_PACKAGES
        }
        self.assertEqual(loaded, set())

    def test_the_demo_uses_only_the_public_facade(self) -> None:
        """A reference application must be copy-pasteable out of this package.

        Reaching into `contexture.core.*` would make the example unusable as a
        starting point — a copied directory cannot resolve a relative import
        beyond its own top level — and would quietly contradict the claim these
        files make about themselves.
        """

        # Two names, and no layer path among them. `Prompt` and `Resource`
        # are on the facade beside the three node kinds, so publishing a
        # document no longer costs a reader a directory name they would have
        # to decode — and an example that reached for one would be teaching
        # the decoding.
        allowed = {
            "contexture",
            "contexture.server",
        }
        for path in _source_modules(PACKAGE / "demo"):
            with self.subTest(module=str(path.relative_to(PACKAGE))):
                for imported in _contexture_imports(path):
                    if imported.startswith("."):
                        # A relative import must stay inside the example itself.
                        self.assertEqual(
                            imported.count("."),
                            1,
                            f"{path.name} reaches outside the example with "
                            f"{imported!r}; use the public facade instead.",
                        )
                        continue
                    self.assertIn(
                        imported,
                        allowed,
                        f"{path.name} imports {imported!r}; the demo may only "
                        f"use {sorted(allowed)}.",
                    )


class IOBoundaryTests(unittest.TestCase):
    """Only the scaffold may write, and only inbound adapters touch networking modules.

    One module in the whole package touches the filesystem, and it is the one
    that writes a project *for the user*. Nothing on the path from a
    declaration to the wire opens a file, which is why the boundary is worth
    checking rather than assuming.

    Checks run over the parsed tree rather than the source text, because a
    substring search cannot tell `open(` from `urlopen(`.
    """

    # One module of 106 lines, where this used to name a file of 695. The
    # split did not change the rule; it changed how much code the rule has to
    # excuse.
    FILESYSTEM_WRITERS = {"cli/scaffold.py"}
    # `web.surface` imports urllib only for standards-compliant query decoding;
    # it is itself the human-facing inbound transport and opens no connection.
    NETWORK_MODULES: set[str] = {"web/surface.py"}

    WRITE_CALLS = {"write_text", "write_bytes", "mkdir", "touch", "unlink"}
    NETWORK_PACKAGES = {"urllib", "socket", "http", "httpx", "requests", "aiohttp"}

    def test_only_the_scaffold_touches_the_filesystem(self) -> None:
        for path, relative, tree in _modules():
            if relative in self.FILESYSTEM_WRITERS:
                continue
            with self.subTest(module=relative):
                self.assertEqual(_filesystem_calls(tree), set())

    def test_only_the_transport_reaches_the_network(self) -> None:
        for path, relative, tree in _modules():
            if relative in self.NETWORK_MODULES:
                continue
            with self.subTest(module=relative):
                self.assertEqual(
                    _imported_packages(tree) & self.NETWORK_PACKAGES,
                    set(),
                )


def _modules() -> list[tuple[Path, str, ast.Module]]:
    return [
        (
            path,
            path.relative_to(PACKAGE).as_posix(),
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path)),
        )
        for path in _source_modules(PACKAGE)
    ]


def _filesystem_calls(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            found.add("open")
        elif (
            isinstance(func, ast.Attribute)
            and func.attr in IOBoundaryTests.WRITE_CALLS
        ):
            found.add(func.attr)
    return found


def _imported_packages(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def _modules_loaded_by(statement: str) -> set[str]:
    """Return every module `statement` leaves behind in a fresh interpreter."""

    script = (
        "import sys; sys.path.insert(0, %r);"
        "%s;"
        "print('\\n'.join(sorted(sys.modules)))" % (str(SOURCE_ROOT), statement)
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


class PackagingBoundaryTests(unittest.TestCase):
    """The wheel is built from the one flat module, never a stale build tree."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            import tomllib
        except ModuleNotFoundError:  # Python 3.10
            import tomli as tomllib  # type: ignore[no-redef]
        with (SOURCE_ROOT / "pyproject.toml").open("rb") as handle:
            cls.pyproject = tomllib.load(handle)
        cls.backend = cls.pyproject["tool"]["uv"]["build-backend"]

    def test_the_flat_module_is_the_only_build_input(self) -> None:
        build_system = self.pyproject["build-system"]
        self.assertEqual(build_system["build-backend"], "uv_build")
        self.assertTrue(
            any(requirement.startswith("uv_build>=") for requirement in build_system["requires"])
        )
        self.assertEqual(self.backend["module-name"], "contexture")
        self.assertEqual(self.backend["module-root"], "")

    @unittest.skipUnless(shutil.which("uv"), "wheel verification needs uv")
    def test_a_built_wheel_contains_only_current_package_files(self) -> None:
        """A real manifest catches stale `build/lib` files configuration cannot."""

        with tempfile.TemporaryDirectory(prefix="contexture-wheel-") as raw:
            output = Path(raw)
            subprocess.run(
                [
                    "uv",
                    "build",
                    "--wheel",
                    "--no-sources",
                    "--out-dir",
                    str(output),
                    str(SOURCE_ROOT),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            (wheel,) = output.glob("*.whl")
            with ZipFile(wheel) as archive:
                names = set(archive.namelist())

        package_files = {name for name in names if name.startswith("contexture/")}
        self.assertTrue(package_files)
        self.assertTrue(
            any(name.startswith("contexture/cli/templates/") for name in package_files)
        )
        self.assertIn("contexture/__init__.pyi", package_files)
        self.assertIn("contexture/server/__init__.pyi", package_files)
        self.assertIn("contexture/py.typed", package_files)
        self.assertNotIn("contexture/cli.py", package_files)
        self.assertNotIn("contexture/tree.py", package_files)
        self.assertFalse(any("__pycache__" in name for name in package_files))

    @unittest.skipUnless(shutil.which("uv"), "isolated install verification needs uv")
    def test_a_wheel_runs_the_new_user_path_outside_the_checkout(self) -> None:
        """The installed artifact, not this checkout, must scaffold and run."""

        with tempfile.TemporaryDirectory(prefix="contexture-dist-") as dist_raw:
            dist = Path(dist_raw)
            subprocess.run(
                [
                    "uv",
                    "build",
                    "--wheel",
                    "--no-sources",
                    "--out-dir",
                    str(dist),
                    str(SOURCE_ROOT),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            (wheel,) = dist.glob("*.whl")
            with tempfile.TemporaryDirectory(prefix="contexture-user-") as user_raw:
                user = Path(user_raw)
                command = ["uv", "run", "--no-project", "--with", str(wheel), "contexture"]
                subprocess.run(
                    [*command, "new", "hello-context"],
                    cwd=user,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                project = user / "hello-context"
                checked = subprocess.run(
                    [*command, "check"],
                    cwd=project,
                    check=True,
                    capture_output=True,
                    text=True,
                )

        self.assertIn("OK hello-context", checked.stdout)
