"""
SpeechStudio UI panels package.

Each panel is a self-contained widget with a single responsibility.
The MainWindow assembles them into the five-region layout defined in
the UI Specification (section 3).
"""

__all__ = [
    "menu_bar",
    "toolbar",
    "sidebar",
    
    "control_panel",
    "waveform_player",
    "status_bar",
]
