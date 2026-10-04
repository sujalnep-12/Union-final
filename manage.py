#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from pathlib import Path


def relaunch_with_project_venv():
    """Let `python manage.py ...` work even when the venv is not activated."""
    project_root = Path(__file__).resolve().parent
    candidate_paths = [
        project_root / ".venv" / "Scripts" / "python.exe",
        project_root / "venv" / "Scripts" / "python.exe",
    ]
    venv_python = next((path for path in candidate_paths if path.exists()), None)
    if not venv_python:
        return

    try:
        current_python = Path(sys.executable).resolve()
        target_python = venv_python.resolve()
    except OSError:
        return

    if current_python == target_python:
        return

    os.environ.setdefault("VIRTUAL_ENV", str(venv_python.parent.parent))
    os.execv(str(target_python), [str(target_python), *sys.argv])


def main():
    relaunch_with_project_venv()
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "community_hub.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Is it installed and available on your "
            "PYTHONPATH? Did you forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
