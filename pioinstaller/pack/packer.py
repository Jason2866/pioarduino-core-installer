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
with all dependencies bundled as wheels.
"""

import base64
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile

from pioinstaller import util

log = logging.getLogger(__name__)


def create_wheels(package_dir, dest_dir):
    """
    Create wheels for all Python package dependencies.

    Args:
        package_dir (str): Directory containing the package source.
        dest_dir (str): Directory to store generated wheel files.
    """
    subprocess.check_call(["uv", "sync"], cwd=package_dir)
    subprocess.check_call(["uv", "pip", "install", "pip", "wheel"],
                         cwd=package_dir)

    # Explicitly install zstandard to ensure inclusion
    subprocess.check_call(["uv", "pip", "install", "zstandard>=0.15.0"],
                         cwd=package_dir)

    # Create wheels for all dependencies
    subprocess.check_call(
        ["uv", "run", "pip", "wheel", "--wheel-dir", dest_dir, "."],
        cwd=package_dir
    )


def _process_wheel_files(tmp_dir):
    """
    Process wheel files and create bundled zip data.

    Args:
        tmp_dir (str): Directory containing wheel files.

    Returns:
        tuple: (encoded_zip_data, wheel_names_list)
    """
    new_data = io.BytesIO()
    wheels_found = []

    for filename in os.listdir(tmp_dir):
        if not filename.endswith(".whl"):
            continue
        wheels_found.append(filename)
        filepath = os.path.join(tmp_dir, filename)

        with zipfile.ZipFile(filepath) as existing_zip:
            with zipfile.ZipFile(new_data, mode="a") as new_zip:
                for zinfo in existing_zip.infolist():
                    # Keep metadata for packages that need it
                    if re.search(r"\.dist-info/(METADATA|PKG-INFO)$",
                               zinfo.filename):
                        new_zip.writestr(zinfo, existing_zip.read(zinfo))
                    elif not re.search(r"\.dist-info/", zinfo.filename):
                        new_zip.writestr(zinfo, existing_zip.read(zinfo))

    # Verify critical dependencies are included
    critical_deps = ['zstandard', 'requests']
    for dep in critical_deps:
        if not any(dep.lower() in wheel.lower() for wheel in wheels_found):
            log.warning("Dependency %s not found in wheels: %s",
                       dep, wheels_found)

    zipdata = base64.b64encode(new_data.getvalue()).decode("utf8")
    return zipdata, wheels_found


def pack(target):
    """
    Create a packed installer script with all dependencies bundled.

    Args:
        target (str): Target path for the packed script. Can be a directory
                     or a file path.

    Returns:
        str: Path to the created packed script.
    """
    assert isinstance(target, str)

    if os.path.isdir(target):
        target = os.path.join(target, "get-platformio.py")
    if not os.path.isdir(os.path.dirname(target)):
        os.makedirs(os.path.dirname(target))

    tmp_dir = tempfile.mkdtemp()

    try:
        create_wheels(os.path.dirname(util.get_source_dir()), tmp_dir)
        zipdata, _ = _process_wheel_files(tmp_dir)

        template_path = os.path.join(util.get_source_dir(), "pack",
                                   "template.py")
        with open(target, "w", encoding="utf-8") as fp:
            with open(template_path, encoding="utf-8") as fptlp:
                fp.write(fptlp.read().format(zipfile_content=zipdata))

        # Ensure the permissions on the newly created file
        oldmode = os.stat(target).st_mode & 0o7777
        newmode = (oldmode | 0o555) & 0o7777
        os.chmod(target, newmode)

        return target

    finally:
        # Clean up temporary directory
        shutil.rmtree(tmp_dir)
