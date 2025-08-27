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

import os
import subprocess
import tempfile

import pytest

from pioinstaller import __version__
from pioinstaller.pack import packer


def test_pioinstaller_packer():
    """Test that the packed installer script can be created and executed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        script_path = packer.pack(tmpdir)
        
        # Test that the script can show version (this requires the bundled dependencies)
        # If dependencies are missing, this will fail with ImportError
        try:
            output = subprocess.check_output(["python", script_path, "--version"], 
                                           stderr=subprocess.STDOUT, timeout=30)
            assert ("version %s" % __version__) in output.decode()
        except subprocess.CalledProcessError as e:
            # If it fails, let's see what the error is
            error_output = e.output.decode() if e.output else str(e)
            pytest.fail(f"Packed script failed: {error_output}")
        except subprocess.TimeoutExpired:
            pytest.fail("Packed script timed out")


def test_packer_creates_script():
    """Test that the packer can create a bundled script."""
    with tempfile.TemporaryDirectory() as tmpdir:
        script_path = packer.pack(tmpdir)
        
        # Verify the script was created
        assert os.path.isfile(script_path)
        assert script_path.endswith("get-platformio.py")
        
        # Verify it's executable
        assert os.access(script_path, os.X_OK)
        
        # Verify it has content
        with open(script_path, 'r') as f:
            content = f.read()
            assert len(content) > 1000  # Should be a substantial script
            assert 'pioinstaller' in content  # Should contain our package
