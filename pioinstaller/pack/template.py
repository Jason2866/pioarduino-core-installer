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

    Args:
        data (bytes): Base64 encoded data.

    Returns:
        bytes: Decoded binary data.
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
    """Create a temporary directory for the installer."""
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


def _is_native_extension(member_name):
    """Check if file is a native extension that needs extraction."""
    return (member_name.endswith(('.so', '.pyd', '.dll', '.dylib')) or
            '/backend_c.' in member_name or
            '_cffi.' in member_name or
            'backend_cffi.py' in member_name)


def _extract_single_member(zip_ref, member, native_extensions_dir):
    """Extract a single member from zip archive."""
    try:
        zip_ref.extract(member, native_extensions_dir)
        print(f"Extracted native extension: {member}")
        return True
    except (OSError, zipfile.BadZipFile) as e:
        print(f"Warning: Failed to extract {member}: {e}")
        return False


def _create_direct_access_link(member, native_extensions_dir):
    """Create direct access link for zstandard files."""
    if 'zstandard/' not in member:
        return

    source_path = os.path.join(native_extensions_dir, member)
    direct_path = os.path.join(native_extensions_dir,
                             os.path.basename(member))

    if os.path.exists(direct_path):
        return

    try:
        os.symlink(source_path, direct_path)
    except OSError:
        # Fallback: Copy file if symlink fails
        shutil.copy2(source_path, direct_path)


def _setup_environment_paths(native_extensions_dir):
    """Setup environment variables for native extension loading."""
    # Add to sys.path
    sys.path.insert(0, native_extensions_dir)

    # Add zstandard subdirectory if exists
    zstandard_dir = os.path.join(native_extensions_dir, "zstandard")
    if os.path.exists(zstandard_dir):
        sys.path.insert(0, zstandard_dir)

    # Setup LD_LIBRARY_PATH for Linux
    current_ld_path = os.environ.get('LD_LIBRARY_PATH', '')
    if current_ld_path:
        new_ld_path = f"{native_extensions_dir}:{zstandard_dir}:{current_ld_path}"
    else:
        new_ld_path = f"{native_extensions_dir}:{zstandard_dir}"
    os.environ['LD_LIBRARY_PATH'] = new_ld_path

    # Setup PATH for Windows
    current_path = os.environ.get('PATH', '')
    if current_path:
        new_path = f"{native_extensions_dir};{zstandard_dir};{current_path}"
    else:
        new_path = f"{native_extensions_dir};{zstandard_dir}"
    os.environ['PATH'] = new_path

    print(f"Added paths: {native_extensions_dir}, {zstandard_dir}")


def extract_native_extensions(pioinstaller_zip, tmp_dir):
    """
    Extract native extensions (.so, .pyd, .dll) to filesystem BEFORE import.

    This is CRITICAL: Python cannot import native extensions from ZIP archives.
    We must extract them to real filesystem first.

    Args:
        pioinstaller_zip (str): Path to the ZIP file containing dependencies.
        tmp_dir (str): Temporary directory for extraction.

    Returns:
        str or None: Path to native extensions directory if extracted,
                    None otherwise.
    """
    native_extensions_dir = os.path.join(tmp_dir, "native_extensions")
    os.makedirs(native_extensions_dir, exist_ok=True)

    extracted_count = 0

    try:
        with zipfile.ZipFile(pioinstaller_zip, 'r') as zip_ref:
            for member in zip_ref.namelist():
                if not _is_native_extension(member):
                    continue

                # Extract with correct directory structure
                if _extract_single_member(zip_ref, member,
                                        native_extensions_dir):
                    extracted_count += 1
                    # Create direct access links for zstandard files
                    _create_direct_access_link(member, native_extensions_dir)

        if extracted_count > 0:
            _setup_environment_paths(native_extensions_dir)
            print(f"Extracted {extracted_count} native extensions successfully")
            return native_extensions_dir

    except (OSError, zipfile.BadZipFile) as e:
        print(f"Error during native extension extraction: {e}")

    return None


def bootstrap():
    """Bootstrap the main PlatformIO installer."""
    import pioinstaller.__main__

    pioinstaller.__main__.main()


def main():
    """Main entry point for the packed installer script."""
    runtime_tmp_dir = create_temp_dir()
    os.environ["TMPDIR"] = runtime_tmp_dir
    tmp_dir = tempfile.mkdtemp(dir=runtime_tmp_dir)
    native_dir = None
    pioinstaller_zip = None

    try:
        # Create ZIP file from embedded base64 data
        pioinstaller_zip = os.path.join(tmp_dir, "pioinstaller.zip")
        with open(pioinstaller_zip, "wb") as fp:
            fp.write(decode_base64_padded(DEPENDENCIES))

        # CRITICAL: Extract native extensions BEFORE adding ZIP to sys.path
        print("Extracting native extensions...")
        native_dir = extract_native_extensions(pioinstaller_zip, tmp_dir)

        # Only now add the main ZIP to sys.path for Python modules
        sys.path.insert(0, pioinstaller_zip)

        # Now safe to import and run - native extensions are on filesystem
        print("Starting PlatformIO installer...")
        bootstrap()

    except KeyboardInterrupt:
        print("Installation interrupted by user")
        raise
    except ImportError as e:
        print(f"Import error in packed installer: {e}")
        raise
    except OSError as e:
        print(f"File system error in packed installer: {e}")
        raise

    finally:
        # Cleanup: Remove from sys.path and delete temporary directories
        if pioinstaller_zip and pioinstaller_zip in sys.path:
            sys.path.remove(pioinstaller_zip)

        if native_dir and native_dir in sys.path:
            sys.path.remove(native_dir)

        # Clean up temporary directories
        for d in (runtime_tmp_dir, tmp_dir):
            if d and os.path.isdir(d):
                try:
                    shutil.rmtree(d)
                except OSError:
                    # Ignore cleanup errors
                    continue


if __name__ == "__main__":
    main()
