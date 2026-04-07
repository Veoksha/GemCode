"""
Injectable dependencies (cf. typical `query/deps.ts`).

Kept minimal: tests can replace `uuid` without patching modules.
"""

from __future__ import annotations

import uuid as uuid_mod
from dataclasses import dataclass
from typing import Callable


@dataclass
class QueryDeps:
  uuid: Callable[[], str]


def production_deps() -> QueryDeps:
  return QueryDeps(uuid=lambda: str(uuid_mod.uuid4()))
