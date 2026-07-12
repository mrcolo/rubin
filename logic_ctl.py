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
