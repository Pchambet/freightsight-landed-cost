"""Domain exceptions mapped to RFC 9457 problem+json by the API layer."""

from __future__ import annotations


class DomainError(Exception):
    status = 400
    code = "DOMAIN_ERROR"

    def __init__(self, message: str, *, errors: list[dict[str, str]] | None = None, **extra: object):
        super().__init__(message)
        self.message = message
        self.errors = errors
        # `code="..."` overrides the class default on this instance rather than travelling as an
        # extra field: callers pass it to say what went wrong, and readers expect `exc.code` to say it.
        code = extra.pop("code", None)
        if isinstance(code, str):
            self.code = code
        self.extra = extra


class NotFound(DomainError):
    status = 404
    code = "NOT_FOUND"

    def __init__(self, what: str, *, code: str | None = None):
        super().__init__(f"{what} not found")
        if code is not None:
            self.code = code


class Conflict(DomainError):
    status = 409
    code = "CONFLICT"


class TooManyRequests(DomainError):
    status = 429
    code = "RATE_LIMITED"


class Unprocessable(DomainError):
    status = 422
    code = "UNPROCESSABLE"


class TenantViolation(DomainError):
    status = 403
    code = "TENANT_VIOLATION"


class Unauthorized(DomainError):
    status = 401
    code = "UNAUTHORIZED"


class Forbidden(DomainError):
    status = 403
    code = "FORBIDDEN"
