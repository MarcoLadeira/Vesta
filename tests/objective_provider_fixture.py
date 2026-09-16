"""Deterministic provider boundary for real objective guardian/worker tests."""

import json
from pathlib import Path
import sys
from unittest import mock

from opaihub.objective_worker import main


def reply(root, prompt, **kwargs):
    if prompt.startswith("Return ONLY a JSON object"):
        assert kwargs["mode"] == "plan"
        answer = json.dumps(
            {
                "assignments": [
                    {
                        "name": "docs",
                        "objective": "Update README",
                        "intended_paths": ["README.md"],
                    }
                ]
            }
        )
    else:
        assert kwargs["mode"] == "safe-auto"
        assert kwargs["objective_bypass_permissions"] is False
        Path(root, "README.md").write_text("Updated documentation\n", encoding="utf-8")
        answer = "Updated README in the isolated worktree."
    return {
        "status": "answered",
        "completion_state": "completed",
        "completion_verdict": {"verdict": "completed"},
        "answer": answer,
        "objective_cost_events": [
            {
                "operation_key": kwargs["run_id"] + "-provider",
                "amount_usd": "0",
                "measurement_kind": "actual",
            }
        ],
    }


if __name__ == "__main__":
    with (
        mock.patch(
            "opai.app_state.available_models",
            return_value={
                "models": [
                    {"id": "account:codex:test", "kind": "account", "available": True}
                ]
            },
        ),
        mock.patch(
            "opaihub.objective_routing.provider_usage.usage_overview", return_value=[]
        ),
        mock.patch("opaihub.gui_pipeline.handle_gui_message", side_effect=reply),
    ):
        raise SystemExit(main(sys.argv[1:]))
