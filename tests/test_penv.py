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

import json
import os
import subprocess

from pioinstaller import __version__, penv, python, util


def test_penv_creation_with_uv(tmpdir):
    """Test basic virtual environment creation using uv."""
    penv_dir = str(tmpdir.mkdir("penv"))

    assert penv.create_core_penv(penv_dir=penv_dir)

    # Verify the virtual environment was created
    assert os.path.isdir(penv_dir)
    bin_dir = penv.get_penv_bin_dir(penv_dir)
    assert os.path.isdir(bin_dir)

    python_exe = os.path.join(bin_dir, "python.exe" if util.IS_WINDOWS else "python")
    assert os.path.isfile(python_exe)

    # Check state file was created
    with open(os.path.join(penv_dir, "state.json")) as fp:
        json_info = json.load(fp)
        assert json_info.get("installer_version") == __version__


def test_uv_installed_in_penv(tmpdir):
    """Test that uv is properly installed and functional in the penv."""
    penv_dir = str(tmpdir.mkdir("penv"))

    # Create penv which should install uv
    result_dir = penv.create_core_penv(penv_dir=penv_dir)
    assert result_dir == penv_dir

    # Check that uv binary exists in the penv
    bin_dir = penv.get_penv_bin_dir(penv_dir)
    uv_exe = os.path.join(bin_dir, "uv.exe" if util.IS_WINDOWS else "uv")
    assert os.path.isfile(uv_exe), f"uv executable not found at {uv_exe}"

    # Test that uv works by running 'uv help'
    try:
        result = subprocess.run(
            [uv_exe, "help"], capture_output=True, text=True, check=True, timeout=10
        )
        # Check that help output contains expected content
        assert "uv" in result.stdout.lower()
        assert "help" in result.stdout.lower() or "usage" in result.stdout.lower()
    except subprocess.CalledProcessError as e:
        raise AssertionError(f"uv help command failed: {e}")
    except subprocess.TimeoutExpired:
        raise AssertionError("uv help command timed out")
