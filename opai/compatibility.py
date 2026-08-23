"""Public read-only compatibility identity for startup and release evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

from opai.asset_identity import load_metadata_binding
from opai.update.models import UPDATE_SCHEMA_VERSION, UPDATER_PROTOCOL_VERSION
from opaihub.generated_lifecycle import SCHEMA_VERSION as LIFECYCLE_SCHEMA_VERSION
from opaihub.provider_catalog import CATALOG_VERSION, PROTOCOL_VERSION


class RuntimeCompatibilityError(RuntimeError):
    """Packaged compatibility metadata cannot be consumed by this runtime."""

    def __init__(
        self, *, expected: Mapping[str, object], actual: Mapping[str, object]
    ) -> None:
        self.expected = dict(expected)
        self.actual = dict(actual)
        super().__init__(
            "packaged compatibility metadata is incompatible with this runtime"
        )


def runtime_compatibility_payload() -> dict[str, object]:
    """Return independently versioned compatibility coordinates."""

    return {
        "lifecycle_schema_version": LIFECYCLE_SCHEMA_VERSION,
        "provider_catalog_version": CATALOG_VERSION,
        "provider_protocol_version": PROTOCOL_VERSION,
        "update_schema_version": UPDATE_SCHEMA_VERSION,
        "updater_protocol_version": UPDATER_PROTOCOL_VERSION,
    }


def load_compatibility_binding(paths: Iterable[Path]) -> dict[str, object] | None:
    """Read the immutable compatibility contract from package metadata."""

    return load_metadata_binding(paths, section="compatibility")


def validate_runtime_compatibility(
    actual: Mapping[str, object],
) -> dict[str, object]:
    """Require the exact read-only compatibility contract before mutation."""

    expected = runtime_compatibility_payload()
    observed = dict(actual)
    if observed != expected:
        raise RuntimeCompatibilityError(expected=expected, actual=observed)
    return expected
