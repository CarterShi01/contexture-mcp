"""Who is calling: the fact, and the trip it makes across the SDK.

Two claims are worth a test here and the rest is plumbing.

The first is that a `Principal` cannot be edited by the code it is handed to.
A caller's identity that a capability can append a scope to is not an identity,
and nothing about the mistake would look wrong at the call site.

The second is that identity **survives the round trip**. A verifier returns a
`Principal`, the SDK stores an `AccessToken`, and the gateway gets that back —
so anything the two types do not both carry is silently lost between a business
writing it and a business reading it. The test walks the whole path rather than
either half, because each half alone passes while the pair drops a field.
"""

from __future__ import annotations

import asyncio
import unittest

from contexture import Principal, current_principal
from contexture.core.errors import ModelValidationError
from contexture.core.principal import bound
from contexture.server.identity import Auth, principal_of


class Verifier:
    """The shape a business implements: one method, one return type."""

    def __init__(self, principal: Principal | None) -> None:
        self.principal = principal

    async def verify(self, token: str) -> Principal | None:
        return self.principal if token == "good" else None


def _auth(principal: Principal | None) -> Auth:
    return Auth(
        verifier=Verifier(principal),
        issuer="https://idp.example",
        resource="https://mcp.example/mcp",
    )


class PrincipalTests(unittest.TestCase):
    def test_a_caller_cannot_be_edited_by_the_code_it_is_handed_to(self) -> None:
        """Identity is a fact about the request, not a variable."""

        who = Principal(subject="alice", scopes={"read"}, claims={"tid": "acme"})

        with self.assertRaises(Exception):
            who.subject = "bob"  # type: ignore[misc]
        self.assertIsInstance(who.scopes, frozenset)
        with self.assertRaises(TypeError):
            who.claims["tid"] = "evil"  # type: ignore[index]

    def test_what_a_verifier_finds_convenient_is_normalised_once(self) -> None:
        """A list of scopes and a plain dict are accepted, and neither escapes."""

        scopes = ["read", "write", "read"]
        claims = {"tid": "acme"}
        who = Principal(scopes=scopes, claims=claims)

        scopes.append("admin")
        claims["tid"] = "changed"

        self.assertEqual(who.scopes, frozenset({"read", "write"}))
        self.assertEqual(who.claims["tid"], "acme")

    def test_the_repr_does_not_print_the_token_claims(self) -> None:
        """A Principal reaches log records; a decoded token should not."""

        who = Principal(subject="alice", claims={"ssn": "000-00-0000"})

        self.assertNotIn("000-00-0000", repr(who))
        self.assertIn("alice", repr(who))


class BindingTests(unittest.TestCase):
    def test_nobody_is_the_default_and_it_is_restored(self) -> None:
        """A worker that outlives a request must not inherit its caller."""

        who = Principal(subject="alice")

        self.assertIsNone(current_principal())
        with bound(who):
            self.assertIs(current_principal(), who)
            with bound(Principal(subject="bob")):
                self.assertEqual(current_principal().subject, "bob")
            self.assertIs(current_principal(), who)
        self.assertIsNone(current_principal())

    def test_concurrent_callers_do_not_see_each_other(self) -> None:
        """The reason this is a context variable and not a module global."""

        async def serve(name: str) -> str:
            with bound(Principal(subject=name)):
                await asyncio.sleep(0)
                return current_principal().subject

        async def main() -> list[str]:
            return await asyncio.gather(*(serve(f"user{i}") for i in range(20)))

        self.assertEqual(asyncio.run(main()), [f"user{i}" for i in range(20)])


class RoundTripTests(unittest.TestCase):
    """A verifier writes a Principal; the gateway reads one back."""

    def _round_trip(self, principal: Principal) -> Principal:
        verifier = _auth(principal).sdk_verifier()
        token = asyncio.run(verifier.verify_token("good"))
        recovered = principal_of(token)
        assert recovered is not None
        return recovered

    def test_every_field_survives_the_sdk(self) -> None:
        original = Principal(
            subject="alice",
            client_id="claude-code",
            issuer="https://idp.example",
            scopes={"ctx.read", "k8s.write"},
            claims={"tid": "acme", "groups": ["sre"]},
        )

        recovered = self._round_trip(original)

        self.assertEqual(recovered.subject, original.subject)
        self.assertEqual(recovered.client_id, original.client_id)
        self.assertEqual(recovered.issuer, original.issuer)
        self.assertEqual(recovered.scopes, original.scopes)
        self.assertEqual(recovered.claims["tid"], "acme")
        self.assertEqual(recovered.claims["groups"], ["sre"])

    def test_a_machine_token_with_no_person_stays_that_way(self) -> None:
        """Absent is absent — never the string 'None' arriving as a client id."""

        recovered = self._round_trip(Principal(client_id=None, scopes={"a"}))

        self.assertIsNone(recovered.client_id)
        self.assertIsNone(recovered.subject)

    def test_an_issuer_the_verifier_stated_itself_is_left_alone(self) -> None:
        """`iss` travels in claims, where a real token already keeps it."""

        recovered = self._round_trip(
            Principal(issuer="https://a.example", claims={"iss": "https://b.example"})
        )

        self.assertEqual(recovered.issuer, "https://b.example")

    def test_a_rejected_token_is_nobody(self) -> None:
        verifier = _auth(Principal(subject="alice")).sdk_verifier()

        self.assertIsNone(asyncio.run(verifier.verify_token("wrong")))
        self.assertIsNone(principal_of(None))


class AuthTests(unittest.TestCase):
    def test_something_that_is_not_a_verifier_is_refused_at_startup(self) -> None:
        """Naming the method, because the mistake is always the same one."""

        with self.assertRaises(ModelValidationError) as caught:
            Auth(verifier=object(), issuer="https://i", resource="https://r")  # type: ignore[arg-type]

        self.assertIn("verify", str(caught.exception))

    def test_the_two_published_urls_may_not_be_empty(self) -> None:
        for field in ("issuer", "resource"):
            with self.subTest(field=field):
                kwargs = {"issuer": "https://i", "resource": "https://r"}
                kwargs[field] = "   "
                with self.assertRaises(ModelValidationError):
                    Auth(verifier=Verifier(None), **kwargs)  # type: ignore[arg-type]

    def test_a_url_the_sdk_cannot_use_fails_here_rather_than_on_the_wire(self) -> None:
        with self.assertRaises(ModelValidationError) as caught:
            Auth(
                verifier=Verifier(None), issuer="not-a-url", resource="https://r"
            ).settings()

        self.assertIn("absolute http(s) URLs", str(caught.exception))

    def test_the_settings_carry_what_a_client_needs_to_find_the_issuer(self) -> None:
        settings = Auth(
            verifier=Verifier(None),
            issuer="https://idp.example",
            resource="https://mcp.example/mcp",
            required_scopes=("ctx.read",),
        ).settings()

        self.assertEqual(str(settings.issuer_url), "https://idp.example")
        self.assertEqual(str(settings.resource_server_url), "https://mcp.example/mcp")
        self.assertEqual(settings.required_scopes, ["ctx.read"])


if __name__ == "__main__":
    unittest.main()
