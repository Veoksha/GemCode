import json
import os
import subprocess
import sys
from typing import Any


def _parse_sse_data_frames(stdout: str) -> list[dict[str, Any]]:
  out: list[dict[str, Any]] = []
  # Frames are separated by blank lines; each frame contains one `data: ...` line.
  for frame in stdout.split("\n\n"):
    for line in frame.splitlines():
      if line.startswith("data: "):
        payload = line[len("data: ") :].strip()
        try:
          out.append(json.loads(payload))
        except json.JSONDecodeError:
          # ignore malformed frames
          pass
  return out


def test_claude_sse_adapter_mock(tmp_path) -> None:
  """
  Validates that the web adapter emits a Claude-like SSE stream
  in both StreamEvent and StreamChunk formats.
  """
  req = {
    "messages": [{"role": "user", "content": "Hello"}],
    "model": "gemini-2.5-flash",
    "stream": True,
  }

  env = os.environ.copy()
  env["GEMCODE_WEB_PROJECT_ROOT"] = str(tmp_path)
  env["GEMCODE_WEB_MOCK_RESPONSE"] = "Hello world from mock"
  env["GEMCODE_WEB_MOCK_CHUNK"] = "4"

  proc = subprocess.run(
    [sys.executable, "-m", "gemcode.web.claude_sse_adapter"],
    input=json.dumps(req).encode("utf-8"),
    env=env,
    capture_output=True,
    check=False,
  )

  assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
  frames = _parse_sse_data_frames(proc.stdout.decode("utf-8", errors="replace"))

  # StreamEvent boundaries (useChat)
  assert any(f.get("type") == "message_start" for f in frames)
  assert any(f.get("type") == "content_block_start" for f in frames)
  assert any(f.get("type") == "content_block_delta" for f in frames)
  assert any(f.get("type") == "content_block_stop" for f in frames)
  assert any(f.get("type") == "message_stop" for f in frames)

  # StreamChunk chunks (ChatInput)
  text_chunks = [f for f in frames if f.get("type") == "text" and isinstance(f.get("content"), str)]
  assert text_chunks, "expected at least one text chunk"
  assembled = "".join(f["content"] for f in text_chunks)
  assert assembled == env["GEMCODE_WEB_MOCK_RESPONSE"]

  assert any(f.get("type") == "done" for f in frames)

