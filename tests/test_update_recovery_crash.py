"""Abrupt-process recovery over real persisted updater files.

The service fixture uses a test native adapter; these tests qualify the durable
Python boundary, not MSIX/Sparkle installation or fleet SLOs.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from opai.update.models import UpdateState
from test_update_service import NOW, _installed, _service


CHILD = """
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_update_service import _service, _installed
service, _, _, _ = _service(Path(sys.argv[1]))
restarting = service.install(
    service.download(service.check(force=True).operation_id).operation_id, mode='now'
)
original = service._save

def crash_after_durable_write(operation):
    saved = original(operation)
    if saved.state.value == sys.argv[2]:
        os._exit(73)
    return saved

service._save = crash_after_durable_write
service.confirm_health(
    restarting.operation_id, running=_installed(), interactive=False,
    assets_ok=True, state_schema_ok=True, doctor_ok=True,
)
raise RuntimeError('fault phase was never reached')
"""


@pytest.mark.parametrize("phase", ["health_checking", "rollback_pending"])
def test_abrupt_process_exit_recovers_from_durable_health_phase(tmp_path, phase):
    child = subprocess.run(
        [sys.executable, "-c", CHILD, str(tmp_path), phase],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert child.returncode == 73, child.stderr
    service, _, _, adapter = _service(tmp_path)
    assert service.store.load_operation().state.value == phase
    service._now = lambda: NOW + timedelta(minutes=6)
    recovered = service.maintain()
    assert recovered.state is UpdateState.ROLLING_BACK
    assert recovered.quarantined_versions == ("0.3.0",)
    assert adapter.rollbacks == 1
    result = service.confirm_recovery(
        recovered.operation_id,
        running=_installed(),
        interactive=True,
        assets_ok=True,
        state_schema_ok=True,
        doctor_ok=True,
    )
    assert result.state is UpdateState.ROLLED_BACK
    assert service.check(force=True).error_category == "candidate_quarantined"
