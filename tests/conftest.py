import pytest


@pytest.fixture
def anyio_backend():
    """Run async tests on asyncio only; trio is not a dependency of this project."""
    return "asyncio"
