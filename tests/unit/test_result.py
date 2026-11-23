"""Unit tests for the generic Result wrapper model."""

import pytest
from pydantic import BaseModel, ValidationError

from gosmart_graphite.model import (
    STATUS_BAD_DATA,
    STATUS_BAD_REQUEST,
    STATUS_INTERNAL_ERROR,
    STATUS_OK,
    Result,
)


class _SamplePayload(BaseModel):
    name: str
    count: int


def test_result_status_constants():
    """Verify standard status code constants."""
    assert STATUS_OK == 200
    assert STATUS_BAD_REQUEST == 400
    assert STATUS_BAD_DATA == 401
    assert STATUS_INTERNAL_ERROR == 500


def test_result_defaults():
    """Verify default status, message, and None value for both unparameterized and typed Result."""
    untyped = Result()
    assert untyped.status == 0
    assert untyped.message == "Please set a proper result"
    assert untyped.value is None

    typed = Result[int]()
    assert typed.status == 0
    assert typed.message == "Please set a proper result"
    assert typed.value is None


def test_result_explicit_none_and_validation():
    """Verify Result[T] accepts explicit None and validates typed payloads."""
    res_none = Result[int](value=None)
    assert res_none.value is None

    res_validated_none = Result[int].model_validate({"value": None})
    assert res_validated_none.value is None

    res_ok = Result[int](status=STATUS_OK, message="OK", value=42)
    assert res_ok.status == STATUS_OK
    assert res_ok.message == "OK"
    assert res_ok.value == 42

    with pytest.raises(ValidationError):
        Result[int](value="not-an-int")  # type: ignore[arg-type]


def test_result_nested_model_and_json_roundtrip():
    """Verify Result[BaseModel] validation and JSON serialization round-trips."""
    payload = _SamplePayload(name="study-1", count=3)
    res = Result[_SamplePayload](status=STATUS_OK, message="OK", value=payload)

    dumped = res.model_dump_json()
    restored = Result[_SamplePayload].model_validate_json(dumped)
    assert restored == res

    empty = Result[_SamplePayload](status=STATUS_INTERNAL_ERROR, message="Internal Error")
    restored_empty = Result[_SamplePayload].model_validate_json(empty.model_dump_json())
    assert restored_empty == empty
    assert restored_empty.value is None
