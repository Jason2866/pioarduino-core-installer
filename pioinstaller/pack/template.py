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

    data = re.sub(rb'[^A-Za-z0-9+/=]', b'', data)
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


def extract_all_modules(pioinstaller_zip, tmp_dir):
    """
    Extract ALL modules to filesystem, not just native extensions.
    
    This ensures that modules like zstandard are loaded from
    the extracted version with native extensions available.
    """
    extract_dir = os.path.join(tmp_dir, "extracted_modules")
    os.makedirs(extract_dir, exist_ok=True)
    
    try:
        with zipfile.ZipFile(pioinstaller_zip, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
            print("Extracted all modules to filesystem")
            
        # Add extracted directory to FRONT of sys.path
        sys.path.insert(0, extract_dir)
        
        # Set environment variables for native library loading
        current_ld_path = os.environ.get('LD_LIBRARY_PATH', '')
        if current_ld_path:
            os.environ['LD_LIBRARY_PATH'] = f"{extract_dir}:{current_ld_path}"
        else:
            os.environ['LD_LIBRARY_PATH'] = extract_dir
            
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
    """Bootstrap the main PlatformIO installer."""
    import pioinstaller.__main__
    pioinstaller.__main__.main()


def main():
    """Main entry point for the packed installer script."""
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

        # CRITICAL: Extract ALL modules to filesystem FIRST
        print("Extracting all modules...")
        extracted_dir = extract_all_modules(pioinstaller_zip, tmp_dir)

        # Now safe to import and run - all modules on filesystem
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
        # Cleanup
        if extracted_dir and extracted_dir in sys.path:
            sys.path.remove(extracted_dir)

        for d in (runtime_tmp_dir, tmp_dir):
            if d and os.path.isdir(d):
                try:
                    shutil.rmtree(d)
                except OSError:
                    continue


if __name__ == "__main__":
    main()
