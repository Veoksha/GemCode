"""
Live audio engine (Gemini Live API via ADK).

This wires GemCode's existing outer session + callbacks into ADK's
`Runner.run_live()` path for real-time audio input/output.
"""

from __future__ import annotations

import asyncio
import os
import time
import sys
from dataclasses import dataclass
from typing import Optional

from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig
from google.genai import types

from gemcode.config import GemCodeConfig
from gemcode.session_runtime import create_runner


def _mime_type_for_rate(rate: int) -> str:
  # ADK/examples commonly use this mime type.
  return f"audio/pcm;rate={rate}"


def _require_audio_deps():
  """
  Import audio deps (sounddevice + numpy). Raised error is caught by CLI to show friendly instructions.
  """
  try:
    import sounddevice as sd  # type: ignore
    import numpy as np  # type: ignore
  except ImportError as e:
    raise RuntimeError(
      "Mic capture requires `sounddevice` and `numpy`. Install them to use `gemcode live-audio`."
    ) from e
  return sd, np


def _parse_pcm_rate(mime_type: str | None) -> int | None:
  mt = (mime_type or "").lower()
  if "audio/pcm" not in mt:
    return None
  # e.g. audio/pcm;rate=24000
  for part in mt.split(";"):
    p = part.strip()
    if p.startswith("rate="):
      try:
        return int(p.split("=", 1)[1])
      except Exception:
        return None
  return None


@dataclass
class _AudioIO:
  sd: object
  np: object
  input_rate: int
  output_rate: int
  playback: bool
  _out_stream: object | None = None

  def ensure_output(self) -> None:
    if not self.playback:
      return
    if self._out_stream is not None:
      return
    # RawOutputStream writes bytes directly.
    self._out_stream = self.sd.RawOutputStream(  # type: ignore[attr-defined]
      samplerate=int(self.output_rate),
      channels=1,
      dtype="int16",
    )
    self._out_stream.start()

  def write_audio(self, pcm_bytes: bytes) -> None:
    if not self.playback:
      return
    self.ensure_output()
    try:
      self._out_stream.write(pcm_bytes)  # type: ignore[union-attr]
    except Exception:
      pass

  def close(self) -> None:
    try:
      if self._out_stream is not None:
        self._out_stream.stop()
        self._out_stream.close()
    except Exception:
      pass
    self._out_stream = None


async def run_live_audio(
  cfg: GemCodeConfig,
  *,
  session_id: str,
  user_id: str = "local",
  seconds: int = 10,
  input_rate: int = 24_000,
  language_code: Optional[str] = None,
  playback: bool = True,
) -> None:
  """
  Realtime microphone streaming to Gemini Live + realtime model audio playback.

  Behavior:
  - streams mic audio in small PCM chunks for up to `seconds`
  - prints model-authored text parts (if any)
  - plays model audio parts live when `playback=True`
  """

  sd, np = _require_audio_deps()

  runner = create_runner(cfg)
  live_queue = LiveRequestQueue()

  speech_config = None
  if language_code:
    speech_config = types.SpeechConfig(language_code=language_code)

  run_config = RunConfig(
    # Prefer the enum value (avoids pydantic serializer warnings in some SDK versions).
    # Request TEXT too so users see transcripts even when the model returns only audio.
    response_modalities=[types.Modality.AUDIO, types.Modality.TEXT],
    speech_config=speech_config,
    # Keep SDK defaults for STT/TTS transcription configs.
  )

  agen = runner.run_live(
    user_id=user_id,
    session_id=session_id,
    live_request_queue=live_queue,
    run_config=run_config,
  )

  printed_any = False
  audio_io = _AudioIO(sd=sd, np=np, input_rate=input_rate, output_rate=input_rate, playback=playback)

  async def _consume_events() -> None:
    nonlocal printed_any
    try:
      async for event in agen:
        if not event.content or not event.content.parts:
          continue
        for part in event.content.parts:
          part_text = getattr(part, "text", None)
          # We only print model-authored text to avoid echoing user input.
          if part_text and getattr(event, "author", None) != "user":
            sys.stdout.write(part_text)
            sys.stdout.flush()
            printed_any = True
          # Play audio responses when present.
          inline = getattr(part, "inline_data", None)
          if inline is not None and getattr(event, "author", None) != "user":
            try:
              mime = getattr(inline, "mime_type", None)
              data = getattr(inline, "data", None)
              if isinstance(data, (bytes, bytearray)) and _parse_pcm_rate(mime) is not None:
                r = _parse_pcm_rate(mime) or input_rate
                audio_io.output_rate = int(r)
                audio_io.write_audio(bytes(data))
            except Exception:
              pass
    except Exception as e:
      # Some SDK/ADK versions surface a normal websocket close (1000 OK) as an exception.
      # Treat it as a clean end-of-session (no error).
      try:
        from google.genai.errors import APIError  # type: ignore
        if isinstance(e, APIError) and (
          getattr(e, "status_code", None) == 1000 or "1000" in str(e)
        ):
          return
      except Exception:
        pass
      if "sent 1000 (OK)" in str(e) or "ConnectionClosedOK" in repr(e) or "1000 None" in str(e):
        return
      # Runner/live failures are expected to be surfaced as terminal errors
      # in session state + audit logs; don't crash the CLI.
      raise

  consumer_task = asyncio.create_task(_consume_events())

  # Mic capture → async queue (threaded producer).
  pcm_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)
  stop_at = time.time() + max(1, int(seconds))
  chunks_sent = 0
  non_silent_chunks = 0

  def _mic_thread() -> None:
    # ~20ms frames is a good latency/overhead balance.
    blocksize = max(120, int(input_rate // 50))
    device = os.environ.get("GEMCODE_LIVE_AUDIO_INPUT_DEVICE")
    try:
      stream = sd.RawInputStream(  # type: ignore[attr-defined]
        samplerate=int(input_rate),
        channels=1,
        dtype="int16",
        blocksize=int(blocksize),
        device=device if device else None,
      )
    except Exception:
      # Let the consumer surface this as empty audio.
      return
    with stream:
      while time.time() < stop_at:
        try:
          data, _overflow = stream.read(blocksize)
          if not data:
            continue
          # Push into asyncio queue safely.
          try:
            asyncio.get_running_loop().call_soon_threadsafe(pcm_q.put_nowait, bytes(data))
          except Exception:
            # If the loop isn't available, just drop.
            pass
        except Exception:
          break

  # Send "user started speaking" signal and start streaming.
  live_queue.send_activity_start()
  mic_task = asyncio.create_task(asyncio.to_thread(_mic_thread))

  try:
    while time.time() < stop_at:
      try:
        chunk = await asyncio.wait_for(pcm_q.get(), timeout=0.25)
      except asyncio.TimeoutError:
        continue
      chunks_sent += 1
      try:
        arr = np.frombuffer(chunk, dtype="int16")  # type: ignore[attr-defined]
        # Consider it non-silent if mean abs amplitude crosses a tiny threshold.
        if arr.size and float(np.mean(np.abs(arr))) > 25.0:  # type: ignore[attr-defined]
          non_silent_chunks += 1
      except Exception:
        pass
      live_queue.send_realtime(
        types.Blob(data=chunk, mime_type=_mime_type_for_rate(input_rate))
      )
  finally:
    # End speech activity and close the queue regardless of failures.
    live_queue.send_activity_end()
    live_queue.close()
    try:
      await mic_task
    except Exception:
      pass

  # Wait for event stream to drain.
  try:
    await consumer_task
  finally:
    audio_io.close()

  if not printed_any:
    print("\n[gemcode live-audio] No model text received (audio may have been silent).")
  if chunks_sent <= 2 or non_silent_chunks == 0:
    print(
      "\n[gemcode live-audio] Mic input looks silent or unavailable.\n"
      "Check:\n"
      "- System Settings → Privacy & Security → Microphone (allow your terminal)\n"
      "- Your input device selection\n"
      "Tip: set GEMCODE_LIVE_AUDIO_INPUT_DEVICE to a device index/name from sounddevice.query_devices().\n",
      file=sys.stderr,
    )

  await runner.close()

