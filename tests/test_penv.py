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

import pytest

from pioinstaller import __version__, penv, util


def test_penv_creation_with_uv(tmpdir):
    """Test basic virtual environment creation using uv."""
    penv_dir = str(tmpdir.mkdir("penv"))
    uv_exe = penv.get_uv_executable()
    assert uv_exe is not None, "uv executable not found"

    assert penv.create_core_penv(uv_exe, penv_dir=penv_dir) == penv_dir

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


def test_uv_installed_in_penv(prepared_penv):
    """Test that uv is properly installed and functional in the penv after creation."""
    penv_dir = prepared_penv
    # Check that uv binary exists in the penv
    bin_dir = penv.get_penv_bin_dir(penv_dir)
    uv_exe = os.path.join(bin_dir, "uv.exe" if util.IS_WINDOWS else "uv")
    assert os.path.isfile(uv_exe), f"uv executable not found at {uv_exe}"
    # Test that uv works by running 'uv help'
    try:
        result = subprocess.run(
            [uv_exe, "help"], capture_output=True, text=True, check=True, timeout=10
        )
        print("\nUV Help Output:")
        print(result.stdout)
        assert "uv" in result.stdout.lower()
        assert "help" in result.stdout.lower() or "usage" in result.stdout.lower()
    except subprocess.CalledProcessError as e:
        raise AssertionError(f"uv help command failed: {e}")
    except subprocess.TimeoutExpired:
        raise AssertionError("uv help command timed out")


@pytest.fixture(scope="module")
def prepared_penv(tmp_path_factory):
    penv_dir = str(tmp_path_factory.mktemp("penv"))
    uv_exe = penv.get_uv_executable()
    assert uv_exe is not None, "uv executable not found"
    result_dir = penv.create_core_penv(uv_exe, penv_dir=penv_dir)
    assert result_dir == penv_dir
    # Install uv in the penv like the full install flow does
    penv.install_uv_in_venv_with_system_uv(uv_exe, penv_dir)
    return penv_dir


def test_uv_platform_tag():
    """Test that _get_uv_platform_tag returns a valid tag for the current platform."""
    tag = penv._get_uv_platform_tag()
    assert tag is not None, "No platform tag for current system"
    assert tag.startswith("uv-")


def test_install_uv_download(tmpdir):
    """Test the requests-based fallback downloads a working uv binary."""
    cache_dir = str(tmpdir.mkdir("cache"))
    uv_path = penv.install_uv_download(cache_dir)

    assert uv_path is not None, "install_uv_download returned None"
    assert os.path.isfile(uv_path)
    assert os.path.getsize(uv_path) > 0

    # Verify the downloaded binary actually works
    result = subprocess.run(
        [uv_path, "--version"], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0
    assert "uv" in result.stdout.lower()


def test_uv_help_in_existing_penv(prepared_penv):
    """Test that uv works in an already existing penv (no creation in this test)."""
    penv_dir = prepared_penv
    bin_dir = penv.get_penv_bin_dir(penv_dir)
    uv_exe = os.path.join(bin_dir, "uv.exe" if util.IS_WINDOWS else "uv")
    assert os.path.isfile(uv_exe), f"uv executable not found at {uv_exe}"
    result = subprocess.run(
        [uv_exe, "help"], capture_output=True, text=True, check=True, timeout=10
    )
    print("\nUV Help Output (existing venv):")
    print(result.stdout)
    assert "uv" in result.stdout.lower()
    assert "help" in result.stdout.lower() or "usage" in result.stdout.lower()
