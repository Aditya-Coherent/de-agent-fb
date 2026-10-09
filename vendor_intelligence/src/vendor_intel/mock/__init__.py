"""Mock/demo data — used only when USE_MOCK_DATA=true or --mock flag."""

from vendor_intel.mock.fixtures import (
    apply_mock_validation,
    build_mock_compiler_config,
    generate_mock_discovery_hits,
)

__all__ = [
    "apply_mock_validation",
    "build_mock_compiler_config",
    "generate_mock_discovery_hits",
]
