from gemcode.config import GemCodeConfig
from gemcode.tools_inspector import inspect_tools, smoke_tools


def test_tools_inspector_smoke_default(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)

  inspections = inspect_tools(cfg)
  failures = smoke_tools(inspections)

  assert not failures, f"Expected no declaration failures, got: {failures}"
  names = {i.name for i in inspections}
  assert "read_file" in names
  assert "write_file" in names


def test_tools_inspector_deep_research_excludes_maps_by_default(
  tmp_path,
) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.enable_deep_research = True
  cfg.enable_maps_grounding = False

  inspections = inspect_tools(cfg)
  failures = smoke_tools(inspections)

  assert not failures
  names = {i.name for i in inspections}
  assert "google_search" in names
  assert "url_context" in names
  assert "google_maps_grounding" not in names


def test_tools_inspector_deep_research_includes_maps_when_opt_in(
  tmp_path,
) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.enable_deep_research = True
  cfg.enable_maps_grounding = True

  inspections = inspect_tools(cfg)
  failures = smoke_tools(inspections)

  assert not failures
  names = {i.name for i in inspections}
  assert "google_maps" in names


def test_tools_inspector_embeddings_tool(tmp_path) -> None:
  cfg = GemCodeConfig(project_root=tmp_path)
  cfg.enable_embeddings = True

  inspections = inspect_tools(cfg)
  failures = smoke_tools(inspections)
  assert not failures

  names = {i.name for i in inspections}
  assert "semantic_search_files" in names

