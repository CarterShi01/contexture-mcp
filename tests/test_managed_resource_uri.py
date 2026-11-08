"""Resource URI syntax is validated without transport imports or resolution."""

from __future__ import annotations

import pytest

from contexture.core.graph.errors import GraphError
from contexture.core.graph.model import _publication


def resource(uri: str) -> object:
    return _publication({
        "kind": "resource", "publication_id": "resource", "name": "resource",
        "uri": uri, "mime_type": "application/json", "opens_id": "tool",
    })


@pytest.mark.parametrize("uri", [
    "relative/path", "//host/path", ":path", "1app:data", "a_b:data", "é:data",
    " app:data", "app:data ", "app:\tdata", "app:\ndata", "app:\rdata",
    "app:\x00data", "app:\x1fdata", "app:\u0085data", "app:\u00a0data",
    "app://[::1", "app://::1]", "app://[not-an-ip]", "app://[127.0.0.1]",
    "app://[1::2::3]", "app://prefix[::1]", "app://[::1]suffix",
    "app://[v1.]", "app://[vG.address]", "app://[::1%]",
    "app://host\uff0fpath", "app://host\uff1aport", "app://user\uff20host",
    "app://host\uff1fquery", "app://host\uff03fragment", "app://host\u2100name",
])
def test_malformed_resource_uri_is_rejected(uri: str) -> None:
    with pytest.raises(GraphError) as error:
        resource(uri)
    assert error.value.code == "schema_invalid"


@pytest.mark.parametrize("uri", [
    "app://data", "URN:example:item", "a+1.-:opaque", "app:", "file:///data",
    "app://", "app://user:password@host:123/path?query#fragment",
    "app://[::1]:123/data", "app://[2001:db8::1]", "app://[::ffff:192.0.2.1]",
    "app://[fe80::1%25eth0]", "app://[v1.future]", "app://[VF.future]:port",
    "app://é.example/path", "app://host/path\uff0fpart", "app:opaque[brackets",
    # Split-only validation never validated ports or percent escapes. Do not
    # silently tighten the authoring contract while removing a transport API.
    "app://host:not-a-port", "app://host:99999", "app:percent%zz", "app://host/%20",
])
def test_absolute_resource_uri_is_preserved(uri: str) -> None:
    publication = resource(uri)
    assert getattr(publication, "uri") == uri