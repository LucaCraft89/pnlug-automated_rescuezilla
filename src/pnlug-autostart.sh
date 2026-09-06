#!/bin/bash
# Autostart entry point for PNLUG Rescue. Two things point here so it fires
# regardless of which session type actually boots (confirmed live: the
# stock Rescuezilla ISO's live session is plain `openbox-session`, which
# does NOT read /etc/xdg/autostart/*.desktop at all — only a full session
# manager like xfce4-session/gnome-session does. The .desktop entry alone
# silently never ran):
#   - /etc/xdg/autostart/pnlug-autostart.desktop (honored if the session
#     manager ever does support freedesktop autostart)
#   - a line appended straight into /etc/xdg/openbox/autostart by the build
#     script (the actual entry point on this release, confirmed working)
#
# Chain: GUI -> TUI (in a terminal) -> visible error. A normal close (user
# finishes, or just closes the window) exits 0 and is NOT a failure — only
# a GUI that couldn't even start at all falls back.

LOG=/tmp/pnlug-gui-autostart.log
# Remove-then-create rather than truncate: a stale log from a *different*
# user context (confirmed in testing: the live session's autostart runs as
# the login user, e.g. "ubuntu", but a manually-opened root terminal used to
# debug it runs as root) leaves a file root can't truncate-in-place even
# though root can always unlink it (/tmp's sticky bit only gates deletion,
# not root). A failed redirect target isn't just a log miss either — it
# stops the redirected command from running at all, which would otherwise
# silently break the entire GUI/TUI chain below.
rm -f "$LOG"
: > "$LOG"

show_error() {
    local msg="$1"
    echo "$msg" >> "$LOG"
    if command -v zenity >/dev/null 2>&1 && zenity --error --title="PNLUG Rescue" --text="$msg" 2>>"$LOG"; then
        return
    fi
    if command -v xmessage >/dev/null 2>&1 && xmessage -center "$msg" 2>>"$LOG"; then
        return
    fi
    if python3 - "$msg" >>"$LOG" 2>&1 <<'PYEOF'
import sys
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
d = Gtk.MessageDialog(message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, text="PNLUG Rescue couldn't start")
d.format_secondary_text(sys.argv[1])
d.run()
PYEOF
    then
        return
    fi
    # Nothing graphical worked at all — leave a trace the user can actually
    # find without a terminal.
    for home in /root "$HOME" /home/*; do
        [ -d "$home/Desktop" ] && echo "$msg" > "$home/Desktop/PNLUG-RESCUE-ERROR.txt" 2>/dev/null
    done
}

if python3 /usr/bin/pnlug-rescue-gui >>"$LOG" 2>&1; then
    exit 0
fi
echo "GUI failed to start — falling back to the text menu." >> "$LOG"

tui_ok=false
if command -v xfce4-terminal >/dev/null 2>&1; then
    xfce4-terminal --hold -e "python3 /usr/bin/pnlug-rescue-tui" >>"$LOG" 2>&1 && tui_ok=true
elif command -v xterm >/dev/null 2>&1; then
    xterm -hold -e python3 /usr/bin/pnlug-rescue-tui >>"$LOG" 2>&1 && tui_ok=true
else
    echo "No terminal emulator found (looked for xfce4-terminal, xterm)." >> "$LOG"
fi

if [ "$tui_ok" = true ]; then
    exit 0
fi

show_error "PNLUG Rescue couldn't start: neither the graphical tool nor the text-menu fallback could be launched. Details in $LOG — you can also open a terminal and run: python3 /usr/bin/pnlug-rescue-tui"
exit 1
