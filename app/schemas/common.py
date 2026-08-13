from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    field: str | None = None


class ErrorResponse(BaseModel):
    """The one error shape this API returns, for every status, without exception.

    `code` is stable and machine-readable — the client branches on it. `message` is for
    logs; the client owns its own wording.
    """

    error: ErrorBody
