from gemcode.config import GemCodeConfig
from gemcode.modality_tools import build_extra_tools


def test_deep_research_includes_google_maps(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.enable_deep_research = True

  extra = build_extra_tools(cfg)

  # Maps grounding is opt-in.
  assert not any(getattr(t, "name", "") == "google_maps_grounding" for t in extra)


def test_deep_research_opt_in_maps_includes_google_maps(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.enable_deep_research = True
  cfg.enable_maps_grounding = True

  extra = build_extra_tools(cfg)

  # Compare singleton tool object identity to be strict.
  from google.adk.tools.google_maps_grounding_tool import google_maps_grounding

  assert google_maps_grounding in extra

