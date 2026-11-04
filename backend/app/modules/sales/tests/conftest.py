from __future__ import annotations

import pytest

from app.main import create_app
from app.modules.org.testing import make_business
from app.platform import clock
from app.platform.registry import Registry

from .factories import NOW, Shop


@pytest.fixture
def shop(registry: Registry) -> Shop:
    clock.set_clock(lambda: NOW)
    return Shop(create_app(registry), make_business())
