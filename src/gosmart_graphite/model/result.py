"""Generic result wrapper model for service and actor responses."""

from typing import Optional

from pydantic import BaseModel, Field

STATUS_OK = 200
STATUS_BAD_REQUEST = 400
STATUS_BAD_DATA = 401
STATUS_INTERNAL_ERROR = 500


class Result[T](BaseModel):
    """Generic envelope representing an operation status, message, and optional payload."""

    status: int = Field(default=0, description="Result status code")
    message: str = Field(default="Please set a proper result", description="Human-readable status message")
    value: Optional[T] = Field(default=None, description="Optional result payload")
