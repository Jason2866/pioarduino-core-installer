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

import glob
import json
import logging
import os
import platform
import re
import subprocess
import sys
import tempfile

from pioinstaller import exception, util

log = logging.getLogger(__name__)


def is_conda():
    """
    Check if the current Python interpreter is running inside a Conda environment.
    Returns True if Conda is detected, otherwise False.
    """
    return any(
        [
            os.path.exists(os.path.join(sys.prefix, "conda-meta")),
            "anaconda" in sys.executable.lower(),
            "miniconda" in sys.executable.lower(),
            "continuum analytics" in sys.version.lower(),
            "conda" in sys.version.lower(),
        ]
    )


def is_portable():
    """
    Check if the current Python is portable and compatible (3.10-3.13).
    Returns True if compatible (including WinPython), otherwise False.
    """
    try:
        __import__("winpython")
        return True
    except ImportError:
        pass

    if _is_python_compatible(sys.executable):
        log.debug("Current Python executable is compatible: %s", sys.executable)
        return True

    log.debug("Current Python executable: %s", os.path.normpath(sys.executable))
    python_dir = os.path.dirname(sys.executable)
    if not util.IS_WINDOWS:
        # skip "bin" folder for non-Windows platforms
        python_dir = os.path.dirname(python_dir)

    # Check for Python manifest file
    manifest_path = os.path.join(python_dir, "package.json")
    if not os.path.isfile(manifest_path):
        return False

    try:
        with open(manifest_path, encoding="utf-8") as fp:
            return json.load(fp).get("name") == "python-portable"
    except (ValueError, UnicodeDecodeError):
        pass

    return False


def fetch_portable_python(_dst):
    """
    Attempt to install Python 3.13 using uv and return the path to the new executable.
    Returns path string if successful, otherwise None.
    """
    log.debug("Installing Python 3.13 using uv")
    try:
        # Install Python 3.13 via uv
        cmd = ["uv", "python", "install", "3.13"]
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Find the installed Python 3.13 executable
        cmd = ["uv", "python", "find", "3.13"]
        result = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        python_exe = result.decode().strip()

        if python_exe and os.path.isfile(python_exe):
            log.debug("Python 3.13 installation completed: %s", python_exe)
            return python_exe

        log.error("Could not find Python 3.13 executable after installation")
        return None

    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log.debug("Could not install Python 3.13 using uv: %s", exc)
        return None


def get_portable_python_url():
    """
    Compatibility stub, not needed anymore with uv.
    Always returns None.
    """
    return None


def is_version_system_compatible(_version, _systype):
    """
    Compatibility check. Always returns True since uv handles compatibility.
    """
    return True


def check():
    """
    Verify if the current Python environment is compatible (3.10-3.13).
    Raises IncompatiblePythonError on failure.
    Returns True if compatible.
    """
    # Platform check
    if sys.platform == "cygwin":
        raise exception.IncompatiblePythonError("Unsupported Cygwin platform")

    # Version check: Accept only 3.10 up to (and not including) 3.14
    if sys.version_info < (3, 10) or sys.version_info >= (3, 14):
        raise exception.IncompatiblePythonError(
            "Unsupported Python version: %s. "
            "Supported Python versions are 3.10 to 3.13." % platform.python_version(),
        )

    # Conda environments are not supported
    if is_conda():
        raise exception.IncompatiblePythonError("Conda is not supported")

    # macOS compatibility (Python 3 requires macOS >= 10.13)
    # https://github.com/platformio/platformio-core-installer/issues/70
    if util.IS_MACOS:
        with tempfile.NamedTemporaryFile() as tmpfile:
            os.utime(tmpfile.name)

    if not util.IS_WINDOWS:
        return True

    # Windows environment check for unsupported environments
    if any(s in util.get_pythonexe_path().lower() for s in ("msys", "mingw", "emacs")):
        raise exception.IncompatiblePythonError(
            "Unsupported environments: msys, mingw, emacs >> %s"
            % util.get_pythonexe_path(),
        )

    scripts_dir = os.path.join(sys.prefix, "Scripts")
    if not os.path.isdir(scripts_dir):
        raise exception.IncompatiblePythonError(
            "Unsupported python without 'Scripts' folder"
        )

    return True


def find_compatible_pythons(ignore_pythons=None, raise_exception=True):
    """
    Find all compatible Python executables in the system (Python 3.10 - 3.13).
    Optionally install Python 3.13 using uv if none are found.
    Returns a list of executable paths.
    """
    ignore_list = []
    for p in ignore_pythons or []:
        ignore_list.extend(glob.glob(p))

    exenames = [
        "python3",  # system Python
        "python3.13",
        "python3.12",
        "python3.11",
        "python3.10",
        "python",
    ]
    if util.IS_WINDOWS:
        exenames = ["%s.exe" % item for item in exenames]

    log.debug("Current environment PATH %s", os.getenv("PATH"))
    candidates = _get_python_candidates(exenames)

    result = []
    for item in candidates:
        if item in ignore_list:
            continue
        log.debug("Checking a Python candidate %s", item)
        if _is_python_compatible(item):
            result.append(item)

    if not result and raise_exception:
        # Try to install Python 3.13 using uv
        log.debug(
            "No compatible Python 3.10-3.13 found, attempting to install "
            "Python 3.13 using uv"
        )
        try:
            python_exe = fetch_portable_python(None)
            if python_exe and _is_python_compatible(python_exe):
                log.debug(
                    "Successfully installed and verified Python 3.13: %s", python_exe
                )
                result.append(python_exe)
                return result
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
            log.debug("Failed to install Python 3.13 using uv: %s", exc)

        # If uv installation failed, raise the original error
        raise exception.IncompatiblePythonError(
            "Could not find compatible Python 3.10-3.13 in your system. "
            "Attempted to install Python 3.13 using uv failed. "
            "Please install Python 3.10, 3.11, 3.12, or 3.13 manually and restart "
            "installation."
        )

    return result


def _get_python_candidates(exenames):
    """
    Scan system PATH for all specified Python executables.
    Returns a list of candidate executable paths.
    """
    candidates = []
    env_path = os.getenv("PATH") or ""
    for exe in exenames:
        for path in env_path.split(os.pathsep):
            full = os.path.join(path, exe)
            if os.path.isfile(full) and os.access(full, os.X_OK):
                candidates.append(full)

    if sys.executable in candidates:
        candidates.remove(sys.executable)
    # Place the current Python executable at the top of the list
    candidates.insert(0, sys.executable)
    return candidates


def _is_python_compatible(python_exe):
    """
    Determine if the specified Python executable is compatible (version 3.10 - 3.13).
    Returns True if compatible, otherwise False.
    """
    try:
        # Python version check using subprocess
        cmd = [
            python_exe,
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ]
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        version_str = output.decode().strip()

        # Match Python version 3.10, 3.11, 3.12, or 3.13
        if re.match(r"^3\.(10|11|12|13)$", version_str):
            log.debug("Found compatible Python %s: %s", python_exe, version_str)
            return True

        log.debug("Incompatible Python %s: %s", python_exe, version_str)
        return False

    except subprocess.CalledProcessError as e:
        try:
            error = e.output.decode()
            log.debug("Error checking Python %s: %s", python_exe, error)
        except UnicodeDecodeError:
            log.debug("Error checking Python %s (decode failed)", python_exe)
    except (OSError, ValueError):
        log.debug("Exception checking Python %s", python_exe)

    return False
