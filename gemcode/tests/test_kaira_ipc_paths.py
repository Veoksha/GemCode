"""Fleet manager IPC path resolution (stable orchestration socket)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gemcode.kaira_ipc import (
  default_ipc_socket_path,
  fleet_manager_ipc_path,
  maybe_write_manager_ipc_marker,
)


def test_fleet_manager_ipc_prefers_default_when_it_exists(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setenv("GEMCODE_KAIRA_SOCKET", "/nope/this/should/not/be/used")
  primary = default_ipc_socket_path(tmp_path)
  primary.parent.mkdir(parents=True, exist_ok=True)
  primary.write_text("", encoding="utf-8")
  assert fleet_manager_ipc_path(tmp_path) == primary


def test_fleet_manager_ipc_marker_overrides(tmp_path: Path) -> None:
  alt = tmp_path / "other.sock"
  alt.write_text("", encoding="utf-8")
  maybe_write_manager_ipc_marker(fleet_root=tmp_path, socket_path=alt)
  assert fleet_manager_ipc_path(tmp_path) == alt.resolve()


def test_fleet_manager_ipc_falls_back_to_env_when_no_primary(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
  env_sock = tmp_path / "from-env.sock"
  env_sock.write_text("", encoding="utf-8")
  monkeypatch.setenv("GEMCODE_KAIRA_SOCKET", str(env_sock))
  assert fleet_manager_ipc_path(tmp_path) == env_sock
