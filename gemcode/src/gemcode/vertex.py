"""
Vertex AI (optional production path).

Set for Google GenAI client routing (see google-genai docs):
- GOOGLE_GENAI_USE_VERTEXAI=true
- GOOGLE_CLOUD_PROJECT=your-project-id
- GOOGLE_CLOUD_LOCATION=us-central1

Application Default Credentials (gcloud auth application-default login) or
service account for CI.

GemCode currently uses the default google-genai behavior from the environment;
no extra code is required for basic Vertex text generation once env is set.
"""

from __future__ import annotations

import os


def vertex_env_active() -> bool:
  return os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true", "yes")
