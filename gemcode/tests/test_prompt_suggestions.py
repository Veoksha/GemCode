from gemcode.config import GemCodeConfig
from gemcode.prompt_suggestions import build_prompt_suggestion


def test_prompt_suggestion_permission_denied(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  s = build_prompt_suggestion(cfg, terminal_reason="permission_denied")
  assert s is not None
  assert "--yes" in s


def test_prompt_suggestion_completed_none(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  s = build_prompt_suggestion(cfg, terminal_reason="completed")
  assert s is None


def test_prompt_suggestion_session_token_limit(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  s = build_prompt_suggestion(cfg, terminal_reason="session_token_limit")
  assert s is not None
  assert "--session" in s or "session" in s.lower()

