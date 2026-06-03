"""Tests for operator kill switch: alphaTrade_HALT env var + ./HALT sentinel file."""
from __future__ import annotations



from alphaTrade.kill_switch import is_halted, SENTINEL_FILE, ENV_VAR


class TestIsHalted:
    def test_not_halted_by_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ENV_VAR, raising=False)
        monkeypatch.chdir(tmp_path)
        assert is_halted() is False

    def test_halted_by_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "1")
        monkeypatch.chdir(tmp_path)
        assert is_halted() is True

    def test_env_var_zero_not_halted(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "0")
        monkeypatch.chdir(tmp_path)
        assert is_halted() is False

    def test_env_var_empty_not_halted(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "")
        monkeypatch.chdir(tmp_path)
        assert is_halted() is False

    def test_halted_by_sentinel_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ENV_VAR, raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / SENTINEL_FILE).touch()
        assert is_halted() is True

    def test_both_triggers_halted(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "1")
        monkeypatch.chdir(tmp_path)
        (tmp_path / SENTINEL_FILE).touch()
        assert is_halted() is True

    def test_env_var_case_insensitive_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "true")
        monkeypatch.chdir(tmp_path)
        assert is_halted() is True

    def test_env_var_case_insensitive_yes(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "yes")
        monkeypatch.chdir(tmp_path)
        assert is_halted() is True


# ---------------------------------------------------------------------------
# CLI halt / resume commands
# ---------------------------------------------------------------------------

from typer.testing import CliRunner  # noqa: E402
from alphaTrade.cli import app  # noqa: E402


class TestCLIHaltResume:
    def test_halt_creates_sentinel(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["halt"])
        assert result.exit_code == 0
        assert (tmp_path / "HALT").exists()

    def test_resume_removes_sentinel(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "HALT").touch()
        runner = CliRunner()
        result = runner.invoke(app, ["resume"])
        assert result.exit_code == 0
        assert not (tmp_path / "HALT").exists()

    def test_resume_no_sentinel_ok(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(app, ["resume"])
        assert result.exit_code == 0

    def test_halt_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        runner.invoke(app, ["halt"])
        result = runner.invoke(app, ["halt"])
        assert result.exit_code == 0
        assert (tmp_path / "HALT").exists()
