from typing import Any

from pydantic import BaseModel


class ErrorBody(BaseModel):
    """`details` carries the structured payload a few conflicts cannot express otherwise.

    It was left out originally on purpose — `.claude/rules/api.md` said the envelope was
    `code`, `message`, `field`, "no exceptions", and `VersionConflictError` still declines
    to use it: a client that loses a version race can re-read the resource, so echoing it
    would be a second way to say the same thing.

    Feature 6.2 is the case that cannot be answered that way. A near-duplicate 409 has to
    name *which* categories it matched, with their ids, so the sheet can offer "Use Food".
    Without that the client would have to re-run the similarity rule locally to find out —
    reimplementing a backend rule in a component, which is the failure mode the frontend's
    CLAUDE.md names outright.

    So: still one envelope, still `code` first. `details` is optional, omitted from the
    JSON entirely when absent, and exists for structured facts about a conflict — never as
    a second place to put the message.
    """

    code: str
    message: str
    field: str | None = None
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """The one error shape this API returns, for every status, without exception.

    `code` is stable and machine-readable — the client branches on it. `message` is for
    logs; the client owns its own wording.
    """

    error: ErrorBody
