from __future__ import annotations

from flask import jsonify


class ApiError(Exception):
    def __init__(self, status_code: int, error: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message


def error_response(status_code: int, error: str, message: str):
    return (
        jsonify(
            {
                "error": error,
                "message": message,
                "status": status_code,
            }
        ),
        status_code,
    )
