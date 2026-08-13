import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.routers import health
from app.services.errors import DomainError

API_PREFIX = "/api/v1"

settings = get_settings()
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Penny Pulse API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix=API_PREFIX)


def error_response(
    status_code: int, code: str, message: str, field: str | None = None
) -> JSONResponse:
    body: dict[str, str] = {"code": code, "message": message}
    if field is not None:
        body["field"] = field
    return JSONResponse(status_code=status_code, content={"error": body})


@app.exception_handler(DomainError)
def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, exc.field)


@app.exception_handler(RequestValidationError)
def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0]
    # loc is ("body", "amount_minor"); the client wants the field, not the location.
    field = str(first["loc"][-1]) if first.get("loc") else None
    return error_response(422, "validation_failed", first["msg"], field)


@app.exception_handler(StarletteHTTPException)
def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    codes = {401: "unauthenticated", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
    code = codes.get(exc.status_code, "http_error")
    return error_response(exc.status_code, code, str(exc.detail))


@app.exception_handler(Exception)
def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """The trace goes to the log, the id goes to the client, and neither carries data.

    Four real salaries are in this database — no amount, note, merchant or category
    name is ever logged.
    """
    error_id = uuid4()
    logger.exception("unhandled error id=%s path=%s", error_id, request.url.path)
    return error_response(500, "internal_error", f"Unexpected error. Reference {error_id}.")
