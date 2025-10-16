# Copyright (c) 2014-present PlatformIO <contact@platformio.org>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import time

import click

from pioinstaller import __version__, core, exception, util

log = logging.getLogger(__name__)

# Official uv installer scripts
UV_INSTALL_SCRIPT_UNIX = "https://astral.sh/uv/install.sh"
UV_INSTALL_SCRIPT_WINDOWS = "https://astral.sh/uv/install.ps1"

# Python version to use
PYTHON_VERSION = "3.13"

# Platform-specific constants
PYTHON_EXE = "python.exe" if util.IS_WINDOWS else "python"
BIN_DIR = "Scripts" if util.IS_WINDOWS else "bin"
UV_EXE = "uv.exe" if util.IS_WINDOWS else "uv"


def install_uv_with_official_script(cache_dir):
    """Install uv using official installation scripts."""
    uv_dest = os.path.join(cache_dir, UV_EXE)

    try:
        if util.IS_WINDOWS:
            # Use PowerShell installer for Windows
            log.debug("Installing uv using official Windows installer")
            env = os.environ.copy()
            env["UV_INSTALL_DIR"] = cache_dir

            cmd = [
                "powershell",
                "-ExecutionPolicy", "ByPass",
                "-Command",
                f"irm {UV_INSTALL_SCRIPT_WINDOWS} | iex"
            ]

            subprocess.run(
                cmd,
                check=True,
                timeout=300,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        else:
            # Use shell installer for Unix (Linux/macOS)
            log.debug("Installing uv using official Unix installer")
            env = os.environ.copy()
            env["UV_INSTALL_DIR"] = cache_dir

            cmd = ["sh", "-c", f"curl -LsSf {UV_INSTALL_SCRIPT_UNIX} | sh"]

            subprocess.run(
                cmd,
                check=True,
                timeout=300,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        # Verify installation
        if os.path.isfile(uv_dest):
            if not util.IS_WINDOWS:
                os.chmod(uv_dest, 0o755)
            log.debug("uv installed at %s", uv_dest)
            return uv_dest

        log.error("uv binary not found after installation")
        return None

    except subprocess.CalledProcessError as e:
        log.debug("Failed to install uv: %s", e)
        return None
    except subprocess.TimeoutExpired as e:
        log.debug("Timeout installing uv: %s", e)
        return None
    except OSError as e:
        log.exception("Unexpected error installing uv: %s", e)
        return None


def get_uv_executable():
    """Get path to uv executable, install if needed."""
    # First try to find uv in PATH
    uv_exe = shutil.which("uv")
    if uv_exe and os.path.isfile(uv_exe):
        log.debug("Found uv in PATH: %s", uv_exe)
        return uv_exe

    # Try to find cached uv
    cache_dir = core.get_cache_dir()
    cached_uv = os.path.join(cache_dir, UV_EXE)

    if os.path.isfile(cached_uv) and (util.IS_WINDOWS or os.access(cached_uv, os.X_OK)):
        log.debug("Found cached uv: %s", cached_uv)
        return cached_uv

    # Install uv using official script
    uv_exe = install_uv_with_official_script(cache_dir)
    if uv_exe:
        log.info("uv installed at %s", uv_exe)
        return uv_exe

    return None


def get_penv_dir(path=None):
    """Get the PlatformIO virtual environment directory."""
    if os.getenv("PLATFORMIO_PENV_DIR"):
        return os.getenv("PLATFORMIO_PENV_DIR")

    core_dir = path or core.get_core_dir()
    return os.path.join(core_dir, "penv")


def get_penv_bin_dir(path=None):
    """Get the PlatformIO virtual environment bin directory."""
    penv_dir = path or get_penv_dir()
    return os.path.join(penv_dir, BIN_DIR)


def create_core_penv(penv_dir=None):
    """Create PlatformIO core virtual environment."""
    penv_dir = penv_dir or get_penv_dir()

    # Get uv executable
    uv_exe = get_uv_executable()
    if not uv_exe:
        raise exception.PIOInstallerException(
            "uv package manager is required. Please install uv first."
        )

    # Ensure uv is resolvable via PATH for helpers that shell out to "uv"
    uv_dir = os.path.dirname(uv_exe)
    current_path = os.environ.get("PATH", "")
    if uv_dir not in current_path.split(os.pathsep):
        os.environ["PATH"] = uv_dir + os.pathsep + current_path

    # Create venv with uv - it handles Python installation automatically
    result_dir = create_venv_with_uv(uv_exe, penv_dir)
    if not result_dir:
        raise exception.PIOInstallerException(
            "Could not create PIO Core Virtual Environment. Please report to "
            "https://github.com/pioarduino/pioarduino-core-installer/issues"
        )

    python_exe = os.path.join(get_penv_bin_dir(penv_dir), PYTHON_EXE)
    init_state(python_exe, penv_dir)
    click.echo("Virtual environment has been successfully created at %s!" %
               penv_dir)
    return result_dir


def create_venv_with_uv(uv_exe, penv_dir):
    """Create virtual environment using uv with Python 3.13 managed by uv."""
    # Remove existing directory if it exists
    util.safe_remove_dir(penv_dir)

    try:
        # Create venv with uv using Python 3.13 with managed preference
        cmd = [
            uv_exe,
            "venv",
            penv_dir,
            "--python",
            PYTHON_VERSION,
            "--python-preference",
            "managed",
        ]
        subprocess.run(
            cmd,
            check=True,
            timeout=300,  # 5 minutes timeout
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )

        # Verify the venv was created
        expected_python = os.path.join(get_penv_bin_dir(penv_dir), PYTHON_EXE)
        if os.path.isfile(expected_python):
            log.debug("Successfully created venv at %s with Python %s", penv_dir, PYTHON_VERSION)

            # Make uv CLI available inside the venv
            install_uv_in_venv_with_system_uv(uv_exe, penv_dir)

            return penv_dir

        log.debug("Expected python not found at %s", expected_python)
        return None

    except subprocess.CalledProcessError as e:
        log.debug("Failed to create venv with uv: %s", str(e))
        return None
    except subprocess.TimeoutExpired as e:
        log.debug("Timeout creating venv with uv: %s", str(e))
        return None
    except OSError as e:
        log.debug("OS error creating venv with uv: %s", str(e))
        return None
    except Exception:
        log.exception("Unexpected error creating venv with uv")
        raise


def install_uv_in_venv_with_system_uv(system_uv_exe, penv_dir):
    """
    Use the system uv executable to install uv inside the penv venv.
    """
    # Set VIRTUAL_ENV to target the penv directory
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = penv_dir

    venv_python = os.path.join(get_penv_bin_dir(penv_dir), PYTHON_EXE)
    cmd = [system_uv_exe, "pip", "install", "--python", venv_python, "uv"]

    try:
        subprocess.run(
            cmd,
            check=True,
            timeout=120,  # 2 minutes timeout
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            env=env,
        )
        log.debug("Successfully installed uv in venv")
    except subprocess.CalledProcessError as e:
        log.debug("Failed to install uv in venv: %s", e)
        raise exception.PIOInstallerException(
            "Could not install uv in penv") from e
    except subprocess.TimeoutExpired as e:
        log.debug("Timeout installing uv in venv: %s", e)
        raise exception.PIOInstallerException(
            "Timeout installing uv in penv") from e


def init_state(python_exe, penv_dir):
    """Initialize virtual environment state."""
    version_code = (
        "import sys; version=sys.version_info; "
        "print('%d.%d.%d'%(version[0],version[1],version[2]))"
    )
    try:
        python_version = (
            subprocess.check_output(
                [python_exe, "-c", version_code],
                stderr=subprocess.PIPE,
                timeout=30
            )
            .decode()
            .strip()
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        log.exception("Failed to get python version")
        raise exception.PIOInstallerException(
            "Could not determine python version") from e

    state = {
        "created_on": int(round(time.time())),
        "python": {
            "path": python_exe,
            "version": python_version,
        },
        "installer_version": __version__,
        "platform": {
            "platform": platform.platform(),
            "release": platform.release(),
        },
    }
    return save_state(state, penv_dir)


def load_state(penv_dir=None):
    """Load virtual environment state."""
    penv_dir = penv_dir or get_penv_dir()
    state_path = os.path.join(penv_dir, "state.json")
    if not os.path.isfile(state_path):
        raise exception.PIOInstallerException(
            "Could not find state.json file in `%s`" % state_path
        )
    try:
        with open(state_path, encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, json.JSONDecodeError) as e:
        raise exception.PIOInstallerException(
            f"Could not load state file: {e}"
        ) from e


def save_state(state, penv_dir=None):
    """Save virtual environment state."""
    penv_dir = penv_dir or get_penv_dir()
    state_path = os.path.join(penv_dir, "state.json")
    try:
        with open(state_path, "w", encoding="utf-8") as fp:
            json.dump(state, fp, indent=2)
        return state_path
    except OSError as e:
        raise exception.PIOInstallerException(
            f"Could not save state file: {e}"
        ) from e
