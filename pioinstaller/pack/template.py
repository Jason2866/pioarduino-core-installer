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
        pass
    return tempfile.mkdtemp()


def extract_native_extensions(pioinstaller_zip, tmp_dir):
    """
    Extract native extensions (.so, .pyd, .dll) to filesystem for loading.

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
                # Check if this is a native extension file
                if (member.endswith(('.so', '.pyd', '.dll')) or
                    '/backend_c.' in member or
                        '_cffi.' in member):

                    # Extract to native extensions directory
                    try:
                        zip_ref.extract(member, native_extensions_dir)
                        extracted_count += 1
                    except Exception:  # pylint: disable=broad-except
                        # Continue if individual extraction fails
                        continue

        if extracted_count > 0:
            # Add the native extensions directory to Python path
            sys.path.insert(0, native_extensions_dir)

            # Also set LD_LIBRARY_PATH for Linux shared libraries
            current_ld_path = os.environ.get('LD_LIBRARY_PATH', '')
            if current_ld_path:
                os.environ['LD_LIBRARY_PATH'] = (
                    f"{native_extensions_dir}:{current_ld_path}"
                )
            else:
                os.environ['LD_LIBRARY_PATH'] = native_extensions_dir

    except Exception:  # pylint: disable=broad-except
        # If extraction fails completely, continue without native extensions
        pass

    return native_extensions_dir if extracted_count > 0 else None


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
        pioinstaller_zip = os.path.join(tmp_dir, "pioinstaller.zip")
        with open(pioinstaller_zip, "wb") as fp:
            # Use safe base64 decode with padding correction
            fp.write(decode_base64_padded(DEPENDENCIES))

        # Extract native extensions before adding zip to sys.path
        native_dir = extract_native_extensions(pioinstaller_zip, tmp_dir)

        # Add the main zip to sys.path for Python modules
        sys.path.insert(0, pioinstaller_zip)

        bootstrap()
    finally:
        # Cleanup: Remove from sys.path and delete temporary directories
        if pioinstaller_zip and pioinstaller_zip in sys.path:
            sys.path.remove(pioinstaller_zip)

        if native_dir and native_dir in sys.path:
            sys.path.remove(native_dir)

        for d in (runtime_tmp_dir, tmp_dir):
            if d and os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
