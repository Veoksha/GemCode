"""Installed package version (PyPI / wheel metadata)."""

from __future__ import annotations

try:
  from importlib.metadata import PackageNotFoundError, version
except ImportError:  # pragma: no cover
  from importlib_metadata import PackageNotFoundError, version


def get_version() -> str:
  try:
    return version("gemcode")
  except PackageNotFoundError:
    return "0.0.0"
