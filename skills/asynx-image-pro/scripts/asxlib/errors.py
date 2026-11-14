from __future__ import annotations

from typing import Any


class AsxError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "general_error",
        exit_code: int = 1,
        details: dict[str, Any] | None = None,
        task_id: str | None = None,
        http_status: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.exit_code = exit_code
        self.details = details or {}
        self.task_id = task_id
        self.http_status = http_status
        self.request_id = request_id

    def payload(self) -> dict[str, Any]:
        error_dict: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            error_dict["details"] = self.details
        if self.task_id:
            error_dict["task_id"] = self.task_id
        if self.request_id:
            error_dict["request_id"] = self.request_id
        return {"ok": False, "error": error_dict}
