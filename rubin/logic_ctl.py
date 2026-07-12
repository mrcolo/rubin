"""Drive Logic Pro via AppleScript/System Events.

Only used for the few things a MIDI file can't do by itself:
importing the file into the open project, and transport control.
"""

import os
import subprocess
import time


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


def window_count():
    """Number of Logic windows. Patch loading is unreliable above 1."""
    try:
        return int(osa(
            'tell application "System Events" to tell process "%s" to count windows'
            % process_name(), timeout=15))
    except (LogicError, ValueError):
        return -1


def front_window_title():
    return osa(
        'tell application "System Events" to tell process "%s" to '
        "get name of front window" % process_name()
    )


def import_midi(path):
    """File > Import > MIDI File... then drive the file panel to `path`.

    Logic presents the picker as a WINDOW titled "Import" (not a sheet), and
    it can take a moment to appear — so this polls for it, raises it, and
    clicks its own Import button, rather than firing blind keystrokes on a
    timer (the observed failure mode of the naive flow).
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
        -- poll for a confirmed file panel; NEVER keystroke a path otherwise
        -- (a path's letters fire as key commands in the arrange window)
        set panel to missing value
        repeat 20 times
            delay 0.5
            try
                set panel to window "Import"
                exit repeat
            end try
            try
                if exists text field 1 of sheet 1 of window 1 then
                    set panel to sheet 1 of window 1
                    exit repeat
                end if
            end try
        end repeat
        if panel is missing value then
            key code 53 -- Escape: abort without typing
            error "MIDI-import file panel did not appear - aborted without typing"
        end if
        try
            perform action "AXRaise" of panel
        end try
        delay 0.3
        keystroke "g" using {command down, shift down} -- Go To Folder
        delay 0.8
        keystroke "%(path)s"
        delay 0.5
        key code 36 -- confirm path
        delay 1.0
        try
            click button "Import" of panel
        on error
            key code 36
        end try
        delay 1.5
    end tell
end tell
''' % {
        "app": app_name(),
        "proc": process_name(),
        "path": path.replace("\\", "\\\\").replace('"', '\\"'),
    }
    osa(script, timeout=90)


def find_dialog_buttons():
    """Return button names of a frontmost Logic sheet/dialog, or []."""
    script = '''
tell application "System Events"
    tell process "%s"
        set target to missing value
        if exists sheet 1 of front window then
            set target to sheet 1 of front window
        else
            try
                if subrole of front window is "AXDialog" then set target to front window
            end try
        end if
        if target is missing value then return ""
        set out to ""
        repeat with b in buttons of target
            try
                set nm to name of b
                if nm is not missing value and nm is not "" then set out to out & nm & linefeed
            end try
        end repeat
        return out
    end tell
end tell
''' % process_name()
    out = osa(script)
    return [b for b in out.splitlines() if b.strip()] if out else []


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


def _file_panel_frontmost():
    """True only if a file open/save panel (sheet or window) is frontmost —
    the ONLY safe target for path keystrokes. Anything else and typing a path
    leaks into the arrange window as key commands (r=record, etc.)."""
    script = '''
tell application "System Events" to tell process "%s"
    try
        if exists sheet 1 of window 1 then
            if exists text field 1 of sheet 1 of window 1 then return "yes"
        end if
    end try
    repeat with w in windows
        try
            if name of w is in {"Open", "Import"} then return "yes"
        end try
    end repeat
    return "no"
end tell
''' % process_name()
    return osa(script, timeout=15) == "yes"


def import_audio(path):
    """File > Import > Audio File..., driven SAFELY to `path`.

    Hard rule learned the hard way: never keystroke a path unless a file panel
    is confirmed frontmost. If the panel doesn't appear, abort with the menu
    still harmlessly open rather than typing into the arrange window (where a
    path's letters fire as key commands and can start a recording).
    """
    if not logic_running():
        raise LogicError("Logic Pro is not running")
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        raise LogicError("no such audio file: %s" % path)
    # open the menu item
    osa('''
tell application "%(app)s" to activate
delay 0.5
tell application "System Events" to tell process "%(proc)s"
    set frontmost to true
    set m to menu 1 of menu item "Import" of menu 1 of menu bar item "File" of menu bar 1
    click (first menu item of m whose name begins with "Audio")
end tell
''' % {"app": app_name(), "proc": process_name()}, timeout=30)
    # poll for the panel; abort (Escape) if it never shows — do NOT type blind
    for _ in range(12):
        time.sleep(0.5)
        if _file_panel_frontmost():
            break
    else:
        osa('tell application "System Events" to tell process "%s" to key code 53'
            % process_name())
        raise LogicError(
            "audio-import file panel did not appear — aborted without typing "
            "(refusing to leak a path into the arrange window)")
    # panel confirmed: set the Go-to-folder field by value, not blind keystroke
    osa('''
tell application "System Events" to tell process "%(proc)s"
    keystroke "g" using {command down, shift down}
    delay 0.8
    set tf to text field 1 of sheet 1 of window 1
    set value of tf to "%(path)s"
    delay 0.3
    key code 36
    delay 0.9
    key code 36
    delay 1.2
end tell
''' % {"proc": process_name(),
        "path": path.replace("\\", "\\\\").replace('"', '\\"')}, timeout=45)


def project_state():
    """Disambiguate the states that all looked like 'window_count 0' this far:
    not_running / no_project (display may be asleep, or no doc open) /
    project_open. Returns a dict a caller can branch on before acting."""
    if not logic_running():
        return {"state": "not_running", "windows": 0}
    n = window_count()
    if n < 1:
        return {"state": "no_project",
                "windows": n,
                "hint": "no project window — the display may be asleep, or no "
                        "document is open. UI tools (import, patches) need a "
                        "project window; wake the screen / open a project."}
    try:
        title = front_window_title()
    except LogicError:
        title = None
    return {"state": "project_open", "windows": n, "front_window": title}


def reveal_in_finder(path):
    """Reveal a file or folder in Finder, selected and ready to drag. Safe:
    touches only Finder, never Logic's arrange window. This is the reliable
    way to get audio samples into a project — drag from here onto a track."""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        raise LogicError("no such path: %s" % path)
    subprocess.run(["open", "-R", path], check=True)
    return path


def open_midi_as_project(path):
    """Open a .mid directly in Logic — creates a new project from the file.

    Reliable when no project window is open (no menus or file dialogs involved).
    """
    subprocess.run(["open", "-a", app_name(), path], check=True)


_track_container = None


def _find_bounded_container(probe_snippet):
    """Return the first 'group N of window 1' whose subtree satisfies the probe.

    Scanning the whole window explodes on real projects (every region/note is
    an AX element); per-group subtrees stay small.
    """
    for i in range(1, 7):
        pth = "group %d of window 1" % i
        script = '''
tell application "System Events" to tell process "%s"
    try
        with timeout of 25 seconds
            set els to entire contents of (%s)
            repeat with el in els
                try
                    %s
                end try
            end repeat
            return "no"
        end timeout
    on error
        return "no"
    end try
end tell
''' % (process_name(), pth, probe_snippet)
        try:
            if osa(script, timeout=35) == "yes":
                return pth
        except LogicError:
            continue
    return None


def _track_rows():
    """Bounded scan of the track-header container.

    Returns [(x, y, desc)] for the 18px-tall header text fields.
    """
    global _track_container
    if not _track_container:
        _track_container = _find_bounded_container(
            'if role of el is "AXTextField" then\n'
            '                        set sz to size of el\n'
            '                        if (item 2 of sz is 18) and (item 1 of sz > 100) '
            'then return "yes"\n'
            '                    end if'
        )
        if not _track_container:
            raise LogicError("track headers not found — is a project open?")
    script = '''
with timeout of 60 seconds
    tell application "System Events"
        tell process "%s"
            set out to ""
            set allEls to entire contents of (%s)
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
''' % (process_name(), _track_container)
    fields = []
    for line in osa(script, timeout=90).splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            fields.append((int(parts[0]), int(parts[1]), parts[2]))
    return fields


def select_track(index):
    """Select track `index` (1-based) by keyboard walk: Up to the top, then
    Down. Header clicks proved unreliable (columns can sit off-screen and
    click targets drift); arrow keys always operate on the real selection."""
    index = int(index)
    try:
        n_tracks = len({y for _x, y, _d in _track_rows()}) or 12
    except LogicError:
        n_tracks = 12
    if not 1 <= index <= max(n_tracks, 1):
        raise LogicError("track index %d out of range (project has %d)" % (index, n_tracks))
    ups = "\n".join(["        key code 126\n        delay 0.05"] * (n_tracks + 2))
    downs = "\n".join(["        key code 125\n        delay 0.08"] * (index - 1))
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
    osa(script, timeout=120)


_lib_container = None


def _library_container():
    """Locate the window group holding the Library (bounded AX subtree).

    Scanning the whole window explodes on real projects (every region/note is
    an AX element); the Library subtree stays small. Cached per process.
    """
    global _lib_container
    if _lib_container:
        return _lib_container
    for i in range(1, 7):
        pth = "group %d of window 1" % i
        script = '''
tell application "System Events" to tell process "%s"
    try
        with timeout of 25 seconds
            set els to entire contents of (%s)
            repeat with el in els
                try
                    if role of el is "AXTextField" then
                        if (value of attribute "AXPlaceholderValue" of el) is "Search Sounds" then return "yes"
                    end if
                end try
            end repeat
            return "no"
        end timeout
    on error
        return "no"
    end try
end tell
''' % (process_name(), pth)
        try:
            if osa(script, timeout=35) == "yes":
                _lib_container = pth
                return pth
        except LogicError:
            continue
    raise LogicError("Library pane not found — is the Library open (Y)?")


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
    if was_open != 1:
        activate()
        osa('tell application "System Events" to tell process "%s" to keystroke "y"'
            % process_name())
        import time as _time

        _time.sleep(1.2)
        open_lib = ""
        was_open = 0
    container = _library_container()
    script = '''
tell application "%(app)s" to activate
delay 0.5
tell application "System Events"
    tell process "%(proc)s"
        set frontmost to true
        %(open_lib)s
        -- find the Library search field by its placeholder (bounded subtree)
        set sf to missing value
        with timeout of 60 seconds
            set allEls to entire contents of (%(container)s)
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
        -- collect result rows; retry until names extract (list may still be
        -- populating), prefer an exact name match, else take the first
        set theRow to missing value
        set firstRow to missing value
        repeat with attempt from 1 to 3
            set firstRow to missing value
            set namedCount to 0
            with timeout of 60 seconds
                set allEls2 to entire contents of (%(container)s)
                repeat with el in allEls2
                    try
                        if role of el is "AXRow" then
                            set p to position of el
                            if (item 1 of p) < 300 and (item 2 of p) > (sfY + 10) then
                                if firstRow is missing value then set firstRow to el
                                try
                                    set rn to value of static text 1 of UI element 1 of el
                                    set namedCount to namedCount + 1
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
            if theRow is not missing value then exit repeat
            if namedCount > 0 and attempt > 1 then exit repeat
            delay 0.8
        end repeat
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
        "container": container,
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
    """Read tracks from the 18px-tall header text fields (bounded scan).

    Two columns exist per track row: the header's patch/instrument label and
    the track name. Returns [{"name": ..., "patch": ...}] top to bottom;
    the visible text lives in the AX `description` attribute. Note Logic
    auto-renames a track to its patch name after load_patch unless the track
    was named manually.
    """
    fields = _track_rows()
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


def save_project():
    """Cmd+S. For a never-saved project a Save sheet appears — reported back
    so the caller can fill it via answer_dialog or leave it to the user."""
    script = '''
tell application "%s" to activate
delay 0.4
tell application "System Events"
    tell process "%s"
        set frontmost to true
        keystroke "s" using {command down}
    end tell
end tell
''' % (app_name(), process_name())
    osa(script)
    import time as _time

    _time.sleep(1.0)
    return find_dialog_buttons()


_strip_container = None


def selected_strip_name():
    """Ground truth for 'what patch is on the selected track': the channel
    strip's layout-item name in the inspector. Track-header text can sit in
    an off-screen column and lie; the strip cannot."""
    global _strip_container
    if not _strip_container:
        _strip_container = _find_bounded_container(
            'if role of el is "AXLayoutItem" then\n'
            '                        if (description of el) is "Stereo Out" '
            'then return "yes"\n'
            '                    end if'
        )
        if not _strip_container:
            raise LogicError("inspector channel strip not found - open the inspector (I)")
    script = '''
tell application "System Events" to tell process "%s"
    with timeout of 30 seconds
        set els to entire contents of (%s)
        repeat with el in els
            try
                if role of el is "AXLayoutItem" then
                    set d to description of el
                    if d is not missing value and d is not "Stereo Out" then return d
                end if
            end try
        end repeat
        return ""
    end timeout
end tell
''' % (process_name(), _strip_container)
    return osa(script, timeout=45)


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


def list_audio_units():
    """Installed Audio Units from the component registry (auval -a).

    Returns [{"type": "instrument"|"effect"|..., "manufacturer": ..., "name": ...}].
    Includes third-party synths (Surge XT, Dexed, Vital...) — anything Logic
    can host. No UI involved.
    """
    p = subprocess.run(["auval", "-a"], capture_output=True, text=True, timeout=60)
    return _parse_auval(p.stdout)


def _parse_auval(text):
    kinds = {"aumu": "instrument", "aufx": "effect", "aumf": "music_effect",
             "augn": "generator"}
    out = []
    for line in text.splitlines():
        seg = line.strip().split("  -  ", 1)
        if len(seg) == 2 and len(seg[0].split()) == 3:
            kind = seg[0].split()[0]
            if kind not in kinds:
                continue
            manu_name = seg[1].split(": ", 1)
            out.append({
                "type": kinds[kind],
                "manufacturer": manu_name[0] if len(manu_name) == 2 else "",
                "name": manu_name[1] if len(manu_name) == 2 else seg[1],
            })
    return out
