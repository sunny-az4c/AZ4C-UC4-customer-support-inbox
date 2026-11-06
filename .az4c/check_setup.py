"""Checks that this machine is ready for the assignment's AI coding tool settings, and says what to fix.

    python .az4c/check_setup.py        (on Windows, if that doesn't work: py .az4c/check_setup.py)

Run it once from the repository folder before you start, and again after fixing anything it reports.
It changes nothing on your machine and sends nothing about you or your work.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
WINDOWS = os.name == "nt"
COMMANDS = (["python3"], ["python"], ["py", "-3"])
HOOK_FILES = (".az4c/log_event.py", ".claude/settings.json", ".cursor/hooks.json", ".github/hooks/az4c-activity.json")

fixes: list[str] = []
notes: list[str] = []


def say(ok: bool | None, text: str, fix: str = "") -> None:
    print(f"  [{'OK ' if ok else 'FIX' if ok is False else '!  '}] {text}")
    if fix:
        (fixes if ok is False else notes).append(fix)


def version_of(cmd: list[str]) -> tuple[int, ...] | None:
    exe = shutil.which(cmd[0])
    if exe is None:
        return None
    try:
        r = subprocess.run([exe, *cmd[1:], "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
                           capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        return tuple(int(x) for x in r.stdout.strip().split("."))
    except ValueError:
        return None


def check_python() -> None:
    print("Python")
    found = {" ".join(c): version_of(c) for c in COMMANDS}
    working = {name: v for name, v in found.items() if v}
    for name, v in found.items():
        say(True if v else None, f"{name}: {'.'.join(map(str, v)) if v else 'not available'}")
    if not working:
        how = ("Install Python 3.12 from python.org and, in the installer, tick \"Add python.exe to PATH\". "
               "If it is installed already, run the installer again, choose Modify, and tick "
               "\"Add Python to environment variables\". Then close and reopen your editor."
               if WINDOWS else "Install Python 3.12 (python.org, or your package manager) so that python3 works.")
        say(False, "Your AI tool runs python3, python or py, and none of them works on this machine.", how)
    elif all(v < (3, 8) for v in working.values()):
        say(False, "The Python your AI tool would use is older than 3.8.", "Install Python 3.12 from python.org.")
    else:
        say(True, f"Your AI tool will run {next(iter(working))}.")
    has_312 = any(v[:2] == (3, 12) for v in working.values()) or (WINDOWS and version_of(["py", "-3.12"]))
    if not has_312:
        say(None, "Python 3.12 not found. START_HERE.md asks you to use it: your code is run with 3.12.",
            "Install Python 3.12 from python.org and use it for this project.")


def check_files() -> dict:
    print("Settings files")
    folders = [PROJECT] + ([PROJECT / "code"] if (PROJECT / "code").is_dir() else [])
    for folder in folders:
        missing = [f for f in HOOK_FILES if not (folder / f).is_file()]
        where = "repository folder" if folder == PROJECT else "code/"
        if missing:
            say(False, f"{where}: missing {', '.join(missing)}",
                "Some AI tool settings files are missing. Download your pack again from your assessment link and "
                "copy your work into it.")
        else:
            say(True, f"{where}: all in place")
    try:
        cfg = json.loads((HERE / "candidate.json").read_text(encoding="utf-8"))
        if not all(cfg.get(k) for k in ("candidate", "token", "endpoint")):
            raise ValueError
    except (OSError, ValueError):
        say(False, ".az4c/candidate.json is missing or incomplete",
            "This copy wasn't downloaded from your own assessment link. Download your pack from your link and "
            "work in that copy.")
        return {}
    say(True, "your assessment settings are in place")
    return cfg


def check_connection(cfg: dict) -> None:
    print("Connection")
    host = cfg["endpoint"].split("/")[2] if "//" in cfg["endpoint"] else cfg["endpoint"]
    req = urllib.request.Request(cfg["endpoint"], data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg['token']}"})
    try:
        urllib.request.urlopen(req, timeout=60)
        say(True, f"{host} reached")
    except urllib.error.HTTPError as e:
        if e.code == 400:
            say(True, f"{host} reached, and your settings are accepted")
        elif e.code == 401:
            say(False, f"{host} doesn't accept the settings in this copy",
                "Download your pack again from your own assessment link and work in that copy.")
        else:
            say(None, f"{host} answered {e.code}; run this check again in a few minutes")
    except Exception:
        say(False, f"{host} can't be reached from this machine",
            f"Your network may be blocking https://{host} (a firewall, proxy or VPN). Allow it, or try another "
            "network, then run this check again.")


def flush_waiting(cfg: dict) -> None:
    queue = Path(tempfile.gettempdir()) / f"az4c-activity-{cfg['candidate']}"
    waiting = list(queue.glob("*.json")) if queue.is_dir() else []
    if not waiting:
        return
    subprocess.run([sys.executable, str(HERE / "log_event.py"), "--flush"], timeout=300, stdin=subprocess.DEVNULL)
    left = len(list(queue.glob("*.json")))
    if left:
        say(False, f"{left} of {len(waiting)} recorded steps are still waiting to be sent",
            "Fix the connection above, then run this check again: it sends them.")
    else:
        say(True, f"{len(waiting)} recorded steps that were waiting have now been sent")


def main() -> None:
    print(f"Checking {PROJECT}\n")
    check_python()
    cfg = check_files()
    if cfg:
        check_connection(cfg)
        flush_waiting(cfg)
    print()
    for note in dict.fromkeys(notes):
        print(f"Note: {note}")
    if fixes:
        print("Please fix this, then run the check again:")
        for i, fix in enumerate(dict.fromkeys(fixes), 1):
            print(f"  {i}. {fix}")
        sys.exit(1)
    print("All set. Open this folder (or code/) in Claude Code, Cursor or VS Code with GitHub Copilot, and allow the")
    print("project's settings if your editor asks.")


if __name__ == "__main__":
    main()
