import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "src", ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture
def randomizer_off(monkeypatch):
    """The randomizer switch as a fresh process would see it with no env var."""
    from AiAgentsFleet import randomizer
    monkeypatch.delenv("AIFLEET_RANDOMIZE", raising=False)
    monkeypatch.setattr(randomizer, "ENABLED", False)
    return randomizer
