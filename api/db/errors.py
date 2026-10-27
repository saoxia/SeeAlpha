"""Stable errors shared by all business storage implementations."""


class StorageUnavailable(RuntimeError):
    pass


class StorageConflict(RuntimeError):
    """A write violates a database integrity constraint."""
