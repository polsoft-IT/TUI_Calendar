# TerminalX Calendar: {"version": "1.0.0", "author": "polsoft.ITS™ Group", "description": "TerminalX Calendar - month view with events (curses TUI)", "aliases": ["txcal"]}

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TXCal v1.1 - a compact curses month calendar with events, search,
agenda, clipboard and event moving."""

from __future__ import annotations

import calendar
import csv
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Optional

try:
    import curses
except ModuleNotFoundError as exc:
    if exc.name == "_curses":
        print(
            "This curses TUI requires a Python build with the _curses module. "
            "Use: py -3.14 TXCal.py",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    raise


_CP_TITLE = 1
_CP_STATUS = 2
_CP_CURSOR = 3
_CP_TODAY = 4
_CP_MONTH = 5
_CP_OTHER = 6
_CP_WEEKEND = 7
_CP_EVENT = 8
_CP_MENU = 9
_CP_MENU_KEY = 10
_CP_HEADER = 11
_CP_HELP = 12
_CP_MATCH = 13
_CP_FRAME = 14
_CP_PANEL = 15
_CP_ACCENT = 16
_CP_DIM = 17
_CP_HINT = 18

# unicode glyphs used across the UI (ascii fallbacks provided)
_GLYPHS = {
    "bullet": "•", "dot": "·", "vline": "│", "hline": "─",
    "cross": "┼", "tee_l": "├", "tee_r": "┤", "corner": "┘",
    "star": "*", "arrow": "‹›", "clock": "◷",
}
_ASCII = {
    "bullet": "*", "dot": ".", "vline": "|", "hline": "-",
    "cross": "+", "tee_l": "+", "tee_r": "+", "corner": "+",
    "star": "*", "arrow": "<>", "clock": "o",
}


def _glyph(name: str) -> str:
    """Prefer unicode box-drawing glyphs, fall back to ascii on Win9x TUIs."""
    try:
        if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8" \
                and os.name != "nt":
            return _ASCII[name]
    except (AttributeError, KeyError):
        pass
    return _GLYPHS.get(name, "?")

_STORE_NAME = ".txcal_events.json"


def classify_event_priority(text: str) -> str:
    """Classify a task/event into High, Medium, or Low priority.

    The classifier is intentionally tolerant: it recognizes common markers such
    as [HIGH], !H, [medium], or plain text with no marker.
    """
    normalized = text.strip().lower()
    if any(marker in normalized for marker in ("[high]", "!h", "high")):
        return "High"
    if any(marker in normalized for marker in ("[medium]", "!m", "medium")):
        return "Medium"
    if any(marker in normalized for marker in ("[low]", "!l", "low")):
        return "Low"
    return "Low"


def resolve_store_path(argv: Optional[list[str]] = None) -> Path:
    """Return the storage path for the calendar data.

    Keeps all path-resolution logic in one place so the single-file app stays
    easy to read and extend.
    """
    default_store = Path(os.environ.get("TXCAL_STORE", "") or Path.home() / _STORE_NAME)
    candidates = list(argv or sys.argv[1:])
    if candidates:
        return Path(candidates[0]).expanduser()
    return default_store.expanduser()


def _init_colors() -> None:
    if not curses.has_colors():
        return
    curses.start_color()
    curses.use_default_colors()

    # High-contrast terminal palette for a cleaner, more premium look.
    curses.init_pair(_CP_TITLE, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(_CP_STATUS, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(_CP_CURSOR, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(_CP_TODAY, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(_CP_MONTH, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(_CP_OTHER, curses.COLOR_BLACK, curses.COLOR_BLACK)
    curses.init_pair(_CP_WEEKEND, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
    curses.init_pair(_CP_EVENT, curses.COLOR_GREEN, curses.COLOR_BLACK)
    curses.init_pair(_CP_MENU, curses.COLOR_CYAN, curses.COLOR_BLUE)
    curses.init_pair(_CP_MENU_KEY, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(_CP_HEADER, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(_CP_HELP, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(_CP_MATCH, curses.COLOR_BLACK, curses.COLOR_YELLOW)
    curses.init_pair(_CP_FRAME, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(_CP_PANEL, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(_CP_ACCENT, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(_CP_DIM, curses.COLOR_BLUE, curses.COLOR_BLACK)
    curses.init_pair(_CP_HINT, curses.COLOR_BLACK, curses.COLOR_CYAN)


# ── app lifecycle ───────────────────────────────────────────────────────────
# Single-file app pattern:
#   setup() -> initialize terminal + app state
#   render_frame() -> draw UI
#   process_input() -> read and handle keyboard input
#   main() -> entry point


def _clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    return text[:width]


def _safe_addstr(win, row: int, column: int, text: str, attr: int = 0) -> None:
    """Avoid curses ERR when text reaches the terminal's lower-right cell."""
    try:
        max_width = max(0, win.getmaxyx()[1] - column - 1)
        win.addnstr(row, column, text, max_width, attr)
    except curses.error:
        pass


def prompt_for_text(stdscr, title: str, prompt: str,
                   default: str = "") -> Optional[str]:
    """Prompt for a single-line text input inside a small dialog."""
    height, width = stdscr.getmaxyx()
    if height < 4 or width < 8:
        return None
    dialog_width = min(max(40, len(prompt) + len(default) + 10), width - 2)
    dialog_width = max(12, dialog_width)
    dialog_height = 6
    win = curses.newwin(dialog_height, dialog_width,
                        max(0, (height - dialog_height) // 2),
                        max(0, (width - dialog_width) // 2))
    win.bkgd(" ", curses.color_pair(_CP_STATUS))
    win.border()
    if dialog_width > 10:
        win.addstr(0, max(1, (dialog_width - len(title) - 2) // 2),
                   _clip(f" {title} ", dialog_width - 2),
                   curses.color_pair(_CP_MENU_KEY) | curses.A_BOLD)
    win.addstr(1, 2, _clip(prompt, max(0, dialog_width - 4)))

    value = list(default)
    curses.curs_set(1)
    try:
        while True:
            win.addstr(2, 2, " " * max(1, dialog_width - 4))
            win.addstr(2, 2, "".join(value)[:max(1, dialog_width - 4)])
            win.move(2, min(dialog_width - 2, 2 + len(value)))
            win.refresh()
            key = win.getch()
            if key in (10, 13, curses.KEY_ENTER):
                return "".join(value)
            if key == 27:
                return None
            if key in (curses.KEY_BACKSPACE, 8, 127):
                if value:
                    value.pop()
            elif 32 <= key <= 126 and len(value) < dialog_width - 6:
                value.append(chr(key))
    finally:
        curses.curs_set(0)


class CalendarApp:
    """Month-view calendar with a JSON event store."""

    def __init__(self, stdscr, store_path: Path) -> None:
        self.stdscr = stdscr
        self.store_path = store_path
        self.today = datetime.date.today()
        self.year = self.today.year
        self.month = self.today.month
        # (row, col) inside the month grid; row 0 = first week
        self.row = 0
        self.column = 0
        self.running = True
        self.message = "Ready"
        self.events: dict[str, list[str]] = {}
        self.show_help_wrap = True
        # clipboard: list of event texts + their source date
        self.clipboard: list[tuple[datetime.date, str]] = []
        self.search_text = ""
        self.search_hits: set[str] = set()
        self.menu_items = [
            ("Add event", "add"),
            ("Edit event", "edit"),
            ("Delete event", "delete"),
            ("Move event", "move_event"),
            ("Copy day", "copy_day"),
            ("Cut day", "cut_day"),
            ("Paste events", "paste_day"),
            ("Clear day", "clear_day"),
            ("Export CSV", "export_csv"),
            ("Import CSV", "import_csv"),
            ("Statistics", "stats"),
            ("Priority view", "priority"),
            ("Search events", "search"),
            ("Agenda", "agenda"),
            ("Today", "today"),
            ("Next month", "next_month"),
            ("Previous month", "prev_month"),
            ("Jump to date", "jump"),
            ("Help", "help"),
            ("About", "about"),
            ("Quit", "quit"),
        ]
        self.load_events()

    # ── storage logic ───────────────────────────────────────────────────────
    def load_events(self) -> None:
        """Load event data from the JSON store."""
        try:
            self.events = json.loads(self.store_path.read_text(
                encoding="utf-8"))
        except FileNotFoundError:
            self.events = {}
            self.message = "New event store"
        except (OSError, ValueError) as exc:
            self.events = {}
            self.message = f"Store error: {exc}"

    def save_events(self) -> None:
        """Persist the current calendar events to disk."""
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            self.store_path.write_text(
                json.dumps(self.events, indent=2, sort_keys=True),
                encoding="utf-8")
        except OSError as exc:
            self.message = f"Save error: {exc}"
        else:
            self.message = "Events saved"

    # ── calendar helpers ─────────────────────────────────────────────────────
    def _key(self, day: int) -> str:
        return f"{self.year:04d}-{self.month:02d}-{day:02d}"

    def _grid(self) -> list[list[Optional[int]]]:
        """Month weeks as rows of day numbers (None outside the month)."""
        weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(
            self.year, self.month)
        return [[day or None for day in week] for week in weeks]

    def _selected_day(self) -> Optional[int]:
        grid = self._grid()
        self.row = min(self.row, len(grid) - 1)
        week = grid[self.row]
        if 0 <= self.column < 7 and week[self.column]:
            return week[self.column]
        return None

    def _selected_date(self) -> Optional[datetime.date]:
        day = self._selected_day()
        if day is None:
            return None
        return datetime.date(self.year, self.month, day)

    def _day_events(self, day: int) -> list[str]:
        return self.events.get(self._key(day), [])

    def _move(self, delta: int) -> None:
        grid = self._grid()
        self.row = max(0, min(len(grid) - 1, self.row + delta))
        self._fix_column()

    def _move_day(self, delta: int) -> None:
        date = self._selected_date()
        if date is None:
            return
        target = date + datetime.timedelta(days=delta)
        self.year, self.month = target.year, target.month
        grid = self._grid()
        for index, week in enumerate(grid):
            if target.day in week:
                self.row = index
                self.column = week.index(target.day)
                return
        self.row = self.column = 0

    def _fix_column(self) -> None:
        grid = self._grid()
        week = grid[self.row]
        if week[self.column]:
            return
        for step in (1, -1, 2, -2, 3, -3):
            probe = self.column + step
            if 0 <= probe < 7 and week[probe]:
                self.column = probe
                return

    def _shift_month(self, delta: int) -> None:
        current_day = self._selected_day() or self.today.day
        month = self.month + delta
        if month < 1:
            month, self.year = 12, self.year - 1
        elif month > 12:
            month, self.year = 1, self.year + 1
        self.month = month
        self.row = self.column = 0
        grid = self._grid()
        last_day = max(
            day for week in grid for day in week if day is not None
        )
        target_day = min(current_day, last_day)
        for index, week in enumerate(grid):
            if target_day in week:
                self.row = index
                self.column = week.index(target_day)
                break

    # ── actions ──────────────────────────────────────────────────────────────
    def _add_event(self) -> None:
        date = self._selected_date()
        if date is None:
            self.message = "No day selected"
            return
        value = prompt_for_text(self.stdscr, "Add event",
                               f"{date:%Y-%m-%d} event:", "")
        if not value:
            self.message = "Cancelled"
            return
        self.events.setdefault(self._key(date.day), []).append(value)
        self.save_events()
        self.message = f"Event added: {value}"

    def _edit_event(self) -> None:
        date = self._selected_date()
        if date is None:
            self.message = "No day selected"
            return
        day_events = self._day_events(date.day)
        if not day_events:
            self.message = "No events on this day"
            return
        if len(day_events) == 1:
            index, current = 0, day_events[0]
        else:
            index = self._pick_event(day_events)
            if index is None:
                return
            current = day_events[index]
        value = prompt_for_text(self.stdscr, "Edit event",
                               f"{date:%Y-%m-%d} event:", current)
        if value is None:
            self.message = "Cancelled"
            return
        if not value:
            day_events.pop(index)
        else:
            day_events[index] = value
        if not day_events:
            self.events.pop(self._key(date.day), None)
        self.save_events()
        self.message = "Event updated"

    def _delete_event(self) -> None:
        date = self._selected_date()
        if date is None:
            self.message = "No day selected"
            return
        day_events = self._day_events(date.day)
        if not day_events:
            self.message = "No events on this day"
            return
        if len(day_events) == 1:
            index = 0
        else:
            index = self._pick_event(day_events)
            if index is None:
                return
        removed = day_events.pop(index)
        if not day_events:
            self.events.pop(self._key(date.day), None)
        self.save_events()
        self.message = f"Deleted: {removed}"

    def _pick_event(self, day_events: list[str]) -> Optional[int]:
        """Let the user choose one of the day's events."""
        height, width = self.stdscr.getmaxyx()
        menu_width = min(50, max(30, width - 6))
        menu_height = min(len(day_events) + 2, max(4, height - 2))
        win = curses.newwin(menu_height, menu_width,
                            max(0, (height - menu_height) // 2),
                            max(0, (width - menu_width) // 2))
        win.bkgd(" ", curses.color_pair(_CP_MENU))
        win.keypad(True)
        selected = 0
        while True:
            win.erase()
            win.border()
            win.addstr(0, 2, " Pick event ",
                       curses.color_pair(_CP_MENU_KEY) | curses.A_BOLD)
            for index, event in enumerate(
                    day_events[:max(0, menu_height - 2)]):
                attr = (curses.color_pair(_CP_CURSOR) if index == selected
                        else curses.color_pair(_CP_MENU))
                win.addstr(index + 1, 2,
                           _clip(event, menu_width - 5).ljust(menu_width - 4),
                           attr)
            win.refresh()
            key = win.getch()
            if key == curses.KEY_UP:
                selected = (selected - 1) % len(day_events)
            elif key == curses.KEY_DOWN:
                selected = (selected + 1) % len(day_events)
            elif key in (10, 13, curses.KEY_ENTER):
                return selected
            elif key == 27:
                return None

    # ── clipboard ────────────────────────────────────────────────────────────
    def _copy_day(self, cut: bool = False) -> None:
        date = self._selected_date()
        if date is None:
            self.message = "No day selected"
            return
        day_events = self._day_events(date.day)
        if not day_events:
            self.message = "No events to copy"
            return
        self.clipboard = [(date, text) for text in day_events]
        if cut:
            self.events.pop(self._key(date.day), None)
            self.save_events()
            self.message = f"Cut {len(day_events)} event(s) from {date}"
        else:
            self.message = f"Copied {len(day_events)} event(s) from {date}"

    def _paste_day(self) -> None:
        date = self._selected_date()
        if date is None:
            self.message = "No day selected"
            return
        if not self.clipboard:
            self.message = "Clipboard is empty"
            return
        target = self.events.setdefault(self._key(date.day), [])
        for _source, text in self.clipboard:
            target.append(text)
        self.save_events()
        self.message = (f"Pasted {len(self.clipboard)} event(s) "
                        f"to {date:%Y-%m-%d}")

    def _move_event(self) -> None:
        """Move one event from the selected day to another date."""
        date = self._selected_date()
        if date is None:
            self.message = "No day selected"
            return
        day_events = self._day_events(date.day)
        if not day_events:
            self.message = "No events on this day"
            return
        index = (0 if len(day_events) == 1
                 else self._pick_event(day_events))
        if index is None:
            return
        value = prompt_for_text(self.stdscr, "Move event to",
                               "Date (YYYY-MM-DD):", self.today.isoformat())
        if not value:
            self.message = "Cancelled"
            return
        try:
            target = datetime.date.fromisoformat(value.strip())
        except ValueError:
            self.message = "Invalid date (use YYYY-MM-DD)"
            return
        moved = day_events.pop(index)
        if not day_events:
            self.events.pop(self._key(date.day), None)
        self.events.setdefault(target.isoformat(), []).append(moved)
        self.save_events()
        self.message = f"Moved to {target.isoformat()}: {moved}"
        if target.year == self.year and target.month == self.month:
            for row_index, week in enumerate(self._grid()):
                if target.day in week:
                    self.row = row_index
                    self.column = week.index(target.day)
                    break

    # ── search ───────────────────────────────────────────────────────────────
    def _search(self) -> None:
        value = prompt_for_text(self.stdscr, "Search events",
                               "Find text:", self.search_text)
        if value is None:
            return
        self.search_text = value
        if not value:
            self.search_hits = set()
            self.message = "Search cleared"
            return
        needle = value.lower()
        self.search_hits = {
            key for key, texts in self.events.items()
            if any(needle in text.lower() for text in texts)
        }
        count = len(self.search_hits)
        self.message = (f"{count} matching day(s) for '{value}'"
                        if count else f"No matches for '{value}'")
        if self.search_hits:
            target = min(self.search_hits)  # first matching date
            parsed = datetime.date.fromisoformat(target)
            self.year, self.month = parsed.year, parsed.month
            for row_index, week in enumerate(self._grid()):
                if parsed.day in week:
                    self.row = row_index
                    self.column = week.index(parsed.day)
                    break

    def _next_hit(self, direction: int) -> None:
        if not self.search_hits:
            self.message = "No search results (press F)"
            return
        hits = sorted(self.search_hits)
        current = self._selected_date()
        stamp = current.isoformat() if current else ""
        if stamp in hits:
            position = hits.index(stamp)
            position = (position + direction) % len(hits)
        else:
            position = 0 if direction > 0 else len(hits) - 1
        target = datetime.date.fromisoformat(hits[position])
        self.year, self.month = target.year, target.month
        for row_index, week in enumerate(self._grid()):
            if target.day in week:
                self.row = row_index
                self.column = week.index(target.day)
                break
        self.message = f"Hit {position + 1}/{len(hits)}: {hits[position]}"

    # ── agenda ───────────────────────────────────────────────────────────────
    def _agenda(self) -> None:
        """Scrollable list of upcoming events, grouped by date."""
        rows: list[tuple[str, int]] = []  # (text, color pair)
        for key in sorted(self.events):
            try:
                date = datetime.date.fromisoformat(key)
            except ValueError:
                continue
            label = f"{date:%a %Y-%m-%d}"
            if date == self.today:
                label += "  (today)"
            rows.append((label, _CP_HEADER))
            for text in self.events[key]:
                rows.append((f"  • {text}", _CP_EVENT))
        if not rows:
            self.message = "Agenda is empty"
            return
        height, width = self.stdscr.getmaxyx()
        agenda_width = min(64, max(34, width - 6))
        agenda_height = min(max(8, height - 6), max(6, height - 4))
        win = curses.newwin(agenda_height, agenda_width,
                            max(0, (height - agenda_height) // 2),
                            max(0, (width - agenda_width) // 2))
        win.bkgd(" ", curses.color_pair(_CP_MENU))
        win.keypad(True)
        top = 0
        visible = max(1, agenda_height - 3)
        while True:
            win.erase()
            win.border()
            win.addstr(0, 2, " Agenda ",
                       curses.color_pair(_CP_MENU_KEY) | curses.A_BOLD)
            for offset, index in enumerate(range(top, min(len(rows),
                                                          top + visible))):
                text, pair = rows[index]
                _safe_addstr(win, offset + 1, 2,
                             _clip(text, agenda_width - 4),
                             curses.color_pair(pair))
            if len(rows) > visible:
                _safe_addstr(win, agenda_height - 2, 2,
                             _clip(f"{top + 1}-{min(len(rows), top + visible)}"
                                   f" of {len(rows)} | Up/Down, Esc=close",
                                   agenda_width - 4), curses.A_BOLD)
            win.refresh()
            key = win.getch()
            if key == curses.KEY_UP:
                top = max(0, top - 1)
            elif key == curses.KEY_DOWN:
                top = min(max(0, len(rows) - visible), top + 1)
            elif key == curses.KEY_PPAGE:
                top = max(0, top - visible)
            elif key == curses.KEY_NPAGE:
                top = min(max(0, len(rows) - visible), top + visible)
            elif key in (27, 10, 13, curses.KEY_ENTER, ord("q"), ord("Q")):
                return

    def _clear_day(self) -> None:
        date = self._selected_date()
        if date is None:
            self.message = "No day selected"
            return
        key = self._key(date.day)
        if key not in self.events:
            self.message = "No events on this day"
            return
        count = len(self.events[key])
        self.events.pop(key, None)
        self.save_events()
        self.message = f"Removed {count} event(s)"

    def _import_csv(self) -> None:
        """Import events from a CSV file with columns: date,text."""
        import_path = self.store_path.with_name(
            self.store_path.name.replace(".json", "") + ".csv"
        )
        if not import_path.exists():
            self.message = f"No CSV file found: {import_path.name}"
            return
        imported = 0
        try:
            with import_path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.reader(fh)
                next(reader, None)
                for row in reader:
                    if len(row) < 2:
                        continue
                    date_text, text = row[0].strip(), row[1].strip()
                    if not date_text or not text:
                        continue
                    try:
                        target_date = datetime.date.fromisoformat(date_text)
                    except ValueError:
                        continue
                    key = target_date.isoformat()
                    self.events.setdefault(key, [])
                    self.events[key].append(text)
                    imported += 1
        except (OSError, csv.Error) as exc:
            self.message = f"Import failed: {exc}"
            return
        self.save_events()
        self.message = f"Imported {imported} event(s) from {import_path.name}"

    def _export_csv(self) -> None:
        """Export all events to a CSV file in the same directory as the store."""
        if not self.events:
            self.message = "No events to export"
            return
        export_path = self.store_path.with_name(
            self.store_path.name.replace(".json", "") + ".csv"
        )
        try:
            with export_path.open("w", encoding="utf-8", newline="") as fh:
                fh.write("date,text\n")
                for key in sorted(self.events):
                    try:
                        date = datetime.date.fromisoformat(key)
                    except ValueError:
                        continue
                    for text in self.events[key]:
                        safe_text = text.replace('"', '""')
                        fh.write(f'"{date.isoformat()}","{safe_text}"\n')
        except OSError as exc:
            self.message = f"Export failed: {exc}"
            return
        self.message = f"Exported to {export_path.name}"

    def _show_stats(self) -> None:
        """Display a small summary of event counts and busiest days."""
        if not self.events:
            self.message = "No events to summarize"
            return
        day_counts = [(date, len(events)) for date, events in self.events.items()]
        total_events = sum(count for _, count in day_counts)
        busy_day = max(day_counts, key=lambda item: item[1])
        days_with_events = len(day_counts)
        first_day = min(datetime.date.fromisoformat(key) for key in self.events)
        last_day = max(datetime.date.fromisoformat(key) for key in self.events)
        lines = [
            f"Total events: {total_events}",
            f"Days with events: {days_with_events}",
            f"Busiest day: {busy_day[0]} ({busy_day[1]} event(s))",
            f"Range: {first_day.isoformat()} → {last_day.isoformat()}",
            "",
            "Tip: use Search or Agenda to inspect details.",
        ]
        self._show_text_dialog("Statistics", lines)

    def _priority_view(self) -> None:
        """Show a compact overview of event priorities."""
        buckets = {"High": [], "Medium": [], "Low": []}
        for entries in self.events.values():
            for text in entries:
                level = classify_event_priority(text)
                buckets[level].append(text)
        lines = [
            f"High:   {len(buckets['High'])}",
            f"Medium: {len(buckets['Medium'])}",
            f"Low:    {len(buckets['Low'])}",
            "",
        ]
        for level in ("High", "Medium", "Low"):
            if buckets[level]:
                lines.append(f"{level} tasks:")
                for text in buckets[level][:5]:
                    lines.append(f"  - {text}")
                if len(buckets[level]) > 5:
                    lines.append(f"  ... and {len(buckets[level]) - 5} more")
            else:
                lines.append(f"{level} tasks: none")
        if not any(buckets.values()):
            self.message = "No events to classify"
            return
        self._show_text_dialog("Priority view", lines)

    def _go_today(self) -> None:
        self.year, self.month = self.today.year, self.today.month
        grid = self._grid()
        for index, week in enumerate(grid):
            if self.today.day in week:
                self.row = index
                self.column = week.index(self.today.day)
                return
        self.row = self.column = 0

    def _jump(self) -> None:
        value = prompt_for_text(self.stdscr, "Jump to date",
                               "Date (YYYY-MM-DD):",
                               self.today.isoformat())
        if not value:
            self.message = "Cancelled"
            return
        try:
            target = datetime.date.fromisoformat(value.strip())
        except ValueError:
            self.message = "Invalid date (use YYYY-MM-DD)"
            return
        self.year, self.month = target.year, target.month
        grid = self._grid()
        for index, week in enumerate(grid):
            if target.day in week:
                self.row = index
                self.column = week.index(target.day)
                break
        self.message = f"Jumped to {target.isoformat()}"

    # ── drawing ──────────────────────────────────────────────────────────────
    @property
    def title(self) -> str:
        month_name = calendar.month_name[self.month]
        return (f" TXCal v1.2 | {month_name} {self.year} "
                f"│ store: {self.store_path.name} ")

    def draw(self) -> None:
        height, width = self.stdscr.getmaxyx()
        self.stdscr.erase()

        title_attr = curses.color_pair(_CP_TITLE) | curses.A_BOLD
        self.stdscr.addstr(0, 0, " " * max(0, width), curses.color_pair(_CP_STATUS))
        self.stdscr.addstr(0, 0, _clip(self.title, width), title_attr)

        divider_attr = curses.color_pair(_CP_FRAME) | curses.A_BOLD
        _safe_addstr(self.stdscr, 1, 0, "─" * max(1, width), divider_attr)

        grid = self._grid()
        today_key = self._key(self.today.day) \
            if (self.today.year == self.year
                and self.today.month == self.month) else None
        selected_day = self._selected_day()

        row = 2
        header = " Mon  Tue  Wed  Thu  Fri  Sat  Sun"
        header_attr = curses.color_pair(_CP_STATUS) | curses.A_BOLD
        _safe_addstr(self.stdscr, row, 0, _clip(header.ljust(width), width),
                     header_attr)
        row += 1

        for week_index, week in enumerate(grid):
            day_row = row + week_index
            if day_row >= height - 2:
                break
            for column, day in enumerate(week):
                x = 1 + column * 5
                if day is None:
                    cell = f" {_glyph('dot')} "
                    _safe_addstr(self.stdscr, day_row, x,
                                 _clip(cell, width - x),
                                 curses.color_pair(_CP_DIM))
                    continue
                is_today = self._key(day) == today_key
                is_selected = day == selected_day
                day_events = self._day_events(day)
                event_count = len(day_events)
                weekend = column >= 5
                is_hit = self._key(day) in self.search_hits
                if is_selected:
                    attr = curses.color_pair(_CP_CURSOR) | curses.A_BOLD | curses.A_REVERSE
                elif is_today:
                    attr = curses.color_pair(_CP_TODAY) | curses.A_BOLD
                elif is_hit:
                    attr = curses.color_pair(_CP_MATCH) | curses.A_BOLD
                elif weekend:
                    attr = curses.color_pair(_CP_WEEKEND)
                else:
                    attr = curses.color_pair(_CP_MONTH)
                marker = "" if event_count == 1 \
                    else str(min(event_count, 9))
                cell = f" {day:>2} "
                _safe_addstr(self.stdscr, day_row, x,
                             _clip(cell, width - x), attr)
                if event_count:
                    _safe_addstr(self.stdscr, day_row, x + 3,
                                 _clip(f"{_glyph('bullet')}{marker}",
                                       width - x - 3),
                                 curses.color_pair(_CP_EVENT)
                                 | curses.A_BOLD)

        # side panel: framed box with the selected day's events
        panel_x = 1 + 7 * 5 + 2
        panel_width = width - panel_x - 1
        if panel_width > 18 and height > 8:
            self._draw_side_panel(panel_x, panel_width, height)

        # status bar: segmented (message | counters | search | current day)
        selected_date = self._selected_date()
        day_note = (f" │ selected: {selected_date.isoformat()}"
                    if selected_date else "")
        search_note = (f" │ search: '{self.search_text}' "
                       f"({len(self.search_hits)} day(s))"
                       if self.search_text else "")
        status = (f" {self.message} │ {len(self.events)} day(s) with "
                  f"events │ clipboard: {len(self.clipboard)} event(s)"
                  f"{search_note}{day_note} ")
        _safe_addstr(self.stdscr, height - 2, 0,
                     _clip(status.ljust(width), width),
                     curses.color_pair(_CP_STATUS) | curses.A_BOLD)

        hint = " ‹ › day │ ‹‹ ›› week │ PgUp/PgDn month "
        if width >= 128:
            menu = (" F1 Menu │ A Add │ E Edit │ D Del │ M Move │ Y/W/V "
                    "Copy/Cut/Paste │ F Search │ L Agenda │ T Today │ "
                    "G Go to │ H Help │ Q Quit ")
        elif width >= 100:
            menu = (" F1 Menu │ A Add │ E Edit │ D Del │ Y Copy │ V Paste │ "
                    "F Search │ L Agenda │ T Today │ H Help │ Q Quit ")
        elif width >= 84:
            menu = (" F1 Menu │ A Add │ E Edit │ D Del │ T Today │ "
                    "G Go to │ H Help │ Q Quit ")
        else:
            menu = " F1 Menu │ A Add │ H Help │ Q Quit "
        _safe_addstr(self.stdscr, height - 1, 0,
                     _clip(menu.ljust(width), width),
                     curses.color_pair(_CP_MENU) | curses.A_BOLD)
        # navigation hints at the right edge — only when they truly fit
        hint_x = width - len(hint) - 1
        if width > len(menu) + len(hint) + 2 and hint_x > 0:
            _safe_addstr(self.stdscr, height - 1, hint_x,
                         _clip(hint, len(hint)),
                         curses.color_pair(_CP_HINT) | curses.A_BOLD)

        self.stdscr.refresh()

    def _draw_side_panel(self, panel_x: int, panel_width: int,
                         height: int) -> None:
        """Framed side panel with the selected day's events."""
        date = self._selected_date()
        panel_height = height - 4  # keep clear of the status bar row
        frame_attr = curses.color_pair(_CP_FRAME)
        title = f"{date:%a %d %b %Y}" if date else "Day"
        # top border with an embedded, centred title
        border = "┌─" + "─" * (panel_width - 4) + "─┐"
        _safe_addstr(self.stdscr, 1, panel_x, border, frame_attr)
        tcol = panel_x + max(2, (panel_width - len(title) - 2) // 2)
        _safe_addstr(self.stdscr, 1, tcol,
                     _clip(f" {title} ", panel_width - 4),
                     curses.color_pair(_CP_ACCENT) | curses.A_BOLD)
        for line in range(2, panel_height + 1):
            _safe_addstr(self.stdscr, line, panel_x, "│", frame_attr)
            _safe_addstr(self.stdscr, line, panel_x + panel_width - 1,
                         "│", frame_attr)
        _safe_addstr(self.stdscr, panel_height + 1, panel_x,
                     "└" + "─" * (panel_width - 2) + "┘", frame_attr)
        day_events = self._day_events(date.day) if date else []
        inner = max(0, panel_width - 4)
        if not day_events:
            _safe_addstr(self.stdscr, 3, panel_x + 2,
                         _clip("(no events)", inner),
                         curses.color_pair(_CP_DIM))
        else:
            _safe_addstr(self.stdscr, 2, panel_x + 2,
                         _clip(f"{len(day_events)} event(s):", inner),
                         curses.color_pair(_CP_PANEL))
            visible = max(0, panel_height - 4)
            for index, event in enumerate(day_events[:visible]):
                attr = curses.color_pair(_CP_EVENT)
                if index == 0:
                    attr |= curses.A_BOLD
                _safe_addstr(self.stdscr, 3 + index, panel_x + 2,
                             _clip(f"{_glyph('bullet')} {event}",
                                   inner), attr)
            if len(day_events) > visible:
                _safe_addstr(self.stdscr, panel_height, panel_x + 2,
                             _clip(f"… +{len(day_events) - visible} more",
                                   inner),
                             curses.color_pair(_CP_HINT))
        # context footer inside the panel
        if date and panel_height > 7:
            week = date.isocalendar()[1]
            days_in_month = calendar.monthrange(date.year, date.month)[1]
            progress = int(100 * date.day / days_in_month)
            bar_width = min(20, inner - 20)
            filled = int(bar_width * date.day / days_in_month)
            bar = ("█" * filled + "░" * (bar_width - filled)) \
                if bar_width > 0 else ""
            footer = [
                (f"week {week} │ day {date.day}/{days_in_month}",
                 _CP_PANEL),
                (f"[{bar}] {progress}%", _CP_ACCENT),
            ]
            base = panel_height - 3
            for offset, (text, pair) in enumerate(footer):
                if base + offset <= panel_height - 1:
                    _safe_addstr(self.stdscr, base + offset, panel_x + 2,
                                 _clip(text, inner),
                                 curses.color_pair(pair))

    # ── dialogs ──────────────────────────────────────────────────────────────
    def _fancy_border(self, win) -> None:
        """Rounded-corner border drawn with unicode glyphs."""
        height, width = win.getmaxyx()
        if height < 2 or width < 2:
            return
        attr = curses.color_pair(_CP_FRAME)
        _safe_addstr(win, 0, 0, "╭" + "─" * (width - 2) + "╮", attr)
        for line in range(1, height - 1):
            _safe_addstr(win, line, 0, "│", attr)
            _safe_addstr(win, line, width - 1, "│", attr)
        _safe_addstr(win, height - 1, 0,
                     "╰" + "─" * (width - 2) + "╯", attr)

    def _show_text_dialog(self, title: str, lines,
                          centered: bool = False) -> None:
        height, width = self.stdscr.getmaxyx()
        if height < 3 or width < 4:
            self.stdscr.erase()
            _safe_addstr(self.stdscr, 0, 0, _clip(title, max(0, width - 1)),
                         curses.color_pair(_CP_STATUS))
            self.stdscr.refresh()
            self.stdscr.getch()
            return
        max_width = max(4, width - 2)
        max_height = max(3, height - 2)
        requested_width = max([len(title) + 8]
                              + [len(line) + 6 for line in lines])
        dialog_width = min(max(4, requested_width), max_width)
        dialog_height = min(max(3, len(lines) + 4), max_height)
        win = curses.newwin(dialog_height, dialog_width,
                            max(0, (height - dialog_height) // 2),
                            max(0, (width - dialog_width) // 2))
        win.bkgd(" ", curses.color_pair(_CP_MENU))
        self._fancy_border(win)
        title_attr = curses.color_pair(_CP_MENU_KEY) | curses.A_BOLD
        title_text = f" {title} "
        title_col = max(2, (dialog_width - len(title_text)) // 2)
        _safe_addstr(win, 0, title_col,
                     _clip(title_text, max(0, dialog_width - title_col - 1)),
                     title_attr)
        text_attr = (curses.color_pair(_CP_MENU)
                     if centered else curses.color_pair(_CP_HELP))
        for index, line in enumerate(lines[:max(0, dialog_height - 3)]):
            if centered:
                column = max(3, (dialog_width - len(line)) // 2)
            else:
                column = 3
            _safe_addstr(win, index + 1, column,
                         _clip(line, max(0, dialog_width - 6)), text_attr)
        hint = " Enter / Esc = close "
        _safe_addstr(win, dialog_height - 2,
                     max(2, (dialog_width - len(hint)) // 2),
                     _clip(hint, max(0, dialog_width - 4)),
                     curses.color_pair(_CP_MENU_KEY) | curses.A_BOLD)
        win.refresh()
        while win.getch() not in (27, 10, 13, curses.KEY_ENTER, curses.KEY_F1):
            pass

    def _show_help(self) -> None:
        self._show_text_dialog("Help", [
            "F1        open menu",
            "A         add event to the selected day",
            "E         edit / remove one event",
            "D         delete one event",
            "C         clear every event of the day",
            "X         export all events to CSV",
            "I         import events from CSV",
            "S         show statistics",
            "R         priority overview",
            "T         jump to today",
            "G         jump to date (YYYY-MM-DD)",
            "N / P     next / previous month",
            "Arrows    move between days",
            "PgUp/PgDn previous / next month",
            "Home      first day of the month",
            "Q         quit",
            "Events are stored in ~/.txcal_events.json",
        ])

    def _show_about(self) -> None:
        self._show_text_dialog("About TXCal v1.2", [
            "",
            "  A compact curses month calendar with events",
            "  features: JSON store │ search │ agenda │",
            "  clipboard │ event moving │ today colours",
            "",
            "  author:  Sebastian Januchowski",
            "  company: polsoft.ITS™",
            "  email:   polsoft.its@mail.com",
            "  github:  https://github.com/polsoft-it",
            "",
        ], centered=True)

    def _show_menu(self) -> None:
        height, width = self.stdscr.getmaxyx()
        menu_width = min(24, max(18, width - 4))
        menu_height = min(len(self.menu_items) + 2, max(4, height - 2))
        win = curses.newwin(menu_height, menu_width,
                            max(0, (height - menu_height) // 2),
                            max(0, (width - menu_width) // 2))
        win.bkgd(" ", curses.color_pair(_CP_MENU))
        win.keypad(True)
        selected = 0
        while True:
            win.erase()
            win.border()
            win.addstr(0, 2, " Menu ",
                       curses.color_pair(_CP_MENU_KEY) | curses.A_BOLD)
            visible = menu_height - 2
            for index, (label, _) in enumerate(
                    self.menu_items[:max(0, visible)]):
                attr = (curses.color_pair(_CP_CURSOR) if index == selected
                        else curses.color_pair(_CP_MENU))
                win.addstr(index + 1, 2,
                           f"{label:<{menu_width - 4}}", attr)
            win.refresh()
            key = win.getch()
            if key == curses.KEY_UP:
                selected = (selected - 1) % len(self.menu_items)
            elif key == curses.KEY_DOWN:
                selected = (selected + 1) % len(self.menu_items)
            elif key in (10, 13, curses.KEY_ENTER):
                self._run_menu_action(self.menu_items[selected][1])
                return
            elif key in (27, curses.KEY_F1):
                return

    def _run_menu_action(self, action: str) -> None:
        actions = {
            "add": self._add_event,
            "edit": self._edit_event,
            "delete": self._delete_event,
            "clear_day": self._clear_day,
            "export_csv": self._export_csv,
            "import_csv": self._import_csv,
            "move_event": self._move_event,
            "copy_day": self._copy_day,
            "cut_day": self._cut_day,
            "paste_day": self._paste_day,
            "search": self._search,
            "agenda": self._agenda,
            "stats": self._show_stats,
            "priority": self._priority_view,
            "today": self._go_today,
            "next_month": lambda: self._shift_month(1),
            "prev_month": lambda: self._shift_month(-1),
            "jump": self._jump,
            "help": self._show_help,
            "about": self._show_about,
        }
        if action in actions:
            actions[action]()
        elif action == "quit":
            self.running = False

    def _cut_day(self) -> None:
        self._copy_day(cut=True)

    # ── input ────────────────────────────────────────────────────────────────
    def _handle_command(self, key: int) -> bool:
        commands = {
            curses.KEY_F1: self._show_menu,
            ord("a"): self._add_event,
            ord("A"): self._add_event,
            ord("e"): self._edit_event,
            ord("E"): self._edit_event,
            ord("d"): self._delete_event,
            ord("D"): self._delete_event,
            ord("c"): self._clear_day,
            ord("C"): self._clear_day,
            ord("x"): self._export_csv,
            ord("X"): self._export_csv,
            ord("i"): self._import_csv,
            ord("I"): self._import_csv,
            ord("s"): self._show_stats,
            ord("S"): self._show_stats,
            ord("r"): self._priority_view,
            ord("R"): self._priority_view,
            ord("t"): self._go_today,
            ord("T"): self._go_today,
            ord("g"): self._jump,
            ord("G"): self._jump,
            ord("y"): self._copy_day,
            ord("Y"): self._copy_day,
            ord("w"): self._cut_day,
            ord("W"): self._cut_day,
            ord("v"): self._paste_day,
            ord("V"): self._paste_day,
            ord("m"): self._move_event,
            ord("M"): self._move_event,
            ord("f"): self._search,
            ord("F"): self._search,
            ord("l"): self._agenda,
            ord("L"): self._agenda,
            ord("h"): self._show_help,
            ord("H"): self._show_help,
            ord("n"): lambda: self._shift_month(1),
            ord("N"): lambda: self._shift_month(1),
            ord("p"): lambda: self._shift_month(-1),
            ord("P"): lambda: self._shift_month(-1),
        }
        if key in commands:
            commands[key]()
            return True
        if key == 17:  # Ctrl+Q
            self.running = False
            return True
        return False

    def _handle_navigation(self, key: int) -> bool:
        grid_height = len(self._grid())
        if key == curses.KEY_LEFT:
            self._move_day(-1)
        elif key == curses.KEY_RIGHT:
            self._move_day(1)
        elif key == curses.KEY_UP:
            self._move(-1)
        elif key == curses.KEY_DOWN:
            self._move(1)
        elif key in (curses.KEY_PPAGE, ord("-")):
            self._shift_month(-1)
        elif key in (curses.KEY_NPAGE, ord("+")):
            self._shift_month(1)
        elif key in (ord("\x06"), curses.KEY_FIND):  # Ctrl+F: next hit
            self._next_hit(1)
        elif key == curses.KEY_HOME:
            self.row = self.column = 0
        elif key == curses.KEY_END:
            self.row = grid_height - 1
            self._fix_column()
        else:
            return False
        return True

    def handle(self, key: int) -> None:
        if not self._handle_command(key):
            self._handle_navigation(key)

    def setup(self) -> None:
        """Initialize terminal state and the app's default view."""
        curses.curs_set(0)
        self.stdscr.keypad(True)
        self.stdscr.timeout(100)  # poll so we never block forever on a PTY
        _init_colors()
        self._go_today()

    def render_frame(self) -> None:
        """Draw the current UI state."""
        self.draw()

    def process_input(self) -> None:
        """Read and dispatch a single key event."""
        self.handle(self.stdscr.getch())

    def run(self) -> None:
        self.setup()
        while self.running:
            self.render_frame()
            self.process_input()


def main(argv: Optional[list[str]] = None) -> int:
    store = resolve_store_path(argv)

    def _run(stdscr) -> None:
        try:
            CalendarApp(stdscr, store).run()
        except curses.error:
            pass  # survive terminal quirks (PTY, non-standard emulators)
        except Exception:
            import traceback
            with open(os.path.join(os.path.dirname(store),
                                   "txcal_traceback.log"), "w",
                      encoding="utf-8") as fh:
                traceback.print_exc(file=fh)
            raise

    try:
        curses.wrapper(_run)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
