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
import time
from functools import lru_cache

import requests

from pioinstaller import exception, util

log = logging.getLogger(__name__)

# Cache for parsed release data to avoid repeated API calls
_cached_release_data = None
_cached_latest_tag = None
_RELEASE_CACHE_TTL = 300  # 5 minutes
_release_cache_time = 0
_latest_tag_cache_time = 0

# Fallback release tag if latest release has incompatible naming
_FALLBACK_RELEASE_TAG = '20250828'

# Pre-compiled regex for better performance
_ASSET_NAME_REGEX = re.compile(
    r'^cpython-(\d+\.\d+\.\d+)\+(\d+)-([^-]+)-([^-]+)-([^-]+)'
    r'(?:-([^-]+))?(?:-([^.]+))?\.(tar\.(?:gz|zst))$'
)


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


def _get_latest_release_tag():
    """Get the latest release tag from GitHub API with caching."""
    # pylint: disable=global-statement
    global _cached_latest_tag, _latest_tag_cache_time

    now = time.time()

    # Use cached tag if still valid
    if (_cached_latest_tag and
            (now - _latest_tag_cache_time) < _RELEASE_CACHE_TTL):
        return _cached_latest_tag

    try:
        log.debug('Fetching latest release tag from GitHub')
        response = requests.get(
            'https://api.github.com/repos/astral-sh/'
            'python-build-standalone/releases/latest',
            timeout=10,
            headers={
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': 'pioarduino-Python-Installer',
            }
        )
        response.raise_for_status()

        latest_release = response.json()
        _cached_latest_tag = latest_release['tag_name']
        _latest_tag_cache_time = now

        log.debug("Using latest release: %s", _cached_latest_tag)
        return _cached_latest_tag
    except (requests.exceptions.RequestException,
            requests.exceptions.JSONDecodeError,
            KeyError, ValueError):
        # Fallback to known stable release if API fails
        log.warning("Failed to get latest release, using fallback: %s",
                    _FALLBACK_RELEASE_TAG)
        return _FALLBACK_RELEASE_TAG


def _parse_asset_name(asset_name):
    """Parse asset filename to extract metadata."""
    match = _ASSET_NAME_REGEX.match(asset_name)

    if not match:
        return None

    return {
        'python_version': match.group(1),
        'build_date': match.group(2),
        'arch': match.group(3),
        'os': match.group(4),
        'libc': match.group(5),
        'build_variant': match.group(6) or '',
        'package_type': match.group(7) or '',
        'compression': match.group(8),
    }


def _parse_asset_name_fallback(asset_name):
    """Fallback parsing for alternative naming schemes."""
    # Alternative regex patterns for different naming conventions
    fallback_patterns = [
        # Pattern for simplified naming: cpython-3.13.7-linux-x64.tar.gz
        re.compile(r'^cpython-(\d+\.\d+\.\d+)-([^-]+)-([^.]+)\.'
                   r'(tar\.(?:gz|zst))$'),
        # Pattern for date-only naming:
        # python-3.13.7-20250818-linux-x64.tar.gz
        re.compile(r'^python-(\d+\.\d+\.\d+)-(\d+)-([^-]+)-([^.]+)\.'
                   r'(tar\.(?:gz|zst))$'),
        # Generic Python naming: python-3.13.7-linux-x64.tar.gz
        re.compile(r'^python-(\d+\.\d+\.\d+)-([^-]+)-([^.]+)\.'
                   r'(tar\.(?:gz|zst))$'),
    ]

    for pattern in fallback_patterns:
        match = pattern.match(asset_name)
        if match:
            groups = match.groups()
            # Map to standardized format
            return {
                'python_version': groups[0],
                'build_date': groups[1] if len(groups) > 3 else 'unknown',
                'arch': groups[-3] if len(groups) > 2 else 'unknown',
                'os': groups[-4] if len(groups) > 3 else 'unknown',
                'libc': 'unknown',
                'build_variant': '',
                'package_type': 'install_only',
                'compression': groups[-1],
            }

    return None


@lru_cache(maxsize=32)
def _get_system_mapping(systype):
    """Get system mapping for architecture compatibility (cached)."""
    mappings = {
        'darwin-x64': {'arch': 'x86_64', 'os': 'apple', 'libc': 'darwin'},
        'darwin-arm64': {'arch': 'aarch64', 'os': 'apple', 'libc': 'darwin'},
        'linux-x64': {'arch': 'x86_64', 'os': 'unknown', 'libc': 'linux'},
        'linux-arm64': {'arch': 'aarch64', 'os': 'unknown', 'libc': 'linux'},
        'linux-armv7l': {'arch': 'armv7', 'os': 'unknown', 'libc': 'linux'},
        'win32-x64': {'arch': 'x86_64', 'os': 'pc', 'libc': 'windows'},
        'win32-ia32': {'arch': 'i686', 'os': 'pc', 'libc': 'windows'},
    }
    return mappings.get(systype)


def _is_asset_compatible(asset_name, systype):
    """Check if asset is compatible with target system (Python 3.13 only for installation)."""
    parsed = _parse_asset_name(asset_name)
    if not parsed:
        return False

    # For installation, only Python 3.13 assets are considered
    version_parts = parsed['python_version'].split('.')
    major = int(version_parts[0])
    minor = int(version_parts[1])
    if major != 3 or minor != 13:
        return False

    # Exclude unwanted build variants
    build_variant = parsed['build_variant']
    if build_variant and any(variant in build_variant for variant in
                             ['freethreaded', 'debug', 'noopt']):
        return False

    # System compatibility mapping
    system_map = _get_system_mapping(systype)
    if not system_map:
        return False

    return (parsed['arch'] == system_map['arch'] and
            parsed['os'] == system_map['os'] and
            parsed['libc'].startswith(system_map['libc']))


def _is_asset_compatible_fallback(asset_name, systype):
    """Fallback compatibility check for alternative naming schemes (Python 3.13 only)."""
    parsed = _parse_asset_name_fallback(asset_name)
    if not parsed:
        return False

    # For installation, only Python 3.13 assets are considered
    version_parts = parsed['python_version'].split('.')
    major = int(version_parts[0])
    minor = int(version_parts[1])
    if major != 3 or minor != 13:
        return False

    # Simple system compatibility check based on common naming patterns
    name = asset_name.lower()
    compatibility_map = {
        'darwin-x64': ['macos', 'darwin', 'osx', 'x86_64'],
        'darwin-arm64': ['macos', 'darwin', 'osx', 'arm64', 'aarch64'],
        'linux-x64': ['linux', 'x86_64', 'amd64'],
        'linux-arm64': ['linux', 'arm64', 'aarch64'],
        'linux-armv7l': ['linux', 'armv7', 'arm'],
        'win32-x64': ['windows', 'win', 'x86_64', 'amd64'],
        'win32-ia32': ['windows', 'win', 'i686', 'x86'],
    }

    patterns = compatibility_map.get(systype, [])
    return any(pattern in name for pattern in patterns)


def _score_asset(asset_name, systype):
    """Score assets"""
    parsed = _parse_asset_name(asset_name)
    is_fallback = False

    # Try fallback parsing if primary parsing fails
    if not parsed:
        parsed = _parse_asset_name_fallback(asset_name)
        is_fallback = True

    if not parsed:
        return -1

    # Check compatibility
    is_compatible = (_is_asset_compatible_fallback(asset_name, systype)
                     if is_fallback
                     else _is_asset_compatible(asset_name, systype))

    if not is_compatible:
        return -1

    version_parts = parsed['python_version'].split('.')
    patch = int(version_parts[2])
    score = patch

    # Prefer primary naming scheme over fallback
    if is_fallback:
        score -= 5000

    # Performance optimization bonuses
    build_variant = parsed['build_variant']
    package_type = parsed['package_type']

    if build_variant and any(opt in build_variant for opt in ['pgo', 'lto']):
        score += 1000

    if package_type and 'install' in package_type:
        score += 500

    if package_type and 'stripped' in package_type:
        score += 100

    # Slight preference for tar.gz for maximum compatibility
    if parsed['compression'] == 'tar.gz':
        score += 10

    return score


def _try_get_registry_from_release(release_tag, systype):
    """Try to get registry file from a specific release tag."""
    try:
        # Load release data from astral-sh/python-build-standalone
        response = requests.get(
            f'https://api.github.com/repos/astral-sh/'
            f'python-build-standalone/releases/tags/{release_tag}',
            timeout=60,
            headers={
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': 'pioarduino-Python-Installer',
            }
        )
        response.raise_for_status()
        release_data = response.json()

        # Cache the release data if this is the first successful request
        # pylint: disable=global-statement
        global _cached_release_data, _release_cache_time
        now = time.time()
        if (not _cached_release_data or
                (now - _release_cache_time) >= _RELEASE_CACHE_TTL):
            _cached_release_data = release_data
            _release_cache_time = now

        return _select_best_asset(release_data, systype)
    except (requests.exceptions.RequestException,
            requests.exceptions.JSONDecodeError,
            KeyError, ValueError):
        log.warning("Failed to fetch release %s", release_tag)
        return None


def _select_best_asset(release_data, systype):
    """Select the best asset for the given system type."""
    # Filter compatible assets with multiple naming pattern support
    compatible_assets = []
    for asset in release_data['assets']:
        if (_is_asset_compatible(asset['name'], systype) or
                _is_asset_compatible_fallback(asset['name'], systype)):
            compatible_assets.append(asset)

    if not compatible_assets:
        return None

    # Find asset with highest score
    best_asset = None
    best_score = -1

    for asset in compatible_assets:
        current_score = _score_asset(asset['name'], systype)
        if current_score > best_score:
            best_score = current_score
            best_asset = asset

    if not best_asset:
        return None

    # Convert asset to compatible format
    compression = ('zst' if best_asset['name'].endswith('.tar.zst')
                   else 'gzip')

    return {
        'name': best_asset['name'],
        'download_url': best_asset['browser_download_url'],
        'size': best_asset['size'],
        'system': [systype],
        'compression': compression,
        'digest': getattr(best_asset, 'digest', None),
    }


def _get_registry_file():
    """Fetch Python packages from astral-sh/python-build-standalone."""
    systype = util.get_systype()
    now = time.time()

    # Use cached data if still valid
    if (_cached_release_data and
            (now - _release_cache_time) < _RELEASE_CACHE_TTL):
        return _select_best_asset(_cached_release_data, systype)

    # Try latest release first
    selected_asset = _try_get_registry_from_release(_get_latest_release_tag(),
                                                    systype)

    # If latest release has no compatible assets, fallback to known working
    if not selected_asset and _cached_latest_tag != _FALLBACK_RELEASE_TAG:
        log.warning('No compatible assets in latest release, '
                    'trying fallback release')
        selected_asset = _try_get_registry_from_release(_FALLBACK_RELEASE_TAG,
                                                        systype)

    return selected_asset


def fetch_portable_python(dst):
    """Download and install Python 3.13 distribution."""
    log.debug("Starting Python 3.13 installation")

    registry_file = _get_registry_file()
    if not registry_file:
        log.debug("Could not findPython 3.13 for %s", util.get_systype())
        return None

    log.debug("Selected Python package: %s", registry_file['name'])

    try:
        # Download the archive
        archive_path = util.download_file(
            registry_file['download_url'],
            os.path.join(dst, ".cache", "tmp", registry_file['name'])
        )

        # Verify integrity if digest is available
        if (registry_file.get('digest') and
                not util.verify_file_integrity(archive_path,
                                               registry_file['digest'])):
            log.error("Downloaded file failed SHA256 integrity check")
            return None

        # Clean up existing installation
        python_dir = os.path.join(dst, "python3")
        util.safe_remove_dir(python_dir)
        util.safe_create_dir(python_dir, raise_exception=True)

        # Extract archive
        log.debug("Unpacking python...")
        util.unpack_archive(archive_path, python_dir)

        # Return path to Python executable
        if util.IS_WINDOWS:
            python_exe = os.path.join(python_dir, "python.exe")
        else:
            python_exe = os.path.join(python_dir, "bin", "python3")

        # Verify that the executable exists
        if not os.path.isfile(python_exe):
            log.error("Python executable does not exist after extraction!")
            return None

        log.debug("Python 3.13 installation completed: %s", python_dir)
        return python_exe

    except (OSError, PermissionError) as exc:
        log.debug("Could not download python: %s", exc)
        return None


def get_portable_python_url():
    """Compatibility function - uses the astral-sh repository."""
    registry_file = _get_registry_file()
    return registry_file['download_url'] if registry_file else None


def is_version_system_compatible(version, systype):
    """Check if a version is compatible with the system type."""
    return any(systype in item["system"] for item in version["files"])


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


def find_compatible_pythons(
    ignore_pythons=None, raise_exception=True
):  # pylint: disable=too-many-branches
    """Find compatible Python executables (3.10-3.13) or install Python 3.13."""
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
        # Try to download Python 3.13 before giving up
        log.debug("No compatible Python 3.10-3.13 found, attempting to download "
                  "Python 3.13")
        try:
            # Create a temporary directory for portable Python
            with tempfile.TemporaryDirectory() as temp_dir:
                portable_python = fetch_portable_python(temp_dir)
                if portable_python and _is_python_compatible(portable_python):
                    log.debug(
                        "Successfully downloaded and verified Python: "
                        "%s", portable_python,
                    )
                    result.append(portable_python)
                    return result
        except (OSError, PermissionError, subprocess.CalledProcessError):
            log.debug("Failed to download Python 3.13")

        # If portable Python download failed, raise the original error
        raise exception.IncompatiblePythonError(
            "Could not find compatible Python 3.10-3.13 in your system. "
            "Attempted to download Python 3.13 failed. "
            "Please install Python 3.10, 3.11, 3.12, or 3.13 and restart "
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
    """Check if a Python executable is compatible (3.13)."""
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

        # Accept Python 3.13
        if re.match(r'^3\.(1[3])$', version_str):
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
