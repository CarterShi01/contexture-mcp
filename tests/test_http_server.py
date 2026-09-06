"""The server over Streamable HTTP, launched as a real process.

`test_stdio_server.py` covers the transport a host launches as a subprocess.
This covers the one it connects to over a network, and the three things that
are only true there:

* a **client that has not upgraded** still works. The 2026-07-28 revision has
  no `initialize` handshake, but hosts speaking the older revisions are what is
  deployed today, and a server that answers only the newest one drops them
  silently. Both doors are opened here, against one running process.
* **statelessness is real**, not merely configured. Every request is answered
  without a session id, which is what lets two replicas sit behind one address.
* **identity reaches a capability's own code**, and two callers who differ only
  in a scope get two different answers.

Each test pays for a process because none of it is observable from inside one:
an in-process check would exercise the gateway while skipping the transport,
the auth middleware, and the round trip that identity actually makes.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

try:
    import httpx2
    from mcp import ClientSession, types
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared.exceptions import MCPError
except ImportError:  # pragma: no cover - the SDK is a hard dependency
    ClientSession = None  # type: ignore[assignment]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = PROJECT_ROOT / "src"

TIMEOUT_SECONDS = 30
STARTUP_SECONDS = 20


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _Server:
    """One `tests/http_fixture.py` process, started and stopped."""

    def __init__(self, *, secured: bool) -> None:
        self.port = _free_port()
        self.secured = secured
        self.process: subprocess.Popen[bytes] | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def __enter__(self) -> "_Server":
        environment = dict(os.environ)
        existing = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(SOURCE_ROOT), str(PROJECT_ROOT), existing) if part
        )
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.http_fixture",
                str(self.port),
                "secured" if self.secured else "open",
            ],
            cwd=str(PROJECT_ROOT),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._await_ready()
        return self

    def _await_ready(self) -> None:
        """Wait for the port to answer, rather than sleeping a guessed amount."""

        deadline = time.monotonic() + STARTUP_SECONDS
        while time.monotonic() < deadline:
            assert self.process is not None
            if self.process.poll() is not None:
                stderr = (self.process.stderr.read() or b"").decode()
                raise RuntimeError(f"fixture exited during startup:\n{stderr}")
            try:
                # Any answer means the socket is up. Unauthenticated is one.
                urllib.request.urlopen(self.url, data=b"{}", timeout=1)
            except urllib.error.HTTPError:
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError(f"fixture did not start within {STARTUP_SECONDS}s")

    def __exit__(self, *_: object) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            self.process.kill()
        if self.process.stderr is not None:
            self.process.stderr.close()


def _run(
    server: _Server,
    work,
    *,
    token: str | None = None,
    modern: bool = True,
    roots: str | None = None,
    select: str | None = None,
):
    """Open one session against `server` and run `work` in it."""

    async def session():
        headers = {
            **({"Authorization": f"Bearer {token}"} if token else {}),
            **({"Contexture-Roots": roots} if roots is not None else {}),
            **({"Contexture-Select": select} if select is not None else {}),
        }
        async with httpx2.AsyncClient(headers=headers) as client:
            async with streamable_http_client(
                server.url, http_client=client
            ) as streams:
                async with ClientSession(streams[0], streams[1]) as opened:
                    if modern:
                        await opened.discover()
                    else:
                        await opened.initialize()
                    return await work(opened)

    return asyncio.run(asyncio.wait_for(session(), TIMEOUT_SECONDS))


def _text(result) -> str:
    return "".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


@unittest.skipIf(ClientSession is None, "the MCP SDK is not installed")
class TransportTests(unittest.TestCase):
    """One process, opened through both eras of the protocol."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._server = _Server(secured=False)
        cls._server.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._server.__exit__()

    def test_the_newest_revision_reaches_the_gateway(self) -> None:
        async def work(session):
            return session.protocol_version, await session.list_tools()

        version, tools = _run(self._server, work)

        self.assertEqual(version, "2026-07-28")
        self.assertIn("contexture_discover", {tool.name for tool in tools.tools})

    def test_a_host_that_has_not_upgraded_still_gets_a_working_server(self) -> None:
        """The handshake revisions are what is deployed today."""

        async def work(session):
            return session.protocol_version, await session.list_tools()

        version, tools = _run(self._server, work, modern=False)

        self.assertNotEqual(version, "2026-07-28")
        self.assertIn("contexture_open", {tool.name for tool in tools.tools})

    def test_navigating_and_running_a_tool_works_over_http(self) -> None:
        async def work(session):
            opened = await session.call_tool("contexture_open", {"ref": "ops"})
            ran = await session.call_tool(
                "contexture_invoke_read_only", {"ref": "ops/whoami"}
            )
            return _text(opened), _text(ran)

        opened, ran = _run(self._server, work)

        self.assertIn("whoami", opened)
        # Nobody authenticated this server, so nobody is who it reports.
        self.assertIn("anonymous", ran)

    def test_one_header_filters_discovery_instructions_and_direct_access(self) -> None:
        async def work(session):
            discovered = await session.call_tool("contexture_discover", {})
            refused = await session.call_tool("contexture_open", {"ref": "audit"})
            prompts = await session.list_prompts()
            resources = await session.list_resources()
            completion = await session.complete(
                types.PromptReference(name="goto"),
                {"name": "ref", "value": ""},
            )
            prompt_refused = resource_refused = False
            try:
                await session.get_prompt("open-audit")
            except MCPError:
                prompt_refused = True
            try:
                await session.read_resource("contexture://audit/read")
            except MCPError:
                resource_refused = True
            return (
                session.discover_result.instructions,
                _text(discovered),
                refused,
                {prompt.name for prompt in prompts.prompts},
                {str(resource.uri) for resource in resources.resources},
                completion.completion.values,
                prompt_refused,
                resource_refused,
            )

        (
            instructions,
            discovered,
            refused,
            prompts,
            resources,
            completions,
            prompt_refused,
            resource_refused,
        ) = _run(self._server, work, roots="ops")

        self.assertIn("ops", instructions)
        self.assertNotIn("audit", instructions)
        self.assertIn("ops", discovered)
        self.assertNotIn("audit", discovered)
        self.assertTrue(refused.is_error)
        self.assertIn("outside this request's selected surface", _text(refused))
        self.assertIn("open-ops", prompts)
        self.assertNotIn("open-audit", prompts)
        self.assertIn("contexture://ops/whoami", resources)
        self.assertNotIn("contexture://audit/read", resources)
        self.assertTrue(
            all(value == "ops" or value.startswith("ops/") for value in completions)
        )
        self.assertTrue(prompt_refused)
        self.assertTrue(resource_refused)

    def test_deep_selector_projects_one_capability_across_every_protocol_door(self) -> None:
        async def work(session):
            discovered = await session.call_tool("contexture_discover", {})
            opened = await session.call_tool(
                "contexture_open", {"ref": "ops/whoami"}
            )
            ran = await session.call_tool(
                "contexture_invoke_read_only", {"ref": "ops/whoami"}
            )
            parent = await session.call_tool("contexture_open", {"ref": "ops"})
            prompts = await session.list_prompts()
            resources = await session.list_resources()
            completion = await session.complete(
                types.PromptReference(name="goto"),
                {"name": "ref", "value": ""},
            )
            return (
                session.discover_result.instructions,
                _text(discovered),
                _text(opened),
                _text(ran),
                parent,
                {prompt.name for prompt in prompts.prompts},
                {str(resource.uri) for resource in resources.resources},
                completion.completion.values,
            )

        (
            instructions,
            discovered,
            opened,
            ran,
            parent,
            prompts,
            resources,
            completions,
        ) = _run(self._server, work, select="ops/whoami")

        for payload in (instructions, discovered, opened):
            self.assertIn("ops/whoami", payload)
            self.assertNotIn("roll_back", payload)
            self.assertNotIn("audit", payload)
        self.assertIn("anonymous", ran)
        self.assertTrue(parent.is_error)
        self.assertEqual(prompts, {"goto"})
        self.assertEqual(resources, {"contexture://ops/whoami"})
        self.assertEqual(completions, ["ops/whoami"])

    def test_every_request_is_answered_without_a_session(self) -> None:
        """What lets two replicas sit behind one address.

        Each call here opens its own connection, and none of them carries an
        `Mcp-Session-Id` — so the second could have been answered by a process
        that never saw the first.
        """

        async def work(session):
            return _text(
                await session.call_tool(
                    "contexture_invoke_read_only", {"ref": "ops/whoami"}
                )
            )

        first = _run(self._server, work)
        second = _run(self._server, work)

        self.assertEqual(first, second)


@unittest.skipIf(ClientSession is None, "the MCP SDK is not installed")
class AuthenticatedTests(unittest.TestCase):
    """The same server, with the business's verifier plugged in."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._server = _Server(secured=True)
        cls._server.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._server.__exit__()

    def _status(self, token: str | None) -> int:
        request = urllib.request.Request(
            self._server.url,
            data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **({"Authorization": f"Bearer {token}"} if token else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return int(response.status)
        except urllib.error.HTTPError as exc:
            # Kept as the message object: header lookup there is
            # case-insensitive, and the server sends this one lower-cased.
            self._last_headers = exc.headers
            return int(exc.code)

    def test_no_token_is_refused_with_the_pointer_to_get_one(self) -> None:
        """RFC 9728: the 401 has to say where the authorization server is."""

        self.assertEqual(self._status(None), 401)
        challenge = self._last_headers.get("WWW-Authenticate", "")
        self.assertIn("resource_metadata=", challenge)

    def test_a_token_the_business_rejects_is_refused(self) -> None:
        self.assertEqual(self._status("forged"), 401)

    def test_the_metadata_names_the_issuer_the_business_stated(self) -> None:
        base = self._server.url.rsplit("/mcp", 1)[0]
        with urllib.request.urlopen(
            f"{base}/.well-known/oauth-protected-resource/mcp", timeout=10
        ) as response:
            body = response.read().decode()

        self.assertIn("https://idp.example", body)

    def test_identity_reaches_the_capabilitys_own_code(self) -> None:
        """The whole point: a verifier's Principal, read inside `invoke`."""

        async def work(session):
            return _text(
                await session.call_tool(
                    "contexture_invoke_read_only", {"ref": "ops/whoami"}
                )
            )

        reported = _run(self._server, work, token="reader")

        subject, client_id, issuer, scopes, tenant = reported.split("|")
        self.assertEqual(subject, "alice")
        self.assertEqual(client_id, "claude-code")
        self.assertEqual(issuer, "https://idp.example")
        self.assertEqual(scopes, "ctx.read")
        # A claim this framework has no field for still arrives intact.
        self.assertEqual(tenant, "acme")

    def test_two_callers_differing_by_one_scope_get_two_answers(self) -> None:
        """The refusal is the business's, and it reads as a tool result.

        Not a transport error: a model that loses the connection cannot try
        something else, and a model that reads a refusal can.
        """

        async def work(session):
            result = await session.call_tool(
                "contexture_invoke", {"ref": "ops/roll_back", "arguments": {"deployment": "api"}}
            )
            return result.is_error, _text(result)

        refused, refusal = _run(self._server, work, token="reader")
        allowed, answer = _run(self._server, work, token="writer")

        self.assertTrue(refused)
        self.assertIn("lacks k8s.write", refusal)
        self.assertFalse(allowed)
        self.assertIn("rolled back api for bob", answer)


if __name__ == "__main__":
    unittest.main()
