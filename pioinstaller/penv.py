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
import tempfile
import time
import zipfile

import click

from pioinstaller import __version__, core, exception, util

log = logging.getLogger(__name__)

# Official uv installer scripts
UV_INSTALL_SCRIPT_UNIX = "https://astral.sh/uv/install.sh"
UV_INSTALL_SCRIPT_WINDOWS = "https://astral.sh/uv/install.ps1"

# Platform-specific constants
PYTHON_VERSION = "3.13"
PYTHON_EXE = "python.exe" if util.IS_WINDOWS else "python"
BIN_DIR = "Scripts" if util.IS_WINDOWS else "bin"
UV_EXE = "uv.exe" if util.IS_WINDOWS else "uv"


def install_uv_with_official_script(cache_dir):
    """Install uv using the official installer script."""
    uv_dest = os.path.join(cache_dir, UV_EXE)

    try:
        if util.IS_WINDOWS:
            # Use PowerShell installer for Windows
            log.debug("Installing uv using official Windows installer")
            env = os.environ.copy()
            env["UV_UNMANAGED_INSTALL"] = cache_dir
            cmd = [
                "powershell",
                "-ExecutionPolicy",
                "ByPass",
                "-Command",
                f"irm {UV_INSTALL_SCRIPT_WINDOWS} | iex",
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
            if not shutil.which("curl"):
                log.debug("curl not found in PATH, cannot download uv installer")
                return None
            log.debug("Installing uv using official Unix installer")
            env = os.environ.copy()
            env["UV_UNMANAGED_INSTALL"] = cache_dir
            cmd = ["sh", "-c", f"curl -LsSf {UV_INSTALL_SCRIPT_UNIX} | sh"]
            subprocess.run(
                cmd,
                check=True,
                timeout=300,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        if os.path.isfile(uv_dest):
            if not util.IS_WINDOWS:
                os.chmod(uv_dest, 0o755)
            log.debug("uv installed at %s", uv_dest)
            return uv_dest
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        log.debug("Failed to install uv with official script: %s", e)

    return None


def _get_uv_platform_tag():
    """Return the uv release asset platform tag for the current system."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin":
        arch_map = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64"}
        arch = arch_map.get(machine)
        if arch:
            return f"uv-{arch}-apple-darwin"
    elif system == "linux":
        arch_map = {
            "x86_64": "x86_64",
            "aarch64": "aarch64",
            "armv7l": "armv7",
            "i686": "i686",
            "ppc64le": "powerpc64le",
            "s390x": "s390x",
        }
        arch = arch_map.get(machine)
        if arch:
            return f"uv-{arch}-unknown-linux-gnu"
    elif system == "windows":
        if machine in ("amd64", "x86_64"):
            return "uv-x86_64-pc-windows-msvc"
        if machine in ("arm64", "aarch64"):
            return "uv-aarch64-pc-windows-msvc"

    return None


def install_uv_download(cache_dir):
    """Download uv binary directly from GitHub releases using Python (requests)."""
    import requests

    tag = _get_uv_platform_tag()
    if not tag:
        log.debug(
            "Unsupported platform for direct uv download: %s/%s",
            platform.system(),
            platform.machine(),
        )
        return None

    uv_dest = os.path.join(cache_dir, UV_EXE)

    if util.IS_WINDOWS:
        archive_name = f"{tag}.zip"
    else:
        archive_name = f"{tag}.tar.gz"

    url = f"https://github.com/astral-sh/uv/releases/latest/download/{archive_name}"
    log.debug("Downloading uv from %s", url)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = os.path.join(tmpdir, archive_name)
            resp = requests.get(url, stream=True, timeout=120, allow_redirects=True)
            resp.raise_for_status()
            with open(archive_path, "wb") as fp:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        fp.write(chunk)

            extract_dir = os.path.join(tmpdir, "extract")
            os.makedirs(extract_dir)

            if archive_name.endswith(".zip"):
                with zipfile.ZipFile(archive_path) as zf:
                    zf.extractall(extract_dir)
            else:
                util.unpack_archive(archive_path, extract_dir)

            # Find the uv binary in extracted files
            uv_binary = util.find_file(UV_EXE, extract_dir)
            if not uv_binary:
                log.debug("uv binary not found in downloaded archive")
                return None

            shutil.copy2(uv_binary, uv_dest)
            if not util.IS_WINDOWS:
                os.chmod(uv_dest, 0o755)

        log.debug("uv downloaded and installed at %s", uv_dest)
        return uv_dest

    except Exception as e:  # pylint: disable=broad-except
        log.debug("Failed to download uv directly: %s", e)
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

    # Install uv using official script (requires curl on Unix)
    uv_exe = install_uv_with_official_script(cache_dir)
    if uv_exe:
        log.info("uv installed at %s", uv_exe)
        return uv_exe

    # Fallback: download uv binary directly using Python (no curl needed)
    log.debug("Falling back to direct Python download of uv")
    uv_exe = install_uv_download(cache_dir)
    if uv_exe:
        log.info("uv downloaded at %s", uv_exe)
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


def create_core_penv(uv_exe, penv_dir=None):
    """Create PlatformIO core virtual environment."""
    penv_dir = penv_dir or get_penv_dir()

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
    click.echo("Virtual environment has been successfully created at %s!" % penv_dir)
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
        result = subprocess.run(
            cmd,
            check=True,
            timeout=300,  # 5 minutes timeout
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        log.debug("uv venv output: %s", result.stdout)

        # Verify the venv was created
        expected_python = os.path.join(get_penv_bin_dir(penv_dir), PYTHON_EXE)
        if os.path.isfile(expected_python):
            log.debug(
                "Successfully created venv at %s with Python %s",
                penv_dir,
                PYTHON_VERSION,
            )
            return penv_dir

        log.debug("Expected python not found at %s", expected_python)
        return None

    except subprocess.CalledProcessError as e:
        log.debug("Failed to create venv with uv: %s\nOutput: %s", e, e.stdout)
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
    venv_python = os.path.join(get_penv_bin_dir(penv_dir), PYTHON_EXE)
    cmd = [system_uv_exe, "pip", "install", "--python", venv_python, "uv"]

    try:
        result = subprocess.run(
            cmd,
            check=True,
            timeout=120,  # 2 minutes timeout
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        log.debug("Successfully installed uv in venv: %s", result.stdout)
    except subprocess.CalledProcessError as e:
        log.debug("Failed to install uv in venv: %s\nOutput: %s", e, e.stdout)
        raise exception.PIOInstallerException(
            "Could not install uv in penv: %s" % (e.stdout or e)
        ) from e
    except subprocess.TimeoutExpired as e:
        log.debug("Timeout installing uv in venv: %s", e)
        raise exception.PIOInstallerException("Timeout installing uv in penv") from e


def init_state(python_exe, penv_dir):
    """Initialize virtual environment state."""
    version_code = (
        "import sys; version=sys.version_info; "
        "print('%d.%d.%d'%(version[0],version[1],version[2]))"
    )
    try:
        python_version = (
            subprocess.check_output(
                [python_exe, "-c", version_code], stderr=subprocess.PIPE, timeout=30
            )
            .decode()
            .strip()
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        log.exception("Failed to get python version")
        raise exception.PIOInstallerException(
            "Could not determine python version"
        ) from e

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
        raise exception.PIOInstallerException(f"Could not load state file: {e}") from e


def save_state(state, penv_dir=None):
    """Save virtual environment state."""
    penv_dir = penv_dir or get_penv_dir()
    state_path = os.path.join(penv_dir, "state.json")
    try:
        with open(state_path, "w", encoding="utf-8") as fp:
            json.dump(state, fp, indent=2)
        return state_path
    except OSError as e:
        raise exception.PIOInstallerException(f"Could not save state file: {e}") from e
