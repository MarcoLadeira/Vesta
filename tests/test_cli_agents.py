from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from unittest import mock

from opai import cli


def test_agents_list_uses_shared_projection(tmp_path):
    args = cli.build_parser().parse_args(
        ["agents", "list", "--project", str(tmp_path), "--json"]
    )
    expected = {"objectives": [{"objective_id": "o", "status": "paused"}]}
    with mock.patch(
        "opai.agents_bridge.objectives_payload", return_value=expected
    ) as projection:
        output = io.StringIO()
        with redirect_stdout(output):
            assert args.func(args) == 0
        assert json.loads(output.getvalue()) == expected
        projection.assert_called_once_with(tmp_path.resolve())


def test_agents_stop_one_uses_same_control_as_desktop(tmp_path):
    args = cli.build_parser().parse_args(
        [
            "agents",
            "stop",
            "objective-a",
            "--assignment",
            "worker-a",
            "--project",
            str(tmp_path),
            "--json",
        ]
    )
    with mock.patch(
        "opai.agents_bridge.control_objective_payload",
        return_value={"ok": True, "objective": {"status": "stopping"}},
    ) as control:
        with redirect_stdout(io.StringIO()):
            assert args.func(args) == 0
        assert control.call_args.args[1] == {
            "objective_id": "objective-a",
            "assignment_id": "worker-a",
            "action": "stop",
            "value": None,
        }


def test_agents_budget_parser_keeps_exact_decimal(tmp_path):
    args = cli.build_parser().parse_args(
        ["agents", "budget", "o", "--value", "0.123456789", "--project", str(tmp_path)]
    )
    assert args.value == "0.123456789"


def test_agents_create_persists_without_implicitly_dispatching(tmp_path):
    args = cli.build_parser().parse_args(["agents", "create", "Repair independent defects", "--project", str(tmp_path), "--budget", "1.000000001", "--json"])
    with mock.patch("opai.agents_bridge.create_objective_payload", return_value={"objective_id": "o", "status": "planning"}) as create, mock.patch("opaihub.objective_execution.ObjectiveExecutor.run") as run:
        with redirect_stdout(io.StringIO()):
            assert args.func(args) == 0
        assert create.call_args.args[1]["budgetUsd"] == "1.000000001"
        assert create.call_args.args[1]["allowCloud"] is False
        run.assert_not_called()


def test_agents_run_uses_shared_executor_in_foreground(tmp_path):
    args = cli.build_parser().parse_args(["agents", "run", "o", "--project", str(tmp_path), "--json"])
    with mock.patch("opaihub.objective_execution.ObjectiveExecutor") as executor:
        executor.return_value.run.return_value = {"objective_id": "o", "status": "completed"}
        with redirect_stdout(io.StringIO()):
            assert args.func(args) == 0
        executor.return_value.run.assert_called_once_with("o")
