"""Drive Logic Pro via AppleScript/System Events.

Only used for the few things a MIDI file can't do by itself:
importing the file into the open project, and transport control.
"""

import subprocess


class LogicError(RuntimeError):
    pass


_APP_CANDIDATES = ("Logic Pro", "Logic Pro X")
_app_name = None


def osa(script, timeout=45):
    p = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, timeout=timeout
    )
    if p.returncode != 0:
        raise LogicError(p.stderr.strip() or "osascript failed")
    return p.stdout.strip()


def app_name():
    """Resolve the installed app's name ('Logic Pro' vs 'Logic Pro X')."""
    global _app_name
    if _app_name is None:
        import os

        for cand in _APP_CANDIDATES:
            if os.path.isdir("/Applications/%s.app" % cand):
                _app_name = cand
                break
        else:
            _app_name = _APP_CANDIDATES[0]
    return _app_name


def logic_running():
    names = osa('tell application "System Events" to get name of processes')
    return any(cand in names.split(", ") for cand in _APP_CANDIDATES)


def process_name():
    names = osa('tell application "System Events" to get name of processes').split(", ")
    for cand in _APP_CANDIDATES:
        if cand in names:
            return cand
    return app_name()


def activate():
    osa('tell application "%s" to activate\ndelay 0.5' % app_name())


def front_window_title():
    return osa(
        'tell application "System Events" to tell process "%s" to '
        "get name of front window" % process_name()
    )


def import_midi(path):
    """File > Import > MIDI File... then drive the open panel to `path`.

    Import lands at the playhead, so we first send Return (Go to Beginning).
    """
    if not logic_running():
        raise LogicError("Logic Pro is not running")
    script = '''
tell application "%(app)s" to activate
delay 0.6
tell application "System Events"
    tell process "%(proc)s"
        set frontmost to true
        delay 0.3
        key code 36 -- Return: go to beginning so import lands at bar 1
        delay 0.4
        set importMenu to menu 1 of menu item "Import" of menu 1 of menu bar item "File" of menu bar 1
        set midiItem to (first menu item of importMenu whose name begins with "MIDI")
        click midiItem
        delay 1.2
        keystroke "g" using {command down, shift down} -- Go To Folder sheet
        delay 0.7
        keystroke "%(path)s"
        delay 0.5
        key code 36 -- confirm path
        delay 1.0
        key code 36 -- click Import/Open
        delay 1.5
    end tell
end tell
''' % {
        "app": app_name(),
        "proc": process_name(),
        "path": path.replace("\\", "\\\\").replace('"', '\\"'),
    }
    osa(script, timeout=60)


def find_dialog_buttons():
    """Return button names of a frontmost Logic sheet/dialog, or []."""
    script = '''
tell application "System Events"
    tell process "%s"
        if exists sheet 1 of front window then
            return name of buttons of sheet 1 of front window as string
        end if
        try
            if subrole of front window is "AXDialog" then
                return name of buttons of front window as string
            end if
        end try
        return ""
    end tell
end tell
''' % process_name()
    out = osa(script)
    return [b for b in out.split(", ") if b] if out else []


def answer_dialog(preferred=("Import Tempo", "Import", "Yes", "OK")):
    """Click the best-matching button on an open sheet/dialog. Returns clicked name or None."""
    buttons = find_dialog_buttons()
    if not buttons:
        return None
    choice = None
    for want in preferred:
        for b in buttons:
            if want.lower() in b.lower():
                choice = b
                break
        if choice:
            break
    if choice is None:
        choice = buttons[-1]  # default button is usually rightmost
    script = '''
tell application "System Events"
    tell process "%s"
        if exists sheet 1 of front window then
            click button "%s" of sheet 1 of front window
        else
            click button "%s" of front window
        end if
    end tell
end tell
''' % (process_name(), choice, choice)
    osa(script)
    return choice


def open_midi_as_project(path):
    """Open a .mid directly in Logic — creates a new project from the file.

    Reliable when no project window is open (no menus or file dialogs involved).
    """
    subprocess.run(["open", "-a", app_name(), path], check=True)


def select_track(index):
    """Select track `index` (1-based) via arrow-key navigation.

    Logic has no direct 'select track N' command, so walk to the top with
    Up presses, then step Down. Bounded, deterministic, works on any project
    with a reasonable track count.
    """
    if not 1 <= int(index) <= 60:
        raise LogicError("track index out of range (1-60)")
    ups = "\n".join(["        key code 126\n        delay 0.05"] * 60)
    downs = "\n".join(["        key code 125\n        delay 0.05"] * (int(index) - 1))
    script = '''
tell application "%s" to activate
delay 0.4
tell application "System Events"
    tell process "%s"
        set frontmost to true
%s
%s
    end tell
end tell
''' % (app_name(), process_name(), ups, downs)
    osa(script, timeout=90)


def _library_checkbox_value():
    """Read the Library toolbar toggle (1 = open). Returns int or None.

    The toggle lives one group below the window in the AX tree.
    """
    script = '''
tell application "System Events"
    tell process "%s"
        try
            return value of (checkboxes of UI elements of window 1 whose name is "Library") as string
        end try
        return ""
    end tell
end tell
''' % process_name()
    out = osa(script)
    return int(out) if out.isdigit() else None


def load_patch(query):
    """Load a Library patch onto the selected track by search.

    Opens the Library if needed, fills its search field (identified by the
    'Search Sounds' placeholder), and loads the top result. This is the only
    route to Alchemy/synth patches — Logic exposes no API for patch loading.
    """
    if not logic_running():
        raise LogicError("Logic Pro is not running")
    was_open = _library_checkbox_value()
    open_lib = "" if was_open == 1 else 'keystroke "y"\n        delay 1.2'
    script = '''
tell application "%(app)s" to activate
delay 0.5
tell application "System Events"
    tell process "%(proc)s"
        set frontmost to true
        %(open_lib)s
        -- find the Library search field by its placeholder
        set sf to missing value
        with timeout of 60 seconds
            set allEls to entire contents of window 1
            repeat with el in allEls
                try
                    if role of el is "AXTextField" then
                        if (value of attribute "AXPlaceholderValue" of el) is "Search Sounds" then
                            set sf to el
                            exit repeat
                        end if
                    end if
                end try
            end repeat
        end timeout
        if sf is missing value then error "Library search field not found"
        set sfPos to position of sf
        set sfY to item 2 of sfPos
        set focused of sf to true
        delay 0.3
        set value of sf to "%(query)s"
        delay 0.2
        key code 36 -- run search
        delay 1.5
        -- collect result rows; prefer an exact name match, else take the first
        set theRow to missing value
        set firstRow to missing value
        with timeout of 60 seconds
            set allEls2 to entire contents of window 1
            repeat with el in allEls2
                try
                    if role of el is "AXRow" then
                        set p to position of el
                        if (item 1 of p) < 300 and (item 2 of p) > (sfY + 10) then
                            if firstRow is missing value then set firstRow to el
                            try
                                set rn to value of static text 1 of UI element 1 of el
                                ignoring case
                                    if rn is "%(query)s" then
                                        set theRow to el
                                        exit repeat
                                    end if
                                end ignoring
                            end try
                        end if
                    end if
                end try
            end repeat
        end timeout
        if theRow is missing value then set theRow to firstRow
        if theRow is missing value then error "no Library results for '%(query)s'"
        set patchName to ""
        try
            set patchName to value of static text 1 of UI element 1 of theRow
        end try
        set selected of theRow to true
        delay 1.2
        key code 53 -- escape: release search focus
        return patchName
    end tell
end tell
''' % {
        "app": app_name(),
        "proc": process_name(),
        "open_lib": open_lib,
        "query": query.replace("\\", "\\\\").replace('"', '\\"'),
    }
    loaded = osa(script, timeout=180)
    if was_open == 0:
        # restore the Library to closed
        transport_script = '''
tell application "System Events" to tell process "%s"
    keystroke "y"
end tell
''' % process_name()
        try:
            osa(transport_script)
        except LogicError:
            pass
    return loaded


def list_tracks():
    """Read tracks from the 18px-tall header text fields.

    Two columns exist per track row: the header's patch/instrument label and
    the track name. Returns [{"name": ..., "patch": ...}] top to bottom;
    the visible text lives in the AX `description` attribute.
    """
    script = '''
with timeout of 60 seconds
    tell application "System Events"
        tell process "%s"
            set out to ""
            set allEls to entire contents of window 1
            repeat with el in allEls
                try
                    if role of el is "AXTextField" then
                        set s to size of el
                        if (item 2 of s is 18) and (item 1 of s > 100) then
                            set p to position of el
                            set out to out & (item 1 of p) & "|" & (item 2 of p) & "|" & (description of el) & linefeed
                        end if
                    end if
                end try
            end repeat
            return out
        end tell
    end tell
end timeout
''' % process_name()
    out = osa(script, timeout=90)
    fields = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            fields.append((int(parts[0]), int(parts[1]), parts[2]))
    if not fields:
        return []
    xs = sorted({x for x, _, _ in fields})
    left_col = [f for f in fields if f[0] == xs[0]]
    right_col = [f for f in fields if f[0] == xs[-1]] if len(xs) > 1 else []
    left_col.sort(key=lambda f: f[1])
    right_col.sort(key=lambda f: f[1])
    tracks = []
    for i, (_, _, patch) in enumerate(left_col):
        name = right_col[i][2] if i < len(right_col) else patch
        tracks.append({"name": name, "patch": patch})
    return tracks


_TRANSPORT_KEYS = {
    "play": "key code 49",           # space toggles play/stop
    "stop": "key code 49",
    "record": 'keystroke "r"',
    "go_to_beginning": "key code 36",  # Return
}


def transport(command):
    key = _TRANSPORT_KEYS.get(command)
    if key is None:
        raise LogicError(
            "unknown transport command %r; use one of %s"
            % (command, sorted(_TRANSPORT_KEYS))
        )
    script = '''
tell application "%s" to activate
delay 0.4
tell application "System Events"
    tell process "%s"
        set frontmost to true
        %s
    end tell
end tell
''' % (app_name(), process_name(), key)
    osa(script)
