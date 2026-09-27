"""
error_handler.py
-----------------
Centralized exception handling so every error the API returns - whether
it's a deliberate HTTPException raised by a route, a Pydantic validation
failure, or a genuinely unexpected bug - comes back as a consistent JSON
shape instead of an ad hoc one defined per-route or, worse, a raw stack
trace leaking to the client.

Registered onto the FastAPI app in app.main via register_exception_handlers().
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging_config import get_logger

logger = get_logger("app.errors")


def _error_body(message: str, detail=None, status_code: int = 500) -> dict:
    body = {"error": True, "status_code": status_code, "message": message}
    if detail is not None:
        body["detail"] = detail
    return body


def register_exception_handlers(app: FastAPI) -> None:

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(message=str(exc.detail), status_code=exc.status_code),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=_error_body(
                message="Request validation failed",
                detail=exc.errors(),
                status_code=422,
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unhandled_exception(request: Request, exc: Exception):
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=_error_body(
                message="An unexpected error occurred. Please try again.",
                status_code=500,
            ),
        )
