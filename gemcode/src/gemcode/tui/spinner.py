"""
Spinner component matching Reference UI exact implementation.
Includes thinking status, tips, and animated spinner.
"""

from __future__ import annotations

import os
import random
import time
from typing import Optional

# Spinner frames - Braille patterns like reference terminal UI
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# Spinner verbs - matching Reference UI variety
SPINNER_VERBS = [
    "Thinking",
    "Analyzing",
    "Planning",
    "Writing",
    "Checking",
    "Reviewing",
    "Processing",
    "Evaluating",
    "Considering",
    "Examining",
]

# Tips - matching Reference UI helpful hints
SPINNER_TIPS = [
    "Use /help to see all available commands",
    "Press Ctrl+O to toggle thinking display",
    "Press Ctrl+K to toggle home screen",
    "Use /clear to start fresh when switching topics",
    "Type /tools to see available tools",
    "Use Esc to interrupt the current operation",
]


class SpinnerState:
    """Manages spinner animation state."""
    
    def __init__(self):
        self.frame_index = 0
        self.verb = random.choice(SPINNER_VERBS)
        self.thinking_start: Optional[float] = None
        self.thinking_duration: Optional[float] = None
        self.thinking_display_until: Optional[float] = None
        self.last_update = time.time()
        
    def get_frame(self) -> str:
        """Get current spinner frame."""
        return SPINNER_FRAMES[self.frame_index % len(SPINNER_FRAMES)]
    
    def advance(self) -> None:
        """Advance to next frame."""
        now = time.time()
        # Update every 120ms like reference terminal UI
        if now - self.last_update >= 0.12:
            self.frame_index += 1
            self.last_update = now
    
    def start_thinking(self) -> None:
        """Mark start of thinking phase."""
        if self.thinking_start is None:
            self.thinking_start = time.time()
            self.thinking_duration = None
            self.thinking_display_until = None
    
    def stop_thinking(self) -> None:
        """Mark end of thinking phase."""
        if self.thinking_start is not None:
            duration = time.time() - self.thinking_start
            self.thinking_duration = duration
            # Display "thought for Xs" for minimum 2 seconds
            self.thinking_display_until = time.time() + 2.0
            self.thinking_start = None
    
    def get_thinking_status(self) -> Optional[str]:
        """Get thinking status text."""
        now = time.time()
        
        # Currently thinking
        if self.thinking_start is not None:
            elapsed = int(now - self.thinking_start)
            if elapsed >= 1:
                return f"💭 Thinking for {elapsed}s"
            return "💭 Thinking"
        
        # Show duration after thinking (for 2s minimum)
        if self.thinking_duration is not None and self.thinking_display_until is not None:
            if now < self.thinking_display_until:
                duration = int(self.thinking_duration)
                return f"Thought for {duration}s"
            # Clear after display period
            self.thinking_duration = None
            self.thinking_display_until = None
        
        return None
    
    def should_show_tip(self, elapsed_seconds: float) -> bool:
        """Determine if we should show a tip based on elapsed time."""
        # Show tips after 30 seconds like reference terminal UI
        return elapsed_seconds > 30
    
    def get_tip(self) -> str:
        """Get a random tip."""
        return random.choice(SPINNER_TIPS)


def format_spinner_line(
    spinner_state: SpinnerState,
    mode: str = "working",
    elapsed_seconds: float = 0,
    show_tip: bool = True,
) -> tuple[str, Optional[str]]:
    """
    Format spinner line matching Reference UI exact style.
    
    Returns:
        Tuple of (main_line, tip_line)
    """
    frame = spinner_state.get_frame()
    verb = spinner_state.verb
    
    # Check for thinking status
    thinking_status = spinner_state.get_thinking_status()
    
    if thinking_status:
        # Show thinking status instead of regular spinner
        main_line = f"  {thinking_status}  ·  Esc=interrupt"
    else:
        # Regular spinner with verb
        main_line = f"  {frame} {verb}…  ·  Esc=interrupt"
    
    # Tip line (shown after 30s)
    tip_line = None
    if show_tip and spinner_state.should_show_tip(elapsed_seconds):
        tip = spinner_state.get_tip()
        tip_line = f"  Tip: {tip}"
    
    return main_line, tip_line


def format_idle_status(git_branch: Optional[str] = None) -> str:
    """Format idle status line matching reference terminal UI."""
    if git_branch:
        return f"  🌿 {git_branch}  ·  Ready  ·  Ctrl+K=home  ·  Ctrl+O=thinking"
    return "  📁 workspace  ·  Ready  ·  Ctrl+K=home  ·  Ctrl+O=thinking"
