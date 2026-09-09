"""Positive and negative authoring contracts, including precise slot types.

An unnecessary ignore is an error: these fixtures fail if an invalid argument
starts being accepted (for example when a facade accidentally erases a type).
"""

# pyright: reportUnnecessaryTypeIgnoreComment=true

from typing import assert_type

from contexture import PostProcess, PreProcess, Role, binding_instruction
from contexture.core import (
    binding_instruction as core_binding_instruction,
)
from contexture.core.model import PostProcess as ModelPostProcess, PreProcess as ModelPreProcess


pre = PreProcess(name="prepare", description="Prepare.", instructions="Prepare.")
post = PostProcess(name="finish", description="Finish.", instructions="Finish.")
owner = Role(
    name="owner", description="Work.", instructions="Work.",
    pre_process=pre, post_process=post,
)
assert_type(owner.pre_process, PreProcess | None)
assert_type(owner.post_process, PostProcess | None)
assert_type(ModelPreProcess(name="pre", description="Pre.", instructions="Pre."), PreProcess)
assert_type(ModelPostProcess(name="post", description="Post.", instructions="Post."), PostProcess)
assert_type(binding_instruction("policy", "Body.", action="Act."), str)
assert_type(core_binding_instruction("policy", "Body."), str)

Role(
    name="owner", description="Work.", instructions="Work.",
    pre_process=post,  # pyright: ignore[reportArgumentType]
    post_process=pre,  # pyright: ignore[reportArgumentType]
)
Role(
    name="owner", description="Work.", instructions="Work.",
    pre_process=owner,  # pyright: ignore[reportArgumentType]
    post_process=owner,  # pyright: ignore[reportArgumentType]
)
Role(
    name="owner", description="Work.", instructions="Work.",
    pre_process=PreProcess,  # pyright: ignore[reportArgumentType]
    post_process=PostProcess,  # pyright: ignore[reportArgumentType]
)
Role(
    name="owner", description="Work.", instructions="Work.",
    publication=post,  # pyright: ignore[reportCallIssue]
)
binding_instruction(42, "Body.")  # pyright: ignore[reportArgumentType]
binding_instruction("policy", 42)  # pyright: ignore[reportArgumentType]
binding_instruction("policy", "Body.", action=42)  # pyright: ignore[reportArgumentType]