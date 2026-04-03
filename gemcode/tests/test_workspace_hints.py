from pathlib import Path

from gemcode import workspace_hints


def test_narrow_tip_none_for_non_home(tmp_path: Path) -> None:
  assert workspace_hints.narrow_workspace_tip(tmp_path) is None
  assert workspace_hints.project_root_is_user_home(tmp_path) is False


def test_narrow_tip_for_home_directory() -> None:
  home = Path.home()
  assert workspace_hints.project_root_is_user_home(home) is True
  tip = workspace_hints.narrow_workspace_tip(home)
  assert tip is not None
  assert "gemcode -C" in tip
