"""Fixtures shared by the Qt based suites."""

import pytest
from PySide6.QtCore import QCoreApplication


@pytest.fixture(scope="session")
def qcore_app():
    return QCoreApplication.instance() or QCoreApplication([])
