"""Safety contracts for the read API's local and explicit network binds."""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from equity_lake.cli._app import api_app  # type: ignore[import-untyped]
from equity_lake.cli.commands import api as api_command  # type: ignore[import-untyped]  # noqa: F401


def test_api_defaults_to_loopback_without_confirmation() -> None:
    runner = CliRunner()
    with (
        patch("equity_lake.cli.commands.api._init_logging"),
        patch("equity_lake.api.main.create_app", return_value=object()),
        patch("uvicorn.run") as run,
    ):
        result = runner.invoke(api_app, [])

    assert result.exit_code == 0
    assert "WARNING" not in result.output
    run.assert_called_once()
    assert run.call_args.kwargs["host"] == "127.0.0.1"


@pytest.mark.parametrize("input_text", ["n\n", ""])
def test_non_loopback_bind_aborts_without_consent(input_text: str) -> None:
    runner = CliRunner()
    with (
        patch("equity_lake.cli.commands.api._init_logging"),
        patch("equity_lake.api.main.create_app", return_value=object()),
        patch("uvicorn.run") as run,
    ):
        result = runner.invoke(api_app, ["--host", "0.0.0.0"], input=input_text)

    assert result.exit_code == 1
    assert "UNAUTHENTICATED" in result.output
    assert "Expose the unauthenticated API" in result.output
    run.assert_not_called()


def test_explicit_exposure_opt_in_runs_noninteractively_with_warning() -> None:
    runner = CliRunner()
    with (
        patch("equity_lake.cli.commands.api._init_logging"),
        patch("equity_lake.api.main.create_app", return_value=object()),
        patch("uvicorn.run") as run,
    ):
        result = runner.invoke(
            api_app,
            ["--host", "0.0.0.0", "--allow-unauthenticated-exposure"],
            input="",
        )

    assert result.exit_code == 0
    assert "UNAUTHENTICATED" in result.output
    assert "Expose the unauthenticated API" not in result.output
    run.assert_called_once()
    assert run.call_args.kwargs["host"] == "0.0.0.0"


def test_api_help_discloses_explicit_unauthenticated_exposure_opt_in() -> None:
    result = CliRunner().invoke(api_app, ["--help"], env={"COLUMNS": "180"})

    assert result.exit_code == 0
    assert "--allow-unauthenticated-exposure" in result.output


def test_docker_api_default_and_documented_explicit_container_exposure() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    api_guide = Path("docs/user-guide/20260811-read-api.md").read_text(encoding="utf-8")

    assert 'CMD ["equity", "api", "serve", "--host", "127.0.0.1", "--port", "8000"]' in dockerfile
    assert "http://127.0.0.1:8000/health" in dockerfile
    assert "--allow-unauthenticated-exposure" in api_guide
    assert "-p 127.0.0.1:8000:8000" in api_guide
