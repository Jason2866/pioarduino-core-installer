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

import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import tarfile
import time
import zipfile

import click
import requests

from dataclasses import dataclass
from pioinstaller import __version__, core, exception, python, util

log = logging.getLogger(__name__)


UV_URL = ("https://github.com/astral-sh/uv/releases/latest/download/"
          "uv-{platform}.{ext}")
UV_API_URL = "https://api.github.com/repos/astral-sh/uv/releases/latest"

# Download retry configuration
DEFAULT_RETRIES = 3
DEFAULT_RETRY_DELAY = 5  # seconds

# Platform-specific constants
PYTHON_EXE = "python.exe" if util.IS_WINDOWS else "python"
BIN_DIR = "Scripts" if util.IS_WINDOWS else "bin"
UV_EXE = "uv.exe" if util.IS_WINDOWS else "uv"


@dataclass
class DownloadConfig:
    """Configuration for uv download."""
    uv_url: str
    archive_path: str
    uv_platform: str
    ext: str
    expected_checksum: str | None = None

    def set_checksum(self, checksum: str | None) -> None:
        """Set the expected checksum for verification."""
        self.expected_checksum = checksum

    def is_checksum_available(self) -> bool:
        """Check if checksum is available for verification."""
        return self.expected_checksum is not None


def get_uv_platform():
    """Get the uv platform identifier for the current system."""
    platform_map = {
        ("Windows", "AMD64"): "x86_64-pc-windows-msvc",
        ("Windows", "ARM64"): "aarch64-pc-windows-msvc",
        ("Windows", "x86"): "i686-pc-windows-msvc",
        ("Darwin", "x86_64"): "x86_64-apple-darwin",
        ("Darwin", "arm64"): "aarch64-apple-darwin",
        ("Linux", "x86_64"): "x86_64-unknown-linux-gnu",
        ("Linux", "aarch64"): "aarch64-unknown-linux-gnu",
        ("Linux", "i686"): "i686-unknown-linux-gnu",
        ("Linux", "armv7l"): "armv7-unknown-linux-gnueabihf",
    }

    system = platform.system()
    machine = platform.machine()
    # normalize common variations
    if machine.lower() in ("x86_64", "amd64"):
        machine = "x86_64" if system != "Windows" else "AMD64"

    # Handle different arm64 representations on macOS
    if system == "Darwin" and machine in ("arm64", "aarch64"):
        machine = "arm64"

    key = (system, machine)
    plat = platform_map.get(key)
    # Detect musl on Linux
    if system == "Linux" and plat and detect_musl():
        if plat.endswith("-unknown-linux-gnu"):
            plat = plat.replace("-unknown-linux-gnu", "-unknown-linux-musl")
        elif plat.endswith("-unknown-linux-gnueabihf"):
            plat = plat.replace("gnueabihf", "musleabihf")
    return plat


def detect_musl():
    """Detect musl libc more robustly."""
    ldd_path = shutil.which("ldd")
    methods = [
        lambda: bool(ldd_path)
        and b"musl" in subprocess.check_output([ldd_path, "--version"], stderr=subprocess.STDOUT),
        lambda: os.path.exists("/lib/libc.musl-x86_64.so.1"),
        lambda: "musl" in os.environ.get("LD_LIBRARY_PATH", ""),
    ]

    for method in methods:
        try:
            if method():
                return True
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            continue
    return False


def _parse_digest_string(digest_str):
    """Parse digest string from GitHub API.
    
    GitHub returns digest as string like 'sha256:abc123...'
    Parse and return the hash value if it's SHA256.
    """
    if not digest_str or not isinstance(digest_str, str):
        return None

    parts = digest_str.split(":", 1)
    if len(parts) != 2:
        return None

    algorithm, hash_value = parts
    if algorithm.lower() != "sha256":
        return None

    return hash_value.strip().lower()


def fetch_uv_checksums_from_github():
    """Fetch SHA256 checksums from GitHub API."""
    # Set proper headers for GitHub API stability
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"PlatformIO-Installer/{__version__}",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    try:
        response = requests.get(UV_API_URL, headers=headers, timeout=30)
        response.raise_for_status()
        release_data = response.json()

        checksums = {}
        for asset in release_data.get("assets", []):
            asset_name = asset.get("name", "")
            if asset_name.endswith((".tar.gz", ".zip")):
                # GitHub digest is a string like "sha256:abc123..."
                digest_str = asset.get("digest")
                digest_hash = _parse_digest_string(digest_str)
                if digest_hash:
                    checksums[asset_name] = digest_hash
                    log.debug("Found checksum for %s: %s", asset_name,
                             digest_hash[:16] + "...")

        log.debug("Fetched checksums for %d assets", len(checksums))
        return checksums

    except (requests.RequestException, json.JSONDecodeError, KeyError,
            AttributeError) as e:
        log.warning("Failed to fetch checksums from GitHub API: %s", e)
        return {}


def get_expected_checksum(platform_name, ext):
    """Get expected checksum for platform and extension."""
    filename = f"uv-{platform_name}.{ext}"
    checksums = fetch_uv_checksums_from_github()
    return checksums.get(filename)


def verify_download(file_path, expected_sha256):
    """Verify downloaded file integrity."""
    if not expected_sha256:
        log.warning("No checksum provided for %s, skipping verification",
                    os.path.basename(file_path))
        return True

    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        calculated_hash = sha256_hash.hexdigest().lower()
        expected = (expected_sha256 or "").strip().lower()
        if ":" in expected:
            expected = expected.split(":", 1)[1]
        is_valid = calculated_hash == expected
        if not is_valid:
            log.error("Checksum mismatch for %s: expected %s, got %s",
                      os.path.basename(file_path), expected,
                      calculated_hash)
        else:
            log.debug("Checksum verified for %s", os.path.basename(file_path))
        return is_valid
    except OSError:
        log.exception("Failed to verify checksum for %s",
                      os.path.basename(file_path))
        return False


def _extract_uv_archive(archive_path, extract_dir):
    """Extract uv archive to extract_dir and check for unsafe paths."""
    if util.IS_WINDOWS:
        with zipfile.ZipFile(archive_path) as zf:
            base = os.path.abspath(extract_dir) + os.sep
            for m in zf.infolist():
                dest = os.path.abspath(os.path.join(extract_dir, m.filename))
                if not dest.startswith(base):
                    raise exception.PIOInstallerException(
                        f"Unsafe path in archive entry: {m.filename}")
                zf.extract(m, extract_dir)
    else:
        with tarfile.open(archive_path, "r:*") as tar:
            base = os.path.abspath(extract_dir) + os.sep
            for m in tar.getmembers():
                dest = os.path.abspath(os.path.join(extract_dir, m.name))
                if not dest.startswith(base):
                    raise exception.PIOInstallerException(
                        f"Unsafe path in archive entry: {m.name}")
                tar.extract(m, extract_dir)


def _find_uv_binary(extract_dir):
    """Find the uv binary in the extracted files."""
    for root, _, files in os.walk(extract_dir):
        for file in files:
            if file in ("uv", "uv.exe"):
                return os.path.join(root, file)
    return None


def _prepare_download_dirs(cache_dir, uv_platform, ext):
    """Prepare directories and paths for download."""
    os.makedirs(os.path.join(cache_dir, "tmp"), exist_ok=True)
    archive_path = os.path.join(cache_dir, "tmp", f"uv-{uv_platform}.{ext}")
    extract_dir = os.path.join(cache_dir, "tmp", "uv-extract")
    return archive_path, extract_dir


def _process_downloaded_archive(archive_path, extract_dir, cache_dir):
    """Process downloaded archive: extract and install binary."""
    util.safe_remove_dir(extract_dir)
    os.makedirs(extract_dir, exist_ok=True)
    _extract_uv_archive(archive_path, extract_dir)

    uv_binary = _find_uv_binary(extract_dir)
    if not uv_binary:
        raise exception.PIOInstallerException(
            "Could not find uv binary in downloaded archive"
        )

    uv_dest = os.path.join(cache_dir, UV_EXE)
    shutil.copy2(uv_binary, uv_dest)
    if not util.IS_WINDOWS:
        os.chmod(uv_dest, 0o755)

    log.debug("uv installed at %s", uv_dest)
    return uv_dest


def _attempt_download(config, attempt, retries):
    """Attempt a single download with checksum verification."""
    try:
        log.debug("Downloading uv from %s (attempt %d/%d)",
                 config.uv_url, attempt, retries)

        # Remove potentially corrupted file from previous attempt
        if os.path.exists(config.archive_path):
            os.remove(config.archive_path)

        util.download_file(config.uv_url, config.archive_path, cache=False)

        # Verify checksum from GitHub API
        if config.is_checksum_available():
            if not verify_download(config.archive_path,
                                 config.expected_checksum):
                raise exception.PIOInstallerException(
                    "Downloaded uv archive failed checksum verification"
                )
        else:
            log.warning("No checksum available for verification")

        return True

    except (
        requests.RequestException,
        OSError,
        exception.PIOInstallerException,
    ) as e:
        log.debug("Attempt %d/%d failed: %s", attempt, retries, str(e))
        return False


def download_and_install_uv(cache_dir, retries=DEFAULT_RETRIES,
                          retry_delay=DEFAULT_RETRY_DELAY):
    """Download and install uv package manager with retry on failure."""
    uv_platform = get_uv_platform()
    if not uv_platform:
        raise exception.PIOInstallerException(
            f"Unsupported OS/architecture for uv: "
            f"{platform.system()}/{platform.machine()}"
        )

    ext = "zip" if util.IS_WINDOWS else "tar.gz"
    uv_url = UV_URL.format(platform=uv_platform, ext=ext)

    archive_path, extract_dir = _prepare_download_dirs(
        cache_dir, uv_platform, ext)

    config = DownloadConfig(uv_url, archive_path, uv_platform, ext)
    config.set_checksum(get_expected_checksum(uv_platform, ext))

    for attempt in range(1, retries + 1):
        if not _attempt_download(config, attempt, retries):
            if attempt < retries:
                actual_delay = retry_delay * (2 ** (attempt - 1))
                log.debug("Retrying in %d seconds...", actual_delay)
                time.sleep(actual_delay)
            continue
        return _process_downloaded_archive(archive_path, extract_dir,
                                         cache_dir)

    # Clean up on final failure
    if os.path.exists(archive_path):
        try:
            os.remove(archive_path)
        except OSError:
            pass

    log.error("Failed to download/install uv after %d attempts", retries)
    return None


def get_uv_executable():
    """Get path to uv executable, download if needed."""
    # First try to find uv in PATH
    uv_exe = shutil.which("uv")
    if uv_exe and os.path.isfile(uv_exe):
        log.debug("Found uv in PATH: %s", uv_exe)
        return uv_exe

    # Try to find cached uv
    cache_dir = core.get_cache_dir()
    cached_uv = os.path.join(cache_dir, UV_EXE)

    if os.path.isfile(cached_uv) and os.access(cached_uv, os.X_OK):
        log.debug("Found cached uv: %s", cached_uv)
        return cached_uv

    # Download and install uv
    uv_exe = download_and_install_uv(cache_dir)
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


def create_core_penv(penv_dir=None, ignore_pythons=None):
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

    result_dir = None
    for python_exe in python.find_compatible_pythons(ignore_pythons):
        result_dir = create_venv_with_uv(uv_exe, python_exe, penv_dir)
        if result_dir:
            break

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


def create_venv_with_uv(uv_exe, python_exe, penv_dir):
    """Create virtual environment using uv and install uv into the venv."""

    # Remove existing directory if it exists
    util.safe_remove_dir(penv_dir)

    try:
        # Create venv with uv
        cmd = [uv_exe, "venv", "--python", python_exe, penv_dir]
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
            log.debug("Successfully created venv at %s", penv_dir)

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
