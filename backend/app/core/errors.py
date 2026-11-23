"""Domain errors raised by the service layer.

Services raise these; the API maps them to HTTP responses (see main.py) and the
Celery tasks log them.
"""
from __future__ import annotations


class TabulaError(Exception):
    status_code = 400

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NotFound(TabulaError):
    status_code = 404


class Forbidden(TabulaError):
    status_code = 403


class Conflict(TabulaError):
    status_code = 409


class Invalid(TabulaError):
    status_code = 422


class TooLarge(TabulaError):
    status_code = 413
