"""Creation policy is author metadata, not a legacy disclosure field."""

import pytest

from contexture import Role, Skill, Tool
from contexture.core.errors import ModelValidationError
from contexture.core.model import ExplicitSchemaTool


@pytest.mark.parametrize("kind", [Role, Skill, Tool])
@pytest.mark.parametrize("policy", ["inherit", True, False])
def test_node_creation_policy_is_explicit_and_legacy_cards_do_not_change(kind, policy):
    kwargs = {"name": "node", "description": "Node", "mutable": policy}
    if kind is not Tool:
        kwargs["instructions"] = "program"
    node = kind(**kwargs)
    assert node.mutable == policy
    assert node.compile("route") == {"kind": kind.kind, "name": "node", "description": "Node"}


@pytest.mark.parametrize("policy", [None, 0, 1, [], {}, "true", "false", "Inherit"])
def test_invalid_creation_policy_is_not_coerced(policy):
    with pytest.raises(ModelValidationError, match="mutable"):
        Role(name="root", description="Root", instructions="program", mutable=policy)


@pytest.mark.parametrize("policy", ["inherit", True, False])
def test_explicit_schema_tool_forwards_creation_policy(policy):
    node = ExplicitSchemaTool(name="proxy", description="Proxy", input_schema={"type": "object"},
                              mutable=policy)
    assert node.mutable == policy
