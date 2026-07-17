"""Tests for hosted multi-tenant workspace path locking."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from gemcode.web.project_root import (
  HostedTenantPathError,
  hosted_tenant_root,
  resolve_web_project_root,
)


@pytest.fixture
def tenant_env(monkeypatch: pytest.MonkeyPatch) -> Path:
  root = Path(tempfile.mkdtemp())
  monkeypatch.setenv("GEMCODE_HOSTED_TENANT_ROOT", str(root))
  return root


def test_hosted_tenant_root_from_env(tenant_env: Path) -> None:
  assert hosted_tenant_root() == tenant_env.resolve()


def test_hosted_mode_ignores_empty_client_path(tenant_env: Path) -> None:
  assert resolve_web_project_root(None) == tenant_env.resolve()
  assert resolve_web_project_root("") == tenant_env.resolve()


def test_hosted_mode_allows_path_inside_tenant(tenant_env: Path) -> None:
  sub = tenant_env / "projects" / "demo"
  sub.mkdir(parents=True)
  assert resolve_web_project_root(str(sub)) == sub.resolve()


def test_hosted_mode_rejects_path_outside_tenant(
  tenant_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  outside = Path(tempfile.mkdtemp())
  monkeypatch.setenv("GEMCODE_HOSTED_TENANT_ROOT", str(tenant_env))
  with pytest.raises(HostedTenantPathError):
    resolve_web_project_root(str(outside))


def test_normal_mode_uses_client_path(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.delenv("GEMCODE_HOSTED_TENANT_ROOT", raising=False)
  outside = Path(tempfile.mkdtemp())
  assert resolve_web_project_root(str(outside)) == outside.resolve()


def test_hosted_mode_auto_trusts_workspace(
  tenant_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setenv("GEMCODE_HOME", str(tenant_env / ".gemcode"))
  from gemcode.trust import ensure_hosted_workspace_trust, is_trusted_root, load_trusted_roots

  assert is_trusted_root(tenant_env)
  sub = tenant_env / "projects" / "app"
  sub.mkdir(parents=True)
  assert is_trusted_root(sub)
  ensure_hosted_workspace_trust()
  assert str(tenant_env.resolve()) in load_trusted_roots()
