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
import subprocess
import sys
import tempfile

import requests
import semantic_version

from pioinstaller import exception, util

log = logging.getLogger(__name__)


def is_conda():
    return any(
        [
            os.path.exists(os.path.join(sys.prefix, "conda-meta")),
            # (os.getenv("CONDA_PREFIX") or os.getenv("CONDA_DEFAULT_ENV")),
            "anaconda" in sys.executable.lower(),
            "miniconda" in sys.executable.lower(),
            "continuum analytics" in sys.version.lower(),
            "conda" in sys.version.lower(),
        ]
    )


def is_portable():
    try:
        __import__("winpython")

        return True
    except:  # pylint:disable=bare-except
        pass
    print(os.path.normpath(sys.executable))
    python_dir = os.path.dirname(sys.executable)
    if not util.IS_WINDOWS:
        # skip "bin" folder
        python_dir = os.path.dirname(python_dir)
    manifest_path = os.path.join(python_dir, "package.json")
    if not os.path.isfile(manifest_path):
        return False
    try:
        with open(manifest_path) as fp:
            return json.load(fp).get("name") == "python-portable"
    except ValueError:
        pass
    return False


def fetch_portable_python(dst):
    url = get_portable_python_url()
    if not url:
        log.debug("Could not find portable Python for %s", util.get_systype())
        return None
    try:
        log.debug("Downloading portable python...")

        archive_path = util.download_file(
            url, os.path.join(os.path.join(dst, ".cache", "tmp"), os.path.basename(url))
        )

        python_dir = os.path.join(dst, "python3")
        util.safe_remove_dir(python_dir)
        util.safe_create_dir(python_dir, raise_exception=True)

        log.debug("Unpacking portable python...")
        util.unpack_archive(archive_path, python_dir)
        if util.IS_WINDOWS:
            return os.path.join(python_dir, "python.exe")
        return os.path.join(python_dir, "bin", "python3")
    except:  # pylint:disable=bare-except
        log.debug("Could not download portable python")
    return None


def get_portable_python_url():
    systype = util.get_systype()
    result = requests.get(
        "https://github.com/pioarduino/python-portable/"
        "releases/download/v3.11.7/python-portable.json",
        timeout=10,
    ).json()
    versions = [
        version
        for version in result["versions"]
        if is_version_system_compatible(version, systype)
    ]
    best_version = {}
    for version in versions:
        if not best_version or semantic_version.Version(
            version["name"]
        ) > semantic_version.Version(best_version["name"]):
            best_version = version
    for item in best_version.get("files", []):
        if systype in item["system"]:
            return item["download_url"]
    return None


def is_version_system_compatible(version, systype):
    return any(systype in item["system"] for item in version["files"])


def check():
    # platform check
    if sys.platform == "cygwin":
        raise exception.IncompatiblePythonError("Unsupported Cygwin platform")

    # version check
    if sys.version_info < (3, 10):
        raise exception.IncompatiblePythonError(
            "Unsupported Python version: %s. "
            "Minimum supported Python version is 3.10 or above."
            % platform.python_version(),
        )

    # conda check
    if is_conda():
        raise exception.IncompatiblePythonError("Conda is not supported")

    # portable Python 3 for macOS is not compatible with macOS < 10.13
    # https://github.com/platformio/platformio-core-installer/issues/70
    if util.IS_MACOS:
        with tempfile.NamedTemporaryFile() as tmpfile:
            os.utime(tmpfile.name)

    if not util.IS_WINDOWS:
        return True

    # windows check
    if any(s in util.get_pythonexe_path().lower() for s in ("msys", "mingw", "emacs")):
        raise exception.IncompatiblePythonError(
            "Unsupported environments: msys, mingw, emacs >> %s"
            % util.get_pythonexe_path(),
        )

    try:
        assert os.path.isdir(os.path.join(sys.prefix, "Scripts"))
    except AssertionError:
        raise exception.IncompatiblePythonError(
            "Unsupported python without 'Scripts' folder"
        )

    return True


def find_compatible_pythons(
    ignore_pythons=None, raise_exception=True
):  # pylint: disable=too-many-branches
    """Find compatible Python executables using direct version checks."""
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
        # Try to download portable Python before giving up
        log.debug("No compatible Python found, attempting to download portable Python")
        try:
            # Create a temporary directory for portable Python
            with tempfile.TemporaryDirectory() as temp_dir:
                portable_python = fetch_portable_python(temp_dir)
                if portable_python and _is_python_compatible(portable_python):
                    log.debug("Successfully downloaded and verified portable Python: %s",
                             portable_python)
                    result.append(portable_python)
                    return result
        except Exception as e:  # pylint: disable=broad-except
            log.debug("Failed to download portable Python: %s", e)

        # If portable Python download failed, raise the original error
        raise exception.IncompatiblePythonError(
            "Could not find compatible Python 3.10 or above in your system. "
            "Attempted to download portable Python but failed. "
            "Please install the latest official Python 3 and restart installation."
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
    """Check if a Python executable is compatible (3.10+)."""
    try:
        # Simple version check using Python itself
        cmd = [
            python_exe,
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ]
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        version_str = output.decode().strip()
        major, minor = map(int, version_str.split("."))

        # Check if it's Python 3.10 or higher
        if major >= 3 and minor >= 10:
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
    except Exception as e:  # pylint: disable=broad-except
        log.debug("Exception checking Python %s: %s", python_exe, e)

    return False
