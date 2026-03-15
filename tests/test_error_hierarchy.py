"""Tests for foundation/error.py — error taxonomy and HTTP status mapping."""

from mut.foundation.error import (
    MutError,
    NotARepoError,
    SnapshotNotFoundError,
    ObjectNotFoundError,
    PermissionDenied,
    AuthenticationError,
    ConflictError,
    LockError,
    DirtyWorkdirError,
    NetworkError,
    PayloadTooLargeError,
    ValidationError,
)


class TestErrorHierarchy:
    def test_all_are_mut_error(self):
        errors = [
            NotARepoError, SnapshotNotFoundError, ObjectNotFoundError,
            PermissionDenied, AuthenticationError, ConflictError,
            LockError, DirtyWorkdirError, NetworkError,
            PayloadTooLargeError, ValidationError,
        ]
        for cls in errors:
            assert issubclass(cls, MutError), f"{cls.__name__} should be MutError subclass"

    def test_http_status_codes(self):
        expected = {
            MutError: 500,
            NotARepoError: 400,
            SnapshotNotFoundError: 404,
            ObjectNotFoundError: 404,
            PermissionDenied: 403,
            AuthenticationError: 401,
            ConflictError: 409,
            LockError: 409,
            DirtyWorkdirError: 400,
            NetworkError: 502,
            PayloadTooLargeError: 413,
            ValidationError: 422,
        }
        for cls, code in expected.items():
            assert cls.http_status == code, f"{cls.__name__}.http_status should be {code}"

    def test_error_message(self):
        e = PermissionDenied("access denied")
        assert str(e) == "access denied"

    def test_catch_as_mut_error(self):
        try:
            raise AuthenticationError("bad token")
        except MutError as e:
            assert e.http_status == 401

    def test_catch_as_exception(self):
        try:
            raise NetworkError("timeout")
        except Exception as e:
            assert isinstance(e, MutError)
