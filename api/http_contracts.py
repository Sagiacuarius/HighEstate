"""Framework-level response translation shared by API boundaries."""

from fastapi.responses import JSONResponse


def envelope(status_code: int, code: str, message: str, correlation: str) -> JSONResponse:
    response = JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "correlation_id": correlation}},
    )
    response.headers["X-Correlation-Id"] = correlation
    return response
