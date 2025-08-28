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

"""
Utility functions for PlatformIO installer.

This module provides essential utility functions for file operations,
archive extraction, system detection, and Python environment management.
"""

import hashlib
import io
import logging
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

import requests

# Platform detection constants
IS_WINDOWS = sys.platform.lower().startswith("win")
IS_MACOS = sys.platform.lower() == "darwin"

log = logging.getLogger(__name__)


def get_source_dir():
    """
    Get the directory containing the current source file.
    
    Returns:
        str: Absolute path to the directory containing this module.
    """
    curpath = os.path.realpath(__file__)
    if not os.path.isfile(curpath):
        for p in sys.path:
            if os.path.isfile(os.path.join(p, __file__)):
                curpath = os.path.join(p, __file__)
                break
    return os.path.dirname(curpath)


def get_pythonexe_path():
    """
    Get the path to the current Python executable.
    
    Returns:
        str: Normalized path to the Python executable.
        
    Note:
        Respects PYTHONEXEPATH environment variable if set.
    """
    return os.environ.get("PYTHONEXEPATH", os.path.normpath(sys.executable))


def expanduser(path):
    """
    Expand user home directory path.
    
    Args:
        path (str): Path that may contain ~ for home directory.
        
    Returns:
        str: Expanded path with ~ replaced by actual home directory.
    """
    return os.path.expanduser(path)


def has_non_ascii_char(text):
    """
    Check if text contains non-ASCII characters.
    
    Args:
        text (str): Text to check for non-ASCII characters.
        
    Returns:
        bool: True if text contains characters with ordinal value >= 128.
    """
    for c in text:
        if ord(c) >= 128:
            return True
    return False


def rmtree(path):
    """
    Remove directory tree with enhanced error handling.
    
    Args:
        path (str): Path to directory to remove.
        
    Note:
        In Python 3.10+, shutil.rmtree handles readonly files better on Windows.
        This function provides fallback handling for stubborn readonly files.
    """
    try:
        # Try the simple approach first - works well in Python 3.10+
        return shutil.rmtree(path)
    except PermissionError:
        # Fallback for stubborn readonly files on Windows
        def handle_remove_readonly(func, path, exc):
            if exc[1].errno == 13:  # Permission denied
                os.chmod(path, stat.S_IWRITE)
                func(path)

        return shutil.rmtree(path, onerror=handle_remove_readonly)


def find_file(name, path):
    """
    Recursively search for a file by name in a directory tree.
    
    Args:
        name (str): Name of the file to search for.
        path (str): Root directory to start search from.
        
    Returns:
        str or None: Full path to the file if found, None otherwise.
    """
    for root, _, files in os.walk(path):
        if name in files:
            return os.path.join(root, name)
    return None


def safe_create_dir(path, raise_exception=False):
    """
    Safely create a directory, handling existing directories gracefully.
    
    Args:
        path (str): Path to directory to create.
        raise_exception (bool): Whether to raise exceptions on failure.
        
    Returns:
        str or None: Path to created directory on success, None on failure.
        
    Raises:
        Exception: If raise_exception is True and directory creation fails.
    """
    try:
        os.makedirs(path)
        return path
    except Exception as e:  # pylint: disable=broad-except
        if raise_exception:
            raise e
    return None


def calculate_file_sha256(filepath):
    """
    Calculate SHA256 hash of a file.
    
    Args:
        filepath (str): Path to the file to hash.
        
    Returns:
        str: SHA256 hash as hexadecimal string.
        
    Note:
        Uses 4KB chunks for memory-efficient processing of large files.
    """
    hash_sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()


def verify_file_integrity(filepath, expected_sha):
    """
    Verify file integrity using SHA256 checksum.
    
    Args:
        filepath (str): Path to the file to verify.
        expected_sha (str): Expected SHA256 hash (may include 'sha256:' prefix).
        
    Returns:
        bool: True if verification passes, False otherwise.
        
    Note:
        Automatically strips 'sha256:' prefix from expected hash if present.
    """
    try:
        actual_sha = calculate_file_sha256(filepath)
        expected_sha_clean = expected_sha.replace('sha256:', '').lower()
        actual_sha_clean = actual_sha.lower()
        
        if actual_sha_clean == expected_sha_clean:
            log.debug(f"File integrity verified: {os.path.basename(filepath)}")
            return True
        else:
            log.error(f"File integrity check failed: expected {expected_sha_clean}, got {actual_sha_clean}")
            return False
    except Exception as err:
        log.error(f"SHA256 verification failed: {err}")
        return False


def download_file(url, dst, cache=True):
    """
    Download a file from URL with caching support.
    
    Args:
        url (str): URL to download from.
        dst (str): Destination path for downloaded file.
        cache (bool): Whether to use cached file if available and same size.
        
    Returns:
        str: Path to downloaded file.
        
    Raises:
        requests.RequestException: If download fails.
        
    Note:
        Creates destination directory if it doesn't exist.
        Uses streaming download for memory efficiency.
    """
    if cache:
        try:
            content_length = requests.head(url, timeout=10).headers.get("Content-Length")
            if os.path.isfile(dst) and content_length and int(content_length) == os.path.getsize(dst):
                log.debug("Getting from cache: %s", dst)
                return dst
        except (requests.RequestException, ValueError):
            pass  # Continue with download if cache check fails

    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    itercontent = resp.iter_content(chunk_size=io.DEFAULT_BUFFER_SIZE)
    safe_create_dir(os.path.dirname(dst))
    with open(dst, "wb") as fp:
        for chunk in itercontent:
            fp.write(chunk)
    return dst


def extract_tar_gz(source, destination):
    """
    Extract gzip compressed tar archive.
    
    Args:
        source (str): Path to .tar.gz file to extract.
        destination (str): Directory to extract contents to.
        
    Returns:
        str: Path to destination directory.
        
    Note:
        Uses data filter for security in Python 3.12+ to prevent directory traversal attacks.
    """
    with tarfile.open(source, 'r:gz') as tar:
        # Use data filter for security (Python 3.12+)
        if hasattr(tarfile, 'data_filter'):
            tar.extractall(path=destination, filter='data')
        else:
            tar.extractall(path=destination)
    return destination


def extract_tar_zst(source, destination):
    """
    Extract zstandard compressed tar archive.
    
    Args:
        source (str): Path to .tar.zst file to extract.
        destination (str): Directory to extract contents to.
        
    Returns:
        str: Path to destination directory.
        
    Raises:
        ImportError: If zstandard library is not available.
        
    Note:
        Requires 'zstandard' package to be installed.
        Uses streaming decompression for memory efficiency.
    """
    import zstandard as zstd
    
    with open(source, 'rb') as compressed_file:
        dctx = zstd.ZstdDecompressor()
        with dctx.stream_reader(compressed_file) as reader:
            with tarfile.open(fileobj=reader, mode='r|') as tar:
                # Use data filter for security (Python 3.12+)
                if hasattr(tarfile, 'data_filter'):
                    tar.extractall(path=destination, filter='data')
                else:
                    tar.extractall(path=destination)
    
    return destination


def unpack_archive(src, dst):
    """
    Extract archive with automatic format detection.
    
    Args:
        src (str): Path to archive file to extract.
        dst (str): Directory to extract contents to.
        
    Returns:
        str: Path to destination directory.
        
    Raises:
        ValueError: If archive format is not supported.
        
    Supported formats:
        - .tar.gz (gzip compressed tar)
        - .tar.zst (zstandard compressed tar)
    """
    filename = os.path.basename(src)
    
    if filename.endswith('.tar.zst'):
        return extract_tar_zst(src, dst)
    elif filename.endswith('.tar.gz'):
        return extract_tar_gz(src, dst)
    else:
        # Fallback for legacy support
        if src.endswith("tar.gz"):
            return extract_tar_gz(src, dst)
        else:
            raise ValueError(f"Unsupported archive format: {filename}")


def get_installer_script():
    """
    Get the absolute path to the installer script.
    
    Returns:
        str: Absolute path to the current script file.
    """
    return os.path.abspath(sys.argv[0])


def get_systype():
    """
    Get system type compatible with astral-sh python-build-standalone naming.
    
    Returns:
        str: System identifier in format like 'darwin-x64', 'linux-x64', 'win32-x64'.
        
    Note:
        Normalizes platform.system() and platform.machine() output to match
        the naming conventions used by astral-sh/python-build-standalone releases.
        
    Examples:
        - macOS Intel: 'darwin-x64'
        - macOS Apple Silicon: 'darwin-arm64'
        - Linux x64: 'linux-x64'
        - Windows x64: 'win32-x64'
        - Windows x86: 'win32-ia32'
    """
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    # Normalize system names
    if system == "windows":
        system = "win32"
    elif system == "darwin":
        system = "darwin"
    elif system == "linux":
        system = "linux"
    
    # Normalize architecture names
    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("i386", "i686", "x86"):
        arch = "ia32" if system == "win32" else "x86"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine.startswith("armv7"):
        arch = "armv7l"
    elif machine.startswith("arm"):
        arch = "arm"
    else:
        arch = machine
    
    # Handle Windows architecture detection
    if system == "win32":
        arch = "x64" if platform.architecture()[0] == "64bit" else "ia32"
    
    return f"{system}-{arch}"


def safe_remove_dir(path, raise_exception=False):
    """
    Safely remove a directory tree, handling errors gracefully.
    
    Args:
        path (str): Path to directory to remove.
        raise_exception (bool): Whether to raise exceptions on failure.
        
    Returns:
        None: Always returns None (for compatibility).
        
    Raises:
        Exception: If raise_exception is True and removal fails.
        
    Note:
        Uses rmtree() internally which handles readonly files on Windows.
    """
    try:
        return rmtree(path)
    except Exception as e:  # pylint: disable=broad-except
        if raise_exception:
            raise e
    return None


def pepver_to_semver(pepver):
    """
    Convert PEP 440 version string to semantic version format.
    
    Args:
        pepver (str): Version string in PEP 440 format.
        
    Returns:
        str: Version string in semantic version format.
        
    Note:
        Converts development/pre-release identifiers to semantic version format:
        - 1.0.0.dev1 -> 1.0.0-dev.1
        - 1.0.0a1 -> 1.0.0-a.1
        - 1.0.0rc1 -> 1.0.0-rc.1
    """
    return re.sub(r"(\.\d+)\.?(dev|a|b|rc|post)", r"\1-\2.", pepver, 1)


def where_is_program(program, envpath=None):
    """
    Find the location of a program in the system PATH.
    
    Args:
        program (str): Name of the program to find.
        envpath (str, optional): Custom PATH to search in. If None, uses system PATH.
        
    Returns:
        str: Full path to program if found, otherwise returns the program name unchanged.
        
    Note:
        Uses OS-specific tools (where/which) first, then falls back to manual PATH search.
        On Windows, automatically checks for .exe extension.
    """
    env = os.environ
    if envpath:
        env["PATH"] = envpath

    # Try OS's built-in commands
    try:
        result = (
            subprocess.check_output(
                ["where" if IS_WINDOWS else "which", program], env=env
            )
            .decode()
            .strip()
        )
        if os.path.isfile(result):
            return result
    except (subprocess.CalledProcessError, OSError):
        pass

    # Look up in $PATH manually
    for bin_dir in env.get("PATH", "").split(os.pathsep):
        if os.path.isfile(os.path.join(bin_dir, program)):
            return os.path.join(bin_dir, program)
        if os.path.isfile(os.path.join(bin_dir, "%s.exe" % program)):
            return os.path.join(bin_dir, "%s.exe" % program)

    return program
