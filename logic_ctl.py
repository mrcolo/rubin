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
    """Select track `index` (1-based) by clicking its header row."""
    index = int(index)
    fields = _track_rows()
    if not fields:
        raise LogicError("no track headers visible")
    xs = sorted({x for x, _, _ in fields})
    col = sorted((f for f in fields if f[0] == xs[0]), key=lambda f: f[1])
    if not 1 <= index <= len(col):
        raise LogicError("track index %d out of range (project has %d)" % (index, len(col)))
    x, y, _ = col[index - 1]
    script = '''
tell application "%s" to activate
delay 0.4
tell application "System Events"
    tell process "%s"
        set frontmost to true
        click at {%d, %d}
    end tell
end tell
''' % (app_name(), process_name(), x + 5, y + 9)
    osa(script)


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
