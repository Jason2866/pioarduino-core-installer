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
Cross-platform PlatformIO installer template.

This template provides a standalone installer that automatically detects
the current platform and Python version (3.10-3.13) to extract and load
compatible dependencies, including native extensions.

Supported platforms: Linux, Windows, macOS
Supported Python versions: 3.10, 3.11, 3.12, 3.13
"""

# pylint:disable=bad-option-value,import-outside-toplevel

import os
import re
import shutil
import sys
import tempfile
import zipfile

DEPENDENCIES = b"""
$zipfile_content
"""


def decode_base64_padded(data):
    """
    Safely decode base64 data with automatic padding correction.

    This function handles improperly padded base64 data by cleaning
    non-base64 characters and adding missing padding as needed.

    Args:
        data (bytes): Base64 encoded data that may have incorrect padding.

    Returns:
        bytes: Decoded binary data.

    Raises:
        ValueError: If data cannot be decoded after padding correction.
    """
    import base64

    # Clean non-base64 characters
    data = re.sub(rb'[^A-Za-z0-9+/=]', b'', data)

    # Add missing padding
    missing_padding = len(data) % 4
    if missing_padding:
        data += b'=' * (4 - missing_padding)

    return base64.b64decode(data)


def create_temp_dir():
    """
    Create a temporary directory for the installer.

    This function attempts to create a temporary directory in the preferred
    location specified by PLATFORMIO_INSTALLER_TMPDIR environment variable,
    falling back to the system default temporary directory if needed.

    Returns:
        str: Path to the created temporary directory.

    Raises:
        OSError: If temporary directory creation fails.
    """
    try:
        parent_dir = os.getenv(
            "PLATFORMIO_INSTALLER_TMPDIR",
            os.path.dirname(os.path.realpath(__file__))
        )
        tmp_dir = tempfile.mkdtemp(dir=parent_dir,
                                  prefix=".piocore-installer-")
        testscript_path = os.path.join(tmp_dir, "test.py")
        with open(testscript_path, "w", encoding="utf-8") as fp:
            fp.write("print(1)")
        assert os.path.isfile(testscript_path)
        os.remove(testscript_path)
        return tmp_dir
    except (AssertionError, NameError):
        return tempfile.mkdtemp()


def get_python_version_tag():
    """
    Get current Python version tag for supported versions (3.10-3.13).

    Returns:
        str: Python version tag (e.g., 'cp310', 'cp311').

    Raises:
        RuntimeError: If Python version is not supported.
    """
    version_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"

    # Validate supported Python version
    supported_versions = ['cp310', 'cp311', 'cp312', 'cp313']
    if version_tag not in supported_versions:
        raise RuntimeError(
            f"Unsupported Python version {version_tag}. "
            f"Supported versions: {', '.join(supported_versions)}"
        )

    return version_tag


def get_platform_tag():
    """
    Get current platform tag for wheel selection.

    This function detects the current operating system and architecture
    to select appropriate wheels for the platform.

    Returns:
        str: Platform tag for wheel selection.
    """
    import platform

    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == 'linux':
        if 'aarch64' in machine or 'arm64' in machine:
            return 'linux_aarch64'
        else:
            return 'linux_x86_64'
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
        return 'unknown'


def find_compatible_modules(zip_ref):
    """
    Find modules compatible with current Python version and platform.

    This function analyzes the contents of a ZIP archive to identify
    modules and wheels that are compatible with the current Python
    version and platform.

    Args:
        zip_ref (ZipFile): Open zipfile object containing modules and wheels.

    Returns:
        list: List of compatible module file paths within the ZIP archive.
    """
    py_tag = get_python_version_tag()
    platform_tag = get_platform_tag()
    compatible_files = []

    print(f"Looking for wheels: {py_tag} on {platform_tag}")

    for member in zip_ref.namelist():
        # Include all non-wheel files (Python modules, data files, etc.)
        if not member.endswith('.whl'):
            compatible_files.append(member)
        # Include wheels matching current Python version AND platform
        elif f"-{py_tag}-" in member and any(
            plat in member for plat in [platform_tag, 'any']
        ):
            compatible_files.append(member)
            print(f"Found compatible wheel: {member}")
        # Include universal wheels (compatible with all Python versions)
        elif "-py3-none-any.whl" in member:
            compatible_files.append(member)
        # Fallback: Any wheel for our Python version (may not work due to platform)
        elif f"-{py_tag}-" in member and not compatible_files:
            compatible_files.append(member)
            print(f"Using fallback wheel: {member}")

    return compatible_files


def extract_compatible_modules(pioinstaller_zip, tmp_dir):
    """
    Extract modules compatible with current Python version and platform.

    This function extracts only the modules and wheels that are compatible
    with the current Python version and platform, ensuring that native
    extensions can be properly loaded.

    Args:
        pioinstaller_zip (str): Path to the ZIP file containing dependencies.
        tmp_dir (str): Temporary directory for extraction.

    Returns:
        str or None: Path to extracted modules directory if successful,
                    None if extraction failed.

    Raises:
        ImportError: If no compatible modules are found.
    """
    extract_dir = os.path.join(tmp_dir, "extracted_modules")
    os.makedirs(extract_dir, exist_ok=True)

    py_tag = get_python_version_tag()
    platform_tag = get_platform_tag()
    print(f"Extracting modules for Python {py_tag} on {platform_tag}...")

    try:
        with zipfile.ZipFile(pioinstaller_zip, 'r') as zip_ref:
            compatible_files = find_compatible_modules(zip_ref)

            if not compatible_files:
                raise ImportError(
                    f"No compatible modules found for Python {py_tag} "
                    f"on {platform_tag}"
                )

            # Extract compatible files to filesystem
            extracted_count = 0
            for member in compatible_files:
                try:
                    zip_ref.extract(member, extract_dir)
                    extracted_count += 1
                except (OSError, zipfile.BadZipFile) as e:
                    print(f"Warning: Failed to extract {member}: {e}")
                    continue

            print(f"Extracted {extracted_count} compatible files")

        # Add extracted directory to FRONT of sys.path for import priority
        sys.path.insert(0, extract_dir)

        # Set environment variables for native library loading on Linux/macOS
        current_ld_path = os.environ.get('LD_LIBRARY_PATH', '')
        if current_ld_path:
            os.environ['LD_LIBRARY_PATH'] = f"{extract_dir}:{current_ld_path}"
        else:
            os.environ['LD_LIBRARY_PATH'] = extract_dir

        # Set environment variables for DLL loading on Windows
        current_path = os.environ.get('PATH', '')
        if current_path:
            os.environ['PATH'] = f"{extract_dir};{current_path}"
        else:
            os.environ['PATH'] = extract_dir

        print(f"Added extracted modules path: {extract_dir}")
        return extract_dir

    except (OSError, zipfile.BadZipFile) as e:
        print(f"Error extracting modules: {e}")
        return None


def bootstrap():
    """
    Bootstrap the main PlatformIO installer.

    This function imports and runs the main PlatformIO installer module
    after all dependencies have been extracted and configured.

    Raises:
        ImportError: If PlatformIO installer module cannot be imported.
    """
    import pioinstaller.__main__
    pioinstaller.__main__.main()


def main():
    """
    Main entry point for the packed installer script.

    This function orchestrates the entire installation process:
    1. Detects Python version and platform compatibility
    2. Extracts compatible modules and dependencies
    3. Configures environment for native extensions
    4. Launches the PlatformIO installer

    The function includes comprehensive error handling and cleanup
    to ensure a robust installation experience across all supported
    platforms and Python versions.

    Raises:
        SystemExit: If Python version or platform is not supported.
    """
    try:
        py_tag = get_python_version_tag()
        platform_tag = get_platform_tag()
        print(f"Starting installer with Python {py_tag} on {platform_tag}")
        print("Supported: Python 3.10-3.13 on Linux/Windows/macOS")
    except RuntimeError as e:
        print(f"Error: {e}")
        print("Please use Python 3.10, 3.11, 3.12, or 3.13")
        sys.exit(1)

    runtime_tmp_dir = create_temp_dir()
    os.environ["TMPDIR"] = runtime_tmp_dir
    tmp_dir = tempfile.mkdtemp(dir=runtime_tmp_dir)
    extracted_dir = None
    pioinstaller_zip = None

    try:
        # Create ZIP file from embedded base64 data
        pioinstaller_zip = os.path.join(tmp_dir, "pioinstaller.zip")
        with open(pioinstaller_zip, "wb") as fp:
            fp.write(decode_base64_padded(DEPENDENCIES))

        # Extract modules compatible with current Python version and platform
        extracted_dir = extract_compatible_modules(pioinstaller_zip, tmp_dir)

        if not extracted_dir:
            raise RuntimeError("Failed to extract compatible modules")

        # Launch PlatformIO installer with configured environment
        print("Starting PlatformIO installer...")
        bootstrap()

    except KeyboardInterrupt:
        print("Installation interrupted by user")
        raise
    except ImportError as e:
        print(f"Import error: {e}")
        print("This may be due to incompatible Python version or "
              "missing dependencies")
        raise
    except OSError as e:
        print(f"File system error: {e}")
        raise

    finally:
        # Cleanup: Remove temporary paths and directories
        if extracted_dir and extracted_dir in sys.path:
            sys.path.remove(extracted_dir)

        # Clean up all temporary directories
        for d in (runtime_tmp_dir, tmp_dir):
            if d and os.path.isdir(d):
                try:
                    shutil.rmtree(d)
                except OSError:
                    # Ignore cleanup errors to prevent masking original exceptions
                    continue


if __name__ == "__main__":
    main()
