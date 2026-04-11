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
import sys

import pytest

from pioinstaller import python


@pytest.mark.skipif(
    sys.version_info[:2] != (3, 13), reason="Only Python 3.13 is supported"
)
def test_python_version_compatibility():
    """Test that we correctly identify Python 3.13 compatibility."""

    # Test the check function
    try:
        python.check()  # Should not raise exception for compatible Python
    except Exception as e:
        pytest.fail(f"Compatible Python failed check: {e}")


def test_find_compatible_pythons():
    """Test finding compatible Python 3.13 executables."""
    pythons = python.find_compatible_pythons()

    # Should find at least the current Python
    assert len(pythons) >= 1

    # All found pythons should be valid executables
    for python_exe in pythons:
        assert os.path.isfile(python_exe)
        assert os.access(python_exe, os.X_OK)
