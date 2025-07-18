"""Common test utilities and fixtures."""

from dataclasses import dataclass


@dataclass
class TestItem:
    """Test data class that should not be collected as a test by pytest."""

    id: int
    data: str
    processed: bool = False