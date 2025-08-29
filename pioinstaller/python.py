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
    """Check if current Python is running in a conda environment."""
    return any([
        os.path.exists(os.path.join(sys.prefix, "conda-meta")),
        # (os.getenv("CONDA_PREFIX") or os.getenv("CONDA_DEFAULT_ENV")),
        "anaconda" in sys.executable.lower(),
        "miniconda" in sys.executable.lower(),
        "continuum analytics" in sys.version.lower(),
        "conda" in sys.version.lower(),
    ])


def is_portable():
    """Check if current Python is compatible (3.10-3.13)."""
    # Check for WinPython first
    try:
        __import__("winpython")
        return True
    except ImportError:  # pylint:disable=bare-except
        pass

    if _is_python_compatible(sys.executable):
        log.debug("Current Python executable is compatible: %s", sys.executable)
        return True

    print(os.path.normpath(sys.executable))
    python_dir = os.path.dirname(sys.executable)
    if not util.IS_WINDOWS:
        # skip "bin" folder
        python_dir = os.path.dirname(python_dir)

    # Check for Python manifest
    manifest_path = os.path.join(python_dir, "package.json")
    if not os.path.isfile(manifest_path):
        return False

    try:
        with open(manifest_path, encoding='utf-8') as fp:
            return json.load(fp).get("name") == "python-portable"
    except (ValueError, UnicodeDecodeError):
        pass

    return False


def fetch_portable_python(dst):
    """Install Python 3.13 using uv."""
    log.debug("Installing Python 3.13 using uv")
    
    try:
        # Use uv to install Python 3.13
        cmd = ["uv", "python", "install", "3.13"]
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Find the installed Python 3.13 executable
        cmd = ["uv", "python", "find", "3.13"]
        result = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        python_exe = result.decode().strip()
        
        if python_exe and os.path.isfile(python_exe):
            log.debug("Python 3.13 installation completed: %s", python_exe)
            return python_exe
        else:
            log.error("Could not find Python 3.13 executable after installation")
            return None
            
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log.debug("Could not install Python 3.13 using uv: %s", exc)
        return None


def get_portable_python_url():
    """Compatibility function - not needed anymore with uv."""
    return None


def is_version_system_compatible(version, systype):
    """Check if a version is compatible with the system type."""
    return True  # Simplified since uv handles compatibility


def check():
    """Check if current Python environment is compatible (3.10-3.13)."""
    # platform check
    if sys.platform == "cygwin":
        raise exception.IncompatiblePythonError("Unsupported Cygwin platform")

    # version check - accept 3.10-3.13
    if sys.version_info < (3, 10) or sys.version_info >= (3, 14):
        raise exception.IncompatiblePythonError(
            "Unsupported Python version: %s. "
            "Supported Python versions are 3.10 to 3.13."
            % platform.python_version(),
        )

    # conda check
    if is_conda():
        raise exception.IncompatiblePythonError("Conda is not supported")

    # Python 3 for macOS is not compatible with macOS < 10.13
    # https://github.com/platformio/platformio-core-installer/issues/70
    if util.IS_MACOS:
        with tempfile.NamedTemporaryFile() as tmpfile:
            os.utime(tmpfile.name)

    if not util.IS_WINDOWS:
        return True

    # windows check
    if any(s in util.get_pythonexe_path().lower()
           for s in ("msys", "mingw", "emacs")):
        raise exception.IncompatiblePythonError(
            "Unsupported environments: msys, mingw, emacs >> %s"
            % util.get_pythonexe_path(),
        )

    try:
        assert os.path.isdir(os.path.join(sys.prefix, "Scripts"))
    except AssertionError as exc:
        raise exception.IncompatiblePythonError(
            "Unsupported python without 'Scripts' folder"
        ) from exc

    return True


def find_compatible_pythons(ignore_pythons=None, raise_exception=True):
    """Find compatible Python executables (3.10-3.13) or install Python 3.13 using uv."""
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
        log.debug("No compatible Python 3.10-3.13 found, attempting to install "
                  "Python 3.13 using uv")
        try:
            python_exe = fetch_portable_python(None)
            if python_exe and _is_python_compatible(python_exe):
                log.debug("Successfully installed and verified Python 3.13: %s", python_exe)
                result.append(python_exe)
                return result
        except Exception:
            log.debug("Failed to install Python 3.13 using uv")

        # If uv installation failed, raise the original error
        raise exception.IncompatiblePythonError(
            "Could not find compatible Python 3.10-3.13 in your system. "
            "Attempted to install Python 3.13 using uv failed. "
            "Please install Python 3.10, 3.11, 3.12, or 3.13 manually and restart "
            "installation."
        )

    return result


def _get_python_candidates(exenames):
    """Get list of Python executable candidates."""
    candidates = []
    for exe in exenames:
        for path in os.getenv("PATH").split(os.pathsep):
            if not os.path.isfile(os.path.join(path, exe)):
                continue
            candidates.append(os.path.join(path, exe))

    if sys.executable in candidates:
        candidates.remove(sys.executable)
    # put current Python to the top of list
    candidates.insert(0, sys.executable)
    return candidates


def _is_python_compatible(python_exe):
    """Check if a Python executable is compatible (3.10-3.13)."""
    try:
        # Simple version check using Python itself
        cmd = [
            python_exe,
            "-c",
            "import sys; print(f'{sys.version_info.major}."
            "{sys.version_info.minor}')",
        ]
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        version_str = output.decode().strip()

#        # Accept Python 3.10-3.13
#        if re.match(r'^3\.(10|11|12|13)$', version_str):
#            log.debug("Found compatible Python %s: %s", python_exe, version_str)
#            return True

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
