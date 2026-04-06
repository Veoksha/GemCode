"""
Live audio engine (Gemini Live API via ADK).

This wires GemCode's existing outer session + callbacks into ADK's
`Runner.run_live()` path for real-time audio input/output.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Optional

from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig
from google.genai import types

from gemcode.config import GemCodeConfig
from gemcode.session_runtime import create_runner


def _mime_type_for_rate(rate: int) -> str:
  # ADK/examples commonly use this mime type.
  return f"audio/pcm;rate={rate}"


def _record_mic_pcm_blocking(*, rate: int, seconds: int) -> bytes:
  try:
    import sounddevice as sd
    import numpy as np
  except ImportError as e:
    raise RuntimeError(
      "Mic capture requires `sounddevice` and `numpy`. Install them to use `gemcode live-audio`."
    ) from e

  frames = int(rate * seconds)
  # mono int16
  audio = sd.rec(frames, samplerate=rate, channels=1, dtype="int16")
  sd.wait()
  pcm = np.asarray(audio).astype("int16", copy=False)
  return pcm.tobytes()


async def run_live_audio(
  cfg: GemCodeConfig,
  *,
  session_id: str,
  user_id: str = "local",
  seconds: int = 10,
  input_rate: int = 24_000,
  language_code: Optional[str] = None,
) -> None:
  """
  Record microphone audio for `seconds` and send it to Gemini Live.

  MVP behavior:
  - sends the entire recorded buffer as a single audio blob
  - prints any model text parts it returns (typically transcriptions)
  """

  runner = create_runner(cfg)
  live_queue = LiveRequestQueue()

  speech_config = None
  if language_code:
    speech_config = types.SpeechConfig(language_code=language_code)

  run_config = RunConfig(
    response_modalities=["AUDIO"],
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
    except Exception:
      # Runner/live failures are expected to be surfaced as terminal errors
      # in session state + audit logs; don't crash the CLI.
      raise

  consumer_task = asyncio.create_task(_consume_events())

  # Send "user started speaking" signal.
  live_queue.send_activity_start()

  pcm_bytes = await asyncio.to_thread(
    _record_mic_pcm_blocking, rate=input_rate, seconds=seconds
  )
  live_queue.send_realtime(
    types.Blob(data=pcm_bytes, mime_type=_mime_type_for_rate(input_rate))
  )

  # Send "user finished speaking" signal and close the queue.
  live_queue.send_activity_end()
  live_queue.close()

  # Wait for event stream to drain.
  await consumer_task

  if not printed_any:
    print("\n[gemcode live-audio] No model text received (audio may have been silent).")

  await runner.close()

