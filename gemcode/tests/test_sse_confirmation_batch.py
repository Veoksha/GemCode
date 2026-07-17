"""ADK confirmation FC batch must only respond to the last event's FCs."""

from __future__ import annotations

from types import SimpleNamespace

from gemcode.web import sse_adapter


def _event_with_confirmation(fc_id: str) -> SimpleNamespace:
  fc = SimpleNamespace(name=sse_adapter.REQUEST_CONFIRMATION_FC, id=fc_id, args={})

  class Ev:
    def get_function_calls(self):
      return [fc]

  return Ev()


def test_get_confirmation_requests_returns_only_last_event() -> None:
  events = [
    _event_with_confirmation("old_fc"),
    _event_with_confirmation("new_fc"),
  ]
  fcs = sse_adapter._get_confirmation_requests(events)
  assert len(fcs) == 1
  assert fcs[0].id == "new_fc"


def test_get_confirmation_requests_empty_batch() -> None:
  assert sse_adapter._get_confirmation_requests([]) == []
