"""Package constants: the vocabulary that is fixed before anything is declared.

The package version lives here rather than in the root `__init__` so that any
layer can report it without importing the facade above itself.

The separator and the four entry-point names are here for a related reason.
Both are held by three layers that may not import each other — `core.model`
spells a reference and answers a call, `core.mcp_interface` declares which
names occupy the tool primitive, and `server` writes the sentences an agent
reads when one of those calls fails. Shared ground is what lets all three name
the same thing without one of them importing another, and without a second
copy of the list going quietly out of date.
"""

PACKAGE_NAME = "contexture"
PACKAGE_VERSION = "0.7.0"

#: Separates one segment of a reference from the next.
#:
#: A reference is a path, and this is how it is spelled. It sits on the shared
#: ground rather than beside the tree because a ref *string* is what every
#: layer above the object model holds: a `Prompt` names one, a `Resource` names
#: one, and a failed lookup has to say which segment of one went wrong.
SEPARATOR = "/"

#: The four entry points, and the whole of what this server puts on MCP's tool
#: primitive whatever a declaration contains. Business capabilities travel
#: inside payloads; see `core.model.system_api`.
DISCOVER_TOOL = "contexture_discover"
OPEN_TOOL = "contexture_open"
INVOKE_READ_ONLY_TOOL = "contexture_invoke_read_only"
INVOKE_TOOL = "contexture_invoke"
