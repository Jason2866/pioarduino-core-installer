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
