"""Error types the MORPH engine raises.

Every error carries a short, human-readable message safe to show in the dashboard
and to hand back to the model.  ``code`` is stable and machine-checkable; HTTP
status mapping lives in the dashboard API, not here.
"""
from __future__ import annotations


class MorphError(Exception):
    """Base class. ``code`` is part of the tool/API contract."""

    code = "morph_error"
    status = 400

    def __init__(self, message: str, *, detail: object | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def payload(self) -> dict[str, object]:
        out: dict[str, object] = {"code": self.code, "error": self.message}
        if self.detail is not None:
            out["detail"] = self.detail
        return out


class ConfigError(MorphError):
    code = "config_invalid"
    status = 500


class ConnectorError(MorphError):
    """A connected application could not be reached or answered unusably."""

    code = "connector_unavailable"
    status = 502


class SchemaError(MorphError):
    """A declarative UI schema failed validation."""

    code = "schema_invalid"
    status = 422


class PermissionDenied(MorphError):
    code = "permission_denied"
    status = 403


class ApprovalRequired(MorphError):
    code = "approval_required"
    status = 403


class StaleRecord(MorphError):
    """The source record moved under us; the proposal is void."""

    code = "stale_record"
    status = 409


class NotFound(MorphError):
    code = "not_found"
    status = 404


class Conflict(MorphError):
    code = "conflict"
    status = 409


class VerificationFailed(MorphError):
    """The write was accepted but the authoritative read-back disagreed."""

    code = "verification_failed"
    status = 500


class RateLimited(MorphError):
    code = "rate_limited"
    status = 429
