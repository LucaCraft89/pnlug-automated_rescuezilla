#!/usr/bin/env python3
"""
Shared GTK3 scaffolding for the PNLUG restore/backup GUIs — logo header,
confirmation dialog, streaming progress+log page, and the done page. One
source of truth so both apps look and behave identically.
"""
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, GdkPixbuf

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


def error_dialog(parent, message: str):
    dialog = Gtk.MessageDialog(
        transient_for=parent, modal=True, message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.OK, text="Can't continue")
    dialog.format_secondary_text(message)
    dialog.run()
    dialog.destroy()


STAGE_MARKERS = lib.STAGE_MARKERS


class ProgressPage(Gtk.Box):
    """A progress bar + streaming, auto-scrolling monospace log view, plus an
    inline result banner (hidden until show_result()) with Restart/Close
    buttons.

    The result banner appears *above* the log rather than replacing it with
    a separate page — a failure with only a one-line "it failed" and no way
    to see what actually happened defeats the point of having a verbose log
    at all (confirmed as a real problem: the wrapper-exit-code bugs below
    are exactly the kind of thing you need the log to diagnose)."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        self.progress = Gtk.ProgressBar(show_text=True)
        self.pack_start(self.progress, False, False, 0)

        self.result_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.result_label = Gtk.Label(xalign=0, wrap=True)
        self.result_box.pack_start(self.result_label, False, False, 0)
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        restart = Gtk.Button(label="Restart now")
        restart.connect("clicked", lambda _b: lib._run(["reboot"]))
        later = Gtk.Button(label="Close")
        later.connect("clicked", lambda _b: Gtk.main_quit())
        btns.pack_start(restart, False, False, 0)
        btns.pack_start(later, False, False, 0)
        self.result_box.pack_start(btns, False, False, 0)
        self.pack_start(self.result_box, False, False, 0)
        # hide() alone isn't enough: the window's later top-level show_all()
        # recursively shows every descendant, undoing a plain hide() call
        # made here in __init__ (confirmed in testing: the Restart/Close
        # banner was visible from the very start of every run). no_show_all
        # opts this widget out of that recursive show.
        self.result_box.set_no_show_all(True)
        self.result_box.hide()

        self.log_view = Gtk.TextView(editable=False, monospace=True)
        scroller = Gtk.ScrolledWindow()
        scroller.add(self.log_view)
        self.pack_start(scroller, True, True, 0)

    def append_log(self, line: str):
        buf = self.log_view.get_buffer()
        buf.insert(buf.get_end_iter(), line + "\n")
        self.log_view.scroll_to_iter(buf.get_end_iter(), 0, False, 0, 0)
        for marker, fraction in STAGE_MARKERS:
            if marker in line:
                self.progress.set_fraction(fraction)
                break

    def log_threadsafe(self, line: str):
        print(line, flush=True)  # also on stdout, for debugging without opening the app
        GLib.idle_add(self.append_log, line)

    def show_result(self, ok: bool, message: str):
        self.progress.set_fraction(1.0)
        icon = "✅" if ok else "⚠️"
        self.result_label.set_markup(f"<span size='large'>{icon} {GLib.markup_escape_text(message)}</span>")
        self.result_box.set_no_show_all(False)
        self.result_box.show_all()


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
