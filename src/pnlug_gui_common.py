#!/usr/bin/env python3
"""
Shared GTK3 scaffolding for the PNLUG restore/backup GUIs — logo header,
confirmation dialog, streaming multi-stage progress+log page, and the done
page. One source of truth so both apps look and behave identically.
"""
import os
import statistics
import subprocess
import threading
import time
import traceback
from collections import deque

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk

import pnlug_rescue_lib as lib

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_CANDIDATES = [
    "/usr/share/pixmaps/pnluglogo.png",
    os.path.join(_SCRIPT_DIR, "PNLug_marchio-circolare.png"),
    os.path.join(_SCRIPT_DIR, "..", "PNLug_marchio-circolare.png"),
]


def find_logo():
    for path in LOGO_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def build_header(title_text: str) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16, margin=12)
    logo_path = find_logo()
    if logo_path:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(logo_path, -1, 80, True)
        header.pack_start(Gtk.Image.new_from_pixbuf(pixbuf), False, False, 0)
    title = Gtk.Label()
    title.set_markup(f"<span size='xx-large' weight='bold'>{GLib.markup_escape_text(title_text)}</span>")
    header.pack_start(title, False, False, 0)
    box.pack_start(header, False, False, 0)
    box.pack_start(Gtk.Separator(), False, False, 0)
    return box


def confirm_dialog(parent, question: str, disk: "lib.Disk", extra: str = "") -> bool:
    dialog = Gtk.MessageDialog(
        transient_for=parent, modal=True, message_type=Gtk.MessageType.WARNING,
        buttons=Gtk.ButtonsType.YES_NO, text=question)
    detail = f"Model: {disk.model or 'unknown'}"
    if disk.distro:
        detail += f"\nCurrently has: {disk.distro}"
    dialog.format_secondary_text(f"{detail}\nSize: {disk.size_human}\n\n{extra}".rstrip())
    response = dialog.run()
    dialog.destroy()
    return response == Gtk.ResponseType.YES


def confirm_yes_no(parent, question: str, extra: str = "") -> bool:
    """Plain yes/no confirmation, no disk details — used by the Cancel button."""
    dialog = Gtk.MessageDialog(
        transient_for=parent, modal=True, message_type=Gtk.MessageType.WARNING,
        buttons=Gtk.ButtonsType.YES_NO, text=question)
    if extra:
        dialog.format_secondary_text(extra)
    response = dialog.run()
    dialog.destroy()
    return response == Gtk.ResponseType.YES


def error_dialog(parent, message: str):
    dialog = Gtk.MessageDialog(
        transient_for=parent, modal=True, message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.OK, text="Can't continue")
    dialog.format_secondary_text(message)
    dialog.run()
    dialog.destroy()


STAGE_MARKERS = lib.STAGE_MARKERS


def _format_hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class CpuRamMeter(Gtk.Box):
    """Compact CPU%/RAM mini-bars, stdlib-only (/proc/stat, /proc/meminfo —
    no psutil dependency), updated once a second."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2, halign=Gtk.Align.END)
        self.cpu_bar = Gtk.ProgressBar(show_text=True, text="CPU —")
        self.ram_bar = Gtk.ProgressBar(show_text=True, text="RAM —")
        for bar in (self.cpu_bar, self.ram_bar):
            bar.set_size_request(150, 16)
            self.pack_start(bar, False, False, 0)
        self._prev_cpu = self._read_cpu_times()
        GLib.timeout_add(1000, self._tick)

    @staticmethod
    def _read_cpu_times():
        with open("/proc/stat") as f:
            nums = list(map(int, f.readline().split()[1:]))
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
        return idle, sum(nums)

    def _tick(self):
        try:
            idle, total = self._read_cpu_times()
            prev_idle, prev_total = self._prev_cpu
            self._prev_cpu = (idle, total)
            d_total = total - prev_total
            pct = 0.0 if d_total <= 0 else max(0.0, min(100.0, 100 * (1 - (idle - prev_idle) / d_total)))
            self.cpu_bar.set_fraction(pct / 100)
            self.cpu_bar.set_text(f"CPU {pct:.0f}%")
        except (OSError, ZeroDivisionError):
            pass
        try:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    info[k] = int(v.strip().split()[0])  # kB
            total_kb = info.get("MemTotal", 0)
            used_kb = max(0, total_kb - info.get("MemAvailable", total_kb))
            self.ram_bar.set_fraction(0 if total_kb <= 0 else used_kb / total_kb)
            self.ram_bar.set_text(f"RAM {used_kb / 1024 / 1024:.1f}/{total_kb / 1024 / 1024:.1f}G")
        except OSError:
            pass
        return True  # keep ticking


class ProgressPage(Gtk.Box):
    """Multi-stage progress: a big overall bar (with a text line for
    percent/speed/ETA and a CPU+RAM meter above it), a stage checklist down
    the left with the active stage's own bar to its right, and a smaller
    streaming log underneath — plus the inline result banner (hidden until
    show_result()) with Cancel/Restart/Close/Copy-log/Save-log.

    The result banner appears *above* the log rather than replacing it with
    a separate page — a failure with only a one-line "it failed" and no way
    to see what actually happened defeats the point of having a verbose log
    at all (confirmed as a real problem: the wrapper-exit-code bugs are
    exactly the kind of thing you need the log to diagnose)."""

    def __init__(self, stages: list):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        self.stages = stages
        self.current_stage = -1
        self.stage_status = ["pending"] * len(stages)  # pending|current|done|skipped|error
        self.start_time = None
        self.cancel_event = None
        self._running = False
        self._remaining_samples = deque(maxlen=5)
        self._last_rate = ""
        self._pulse_source = None

        # ---- top: main bar + meter -----------------------------------
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        main_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.progress = Gtk.ProgressBar(show_text=False)
        self.progress.set_size_request(-1, 32)  # bigger main bar, per request
        main_col.pack_start(self.progress, False, False, 0)
        self.main_label = Gtk.Label(xalign=0, label="Waiting to start...")
        main_col.pack_start(self.main_label, False, False, 0)
        top.pack_start(main_col, True, True, 0)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.cpu_ram = CpuRamMeter()
        side.pack_start(self.cpu_ram, False, False, 0)
        self.cancel_button = Gtk.Button(label="Cancel")
        self.cancel_button.connect("clicked", self._on_cancel_clicked)
        side.pack_start(self.cancel_button, False, False, 0)
        top.pack_start(side, False, False, 0)
        self.pack_start(top, False, False, 0)

        # ---- middle: stage checklist + active-stage detail ------------
        middle = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        stage_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        stage_list.set_size_request(260, -1)
        self._stage_rows = []
        for stage in stages:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            status_lbl = Gtk.Label(label="", xalign=1)
            status_lbl.set_size_request(48, -1)
            name_lbl = Gtk.Label(label=stage.label, xalign=0)
            row.pack_start(status_lbl, False, False, 0)
            row.pack_start(name_lbl, True, True, 0)
            stage_list.pack_start(row, False, False, 0)
            self._stage_rows.append((status_lbl, name_lbl))
        self._refresh_stage_rows()
        middle.pack_start(stage_list, False, False, 0)

        detail_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.stage_detail_label = Gtk.Label(xalign=0, label="")
        detail_col.pack_start(self.stage_detail_label, False, False, 0)
        self.stage_bar = Gtk.ProgressBar(show_text=True)
        detail_col.pack_start(self.stage_bar, False, False, 0)
        middle.pack_start(detail_col, True, True, 0)
        self.pack_start(middle, False, False, 0)

        # ---- result banner ---------------------------------------------
        self.result_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.result_label = Gtk.Label(xalign=0, wrap=True)
        self.result_box.pack_start(self.result_label, False, False, 0)
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        restart = Gtk.Button(label="Restart now")
        restart.connect("clicked", lambda _b: lib._run(["reboot"]))
        later = Gtk.Button(label="Close")
        later.connect("clicked", lambda _b: Gtk.main_quit())
        copy_btn = Gtk.Button(label="Copy log")
        copy_btn.connect("clicked", self._on_copy_log)
        save_btn = Gtk.Button(label="Save log")
        save_btn.connect("clicked", self._on_save_log)
        for b in (restart, later, copy_btn, save_btn):
            btns.pack_start(b, False, False, 0)
        self.result_box.pack_start(btns, False, False, 0)
        self.pack_start(self.result_box, False, False, 0)
        # hide() alone isn't enough: the window's later top-level show_all()
        # recursively shows every descendant, undoing a plain hide() call
        # made here in __init__ (confirmed in testing: the result banner was
        # visible from the very start of every run). no_show_all opts this
        # widget out of that recursive show.
        self.result_box.set_no_show_all(True)
        self.result_box.hide()

        # ---- log (smaller default footprint, still the primary
        # failure-diagnosis surface — everything above is a summary of it) --
        self.log_view = Gtk.TextView(editable=False, monospace=True)
        scroller = Gtk.ScrolledWindow()
        scroller.set_size_request(-1, 160)
        scroller.add(self.log_view)
        self.pack_start(scroller, True, True, 0)

    # ------------------------------------------------------------ lifecycle
    def begin(self) -> threading.Event:
        """Call right before starting the worker thread: resets timing/cancel
        state and returns the Event to pass as restore()/backup()'s
        cancel_event kwarg."""
        self.start_time = time.monotonic()
        self.cancel_event = threading.Event()
        self.cancel_button.set_sensitive(True)
        self.cancel_button.set_label("Cancel")
        # Ticks the elapsed-time portion of main_label once a second on its
        # own — confirmed live in testing that long log-heavy-but-percent-
        # silent stretches (e.g. rescuezilla's own LVM/VG probing before the
        # first partclone line) otherwise froze the whole label, which reads
        # exactly like the hung-UI failure mode this codebase already
        # watches for (see pnlug_rescue_lib's GTK-idle-callback gotcha).
        self._running = True
        GLib.timeout_add(1000, self._tick_elapsed)
        return self.cancel_event

    def _tick_elapsed(self):
        if self.current_stage >= 0:
            self._refresh_main_label()
        return self._running

    def _on_cancel_clicked(self, _btn):
        if not self.cancel_event or self.cancel_event.is_set():
            return
        if not confirm_yes_no(self.get_toplevel(), "Cancel this operation?",
                               "This is a best-effort mid-write stop — the disk may be left partially "
                               "written. Only use this if you're sure."):
            return
        self.cancel_event.set()
        self.cancel_button.set_sensitive(False)
        self.cancel_button.set_label("Cancelling...")

    # ------------------------------------------------------------- logging
    def append_log(self, line: str):
        buf = self.log_view.get_buffer()
        buf.insert(buf.get_end_iter(), line + "\n")
        self.log_view.scroll_to_iter(buf.get_end_iter(), 0, False, 0, 0)
        try:
            self._process_line(line)
        except Exception:  # noqa: BLE001 - see the note above append_log's call site
            # An exception raised inside a GLib.idle_add callback is
            # swallowed by GLib silently (confirmed elsewhere in this
            # codebase as a real "the whole UI just freezes with zero error
            # output" failure mode) — never let a bad log line (however
            # unexpected) take the stage tracker down with it. Printed, not
            # logged through self.log_threadsafe, to avoid re-entering this
            # same method from inside its own exception handler.
            print(f"stage tracker error on line {line!r}:\n{traceback.format_exc()}", flush=True)

    def log_threadsafe(self, line: str):
        print(line, flush=True)  # also on stdout, for debugging without opening the app
        GLib.idle_add(self.append_log, line)

    # --------------------------------------------------------- stage logic
    def _process_line(self, line: str):
        for idx in range(self.current_stage + 1, len(self.stages)):
            if self.stages[idx].start_marker in line:
                self._advance_to_stage(idx)
                break
        if self.current_stage < 0:
            return

        stage = self.stages[self.current_stage]
        pc = lib.parse_partclone_progress(line)
        percent = None
        if pc:
            percent = pc.percent
            if pc.remaining_s is not None:
                self._remaining_samples.append(pc.remaining_s)
            if pc.rate:
                self._last_rate = pc.rate
        elif stage.measurable:
            percent = lib.parse_percent_only(line)
        if percent is not None:
            self._update_progress(percent)

    def _advance_to_stage(self, idx: int):
        for i in range(self.current_stage, idx):
            if i >= 0:
                self.stage_status[i] = "done"
        self.current_stage = idx
        self.stage_status[idx] = "current"
        stage = self.stages[idx]
        self.stage_detail_label.set_text(stage.label)
        self._stop_pulse()
        if stage.measurable:
            self.stage_bar.set_fraction(0.0)
            self.stage_bar.set_text("0%")
        else:
            self._start_pulse()
        self._refresh_stage_rows()
        self._update_progress(0.0 if stage.measurable else None)

    def _base_fraction(self) -> float:
        return sum(s.weight for s, status in zip(self.stages, self.stage_status) if status == "done")

    def _update_progress(self, stage_percent):
        stage = self.stages[self.current_stage]
        base = self._base_fraction()
        if stage_percent is not None:
            frac = base + stage.weight * (stage_percent / 100.0)
            self.stage_bar.set_fraction(stage_percent / 100.0)
            self.stage_bar.set_text(f"{stage_percent:.0f}%")
            self._stage_rows[self.current_stage][0].set_markup(f"{stage_percent:.0f}%")
        else:
            frac = base
        self.progress.set_fraction(min(1.0, frac))
        self._refresh_main_label(frac)

    def _refresh_main_label(self, frac=None):
        """Rebuilds the "NN% · rate · ETA · elapsed" line. Called both from
        _update_progress (fresh data) and once a second from _tick_elapsed
        (so "elapsed" keeps moving even through log-heavy-but-percent-silent
        stretches — see begin())."""
        if frac is None:
            frac = self.progress.get_fraction()
        bits = [f"{min(100, frac * 100):.0f}%"]
        if self._last_rate:
            bits.append(self._last_rate)
        if self._remaining_samples:
            eta = statistics.mean(self._remaining_samples)
            bits.append(f"ETA {_format_hms(eta)}")
        if self.start_time:
            bits.append(f"elapsed {_format_hms(time.monotonic() - self.start_time)}")
        self.main_label.set_text(" · ".join(bits))

    def _start_pulse(self):
        def pulse():
            self.stage_bar.pulse()
            return True
        self._pulse_source = GLib.timeout_add(120, pulse)

    def _stop_pulse(self):
        if self._pulse_source is not None:
            GLib.source_remove(self._pulse_source)
            self._pulse_source = None

    def _refresh_stage_rows(self):
        icons = {"pending": "", "current": "●", "done": "✓", "skipped": "–", "error": "✗"}
        for idx, (status_lbl, name_lbl) in enumerate(self._stage_rows):
            status = self.stage_status[idx]
            if status == "current" and self.stages[idx].measurable:
                pass  # left as-is — _update_progress fills in the live %
            else:
                status_lbl.set_markup(icons[status])
            dim = status in ("pending", "skipped")
            name_lbl.set_markup(f"<span alpha='50%'>{GLib.markup_escape_text(self.stages[idx].label)}</span>"
                                 if dim else GLib.markup_escape_text(self.stages[idx].label))

    # ---------------------------------------------------------------- done
    def show_result(self, ok: bool, message: str):
        self._running = False
        self._stop_pulse()
        self.cancel_button.hide()
        for idx in range(len(self.stages)):
            if self.stage_status[idx] == "pending":
                self.stage_status[idx] = "skipped"
            elif self.stage_status[idx] == "current":
                self.stage_status[idx] = "done" if ok else "error"
        self._refresh_stage_rows()
        self.progress.set_fraction(1.0 if ok else self.progress.get_fraction())

        elapsed = _format_hms(time.monotonic() - self.start_time) if self.start_time else "?"
        icon = "✅" if ok else "⚠️"
        full_message = f"{message}\n\nTotal time: {elapsed}"
        self.result_label.set_markup(f"<span size='large'>{icon} {GLib.markup_escape_text(full_message)}</span>")
        self.result_box.set_no_show_all(False)
        self.result_box.show_all()

        try:
            Gdk.beep()
        except Exception:  # noqa: BLE001 - purely cosmetic, never worth failing over
            pass
        try:
            subprocess.Popen(["notify-send", "PNLUG Rescue", message.splitlines()[0]])
        except OSError:
            pass  # notify-send not installed on this live image — fine, Gdk.beep() above still fired

    def _log_text(self) -> str:
        buf = self.log_view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)

    def _on_copy_log(self, _btn):
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(self._log_text(), -1)

    def _on_save_log(self, _btn):
        path = f"/root/pnlug-log-{int(time.time())}.txt"
        try:
            with open(path, "w") as f:
                f.write(self._log_text())
            self.result_label.set_text(self.result_label.get_text() + f"\n\nLog saved to {path}")
        except OSError as exc:
            error_dialog(self.get_toplevel(), f"Could not save log: {exc}")


def build_disk_list_page(on_continue) -> tuple[Gtk.Box, Gtk.ListBox]:
    """A page with a ListBox of disks (populated by the caller) and a
    Continue button. Returns (page, listbox) so the caller can fill rows in."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
    listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
    scroller = Gtk.ScrolledWindow()
    scroller.add(listbox)
    box.pack_start(scroller, True, True, 0)
    go = Gtk.Button(label="Continue")
    go.get_style_context().add_class("suggested-action")
    go.connect("clicked", on_continue)
    box.pack_start(go, False, False, 0)
    return box, listbox


def populate_disk_list(listbox: Gtk.ListBox, disks: list, recommended) -> Gtk.ListBoxRow:
    """Fill `listbox` with one row per disk, bolding+marking the recommended
    one and pre-selecting it. Returns the pre-selected row (or None)."""
    for child in list(listbox.get_children()):
        listbox.remove(child)
    first_row = None
    for disk in disks:
        label = f"{disk.path} — {disk.label} — {disk.size_human}"
        if disk is recommended:
            label += "  (recommended)"
        row = Gtk.ListBoxRow()
        row.disk = disk
        lbl = Gtk.Label(label=label, xalign=0, margin=6)
        if disk is recommended:
            lbl.set_markup(f"<b>{GLib.markup_escape_text(label)}</b>")
        row.add(lbl)
        listbox.add(row)
        if disk is recommended:
            first_row = row
    listbox.show_all()
    if first_row:
        listbox.select_row(first_row)
    return first_row
