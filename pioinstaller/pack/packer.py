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
Package creation utilities for PlatformIO installer.

This module provides functionality to create a standalone installer script
with all dependencies bundled as wheels, optimized for cross-platform
deployment with Python 3.10-3.13 support.
"""

import base64
import io
import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import zipfile

from pioinstaller import util

log = logging.getLogger(__name__)

# Supported Python versions for cross-platform compatibility
PYTHON_VERSIONS = ['cp310', 'cp311', 'cp312', 'cp313']


def get_current_platform():
    """
    Detect current platform for wheel building.

    Returns:
        str: Platform tag for wheel building (e.g., 'win_amd64', 'manylinux2014_x86_64').

    Raises:
        RuntimeError: If platform is not supported.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == 'linux':
        if 'aarch64' in machine or 'arm64' in machine:
            return 'manylinux2014_aarch64'
        else:
            return 'manylinux2014_x86_64'
    elif system == 'darwin':  # macOS
        if 'arm64' in machine:
            return 'macosx_11_0_arm64'
        else:
            return 'macosx_10_9_x86_64'
    elif system == 'windows':
        if '64' in machine:
            return 'win_amd64'
        else:
            return 'win32'
    else:
        raise RuntimeError(f"Unsupported platform: {system}")


def _validate_zstandard_wheel(wheel_dir):
    """
    Validate that zstandard wheel contains required backend files.

    Args:
        wheel_dir (str): Directory containing wheel files.

    Raises:
        RuntimeError: If zstandard wheel is incomplete.
    """
    for filename in os.listdir(wheel_dir):
        if 'zstandard' in filename.lower() and filename.endswith('.whl'):
            filepath = os.path.join(wheel_dir, filename)
            with zipfile.ZipFile(filepath) as whl:
                files = whl.namelist()
                backend_files = [f for f in files if
                               ('backend' in f or '.so' in f or '.pyd' in f)]

                if not backend_files:
                    log.error("zstandard wheel %s missing backend files",
                             filename)
                    log.error("Available files: %s", files[:10])
                    raise RuntimeError(
                        "zstandard wheel missing backend files"
                    )

                log.info("zstandard wheel %s contains backend files: %s",
                       filename, backend_files[:5])


def create_cross_platform_wheels(package_dir, dest_dir):
    """
    Create wheels for current platform and all Python versions 3.10-3.13.

    This function builds wheels compatible with the current platform and
    all supported Python versions, enabling cross-version compatibility
    while maintaining platform-specific optimizations.

    Args:
        package_dir (str): Directory containing package source.
        dest_dir (str): Directory to store wheels.

    Raises:
        subprocess.CalledProcessError: If wheel creation fails.
        RuntimeError: If platform is not supported.
    """
    current_platform = get_current_platform()
    log.info("Building wheels for platform: %s", current_platform)

    subprocess.check_call(["uv", "sync"], cwd=package_dir)

    for py_ver in PYTHON_VERSIONS:
        python_version = f"{py_ver[2]}.{py_ver[3:]}"
        try:
            log.info("Creating wheels for Python %s on %s...",
                    python_version, current_platform)

            # Install dependencies for specific Python version
            subprocess.check_call([
                "uv", "pip", "install",
                "--python", python_version,
                "--only-binary=zstandard",
                "zstandard>=0.15.0"
            ], cwd=package_dir)

            # Build project wheel
            subprocess.check_call(["uv", "build", "--wheel"], cwd=package_dir)

            # Copy built wheel from dist/ to destination
            dist_dir = os.path.join(package_dir, "dist")
            if os.path.exists(dist_dir):
                for filename in os.listdir(dist_dir):
                    if filename.endswith('.whl'):
                        src_path = os.path.join(dist_dir, filename)
                        dst_path = os.path.join(dest_dir, filename)
                        shutil.copy2(src_path, dst_path)

            # Create wheels for all dependencies
            subprocess.check_call([
                "uv", "run", "--python", python_version, "pip", "wheel",
                "--wheel-dir", dest_dir,
                "--only-binary=zstandard",
                "."
            ], cwd=package_dir)

        except subprocess.CalledProcessError as e:
            log.warning("Failed to create wheel for Python %s: %s",
                       python_version, e)
            continue

    _validate_zstandard_wheel(dest_dir)


def _process_wheel_files(tmp_dir):
    """
    Process wheel files and create bundled zip data with complete inclusion.

    Args:
        tmp_dir (str): Directory containing wheel files.

    Returns:
        tuple: (encoded_zip_data, wheel_names_list)

    Raises:
        RuntimeError: If critical backend files are missing.
    """
    new_data = io.BytesIO()
    wheels_found = []
    zstandard_backend_found = False

    for filename in os.listdir(tmp_dir):
        if not filename.endswith(".whl"):
            continue
        wheels_found.append(filename)
        filepath = os.path.join(tmp_dir, filename)

        with zipfile.ZipFile(filepath) as existing_zip:
            if 'zstandard' in filename.lower():
                backend_files = [f for f in existing_zip.namelist()
                               if ('backend' in f or '.so' in f or
                                   '.pyd' in f)]
                if backend_files:
                    zstandard_backend_found = True
                    log.info("Found zstandard backend files: %s",
                           backend_files[:3])
                if not backend_files:
                    log.warning("No backend files in zstandard wheel: %s",
                              filename)

            with zipfile.ZipFile(new_data, mode="a") as new_zip:
                for zinfo in existing_zip.infolist():
                    if 'zstandard' in filepath.lower():
                        new_zip.writestr(zinfo, existing_zip.read(zinfo))
                    elif re.search(r"\.dist-info/(METADATA|PKG-INFO)$",
                                 zinfo.filename):
                        new_zip.writestr(zinfo, existing_zip.read(zinfo))
                    elif not re.search(r"\.dist-info/", zinfo.filename):
                        new_zip.writestr(zinfo, existing_zip.read(zinfo))

    if not zstandard_backend_found:
        raise RuntimeError("zstandard backend files not found")

    critical_deps = ['requests']
    for dep in critical_deps:
        if not any(dep.lower() in wheel.lower() for wheel in wheels_found):
            log.warning("Dependency %s not found in wheels: %s",
                       dep, wheels_found)

    zipdata = base64.b64encode(new_data.getvalue()).decode("utf8")
    return zipdata, wheels_found


def pack(target):
    """
    Create a packed installer script with cross-platform dependencies.

    This function creates a standalone installer script that includes all
    necessary dependencies as wheels, with automatic platform and Python
    version detection for maximum compatibility.

    Args:
        target (str): Target path for the packed script. Can be a directory
                     or a file path.

    Returns:
        str: Path to the created packed script.

    Raises:
        AssertionError: If target is not a string.
        RuntimeError: If critical dependencies are missing.
        OSError: If file operations fail.
    """
    assert isinstance(target, str)

    if os.path.isdir(target):
        target = os.path.join(target, "get-platformio.py")
    if not os.path.isdir(os.path.dirname(target)):
        os.makedirs(os.path.dirname(target))

    tmp_dir = tempfile.mkdtemp()

    try:
        create_cross_platform_wheels(
            os.path.dirname(util.get_source_dir()), tmp_dir
        )
        zipdata, wheels_list = _process_wheel_files(tmp_dir)

        log.info("Successfully bundled wheels: %s", len(wheels_list))

        template_path = os.path.join(util.get_source_dir(), "pack",
                                   "template.py")

        with open(template_path, encoding="utf-8") as fptlp:
            content = fptlp.read()

        # Simple string replacement to avoid template complexity
        result = content.replace('$zipfile_content', zipdata)

        with open(target, "w", encoding="utf-8") as fp:
            fp.write(result)

        oldmode = os.stat(target).st_mode & 0o7777
        newmode = (oldmode | 0o555) & 0o7777
        os.chmod(target, newmode)

        log.info("Successfully created packed installer: %s", target)
        return target

    finally:
        try:
            shutil.rmtree(tmp_dir)
        except OSError as e:
            log.warning("Failed to clean up temporary directory %s: %s",
                       tmp_dir, e)
