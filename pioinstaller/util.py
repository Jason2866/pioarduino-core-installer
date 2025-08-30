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

import io
import logging
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile

import requests

IS_WINDOWS = sys.platform.lower().startswith("win")
IS_MACOS = sys.platform.lower() == "darwin"

log = logging.getLogger(__name__)


def get_source_dir():
    curpath = os.path.realpath(__file__)
    if not os.path.isfile(curpath):
        for p in sys.path:
            if os.path.isfile(os.path.join(p, __file__)):
                curpath = os.path.join(p, __file__)
                break
    return os.path.dirname(curpath)


def get_pythonexe_path():
    return os.environ.get("PYTHONEXEPATH", os.path.normpath(sys.executable))


def expanduser(path):
    """
    Expand user home directory path.
    """
    return os.path.expanduser(path)


def has_non_ascii_char(text):
    for c in text:
        if ord(c) >= 128:
            return True
    return False


def rmtree(path):
    def _onerror(func, path, __):
        st_mode = os.stat(path).st_mode
        if st_mode & stat.S_IREAD:
            os.chmod(path, st_mode | stat.S_IWRITE)
        func(path)

    return shutil.rmtree(path, onerror=_onerror)  # pylint: disable=deprecated-argument


def find_file(name, path):
    for root, _, files in os.walk(path):
        if name in files:
            return os.path.join(root, name)
    return None


def safe_create_dir(path, raise_exception=False):
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except Exception as e:  # pylint: disable=broad-except
        if raise_exception:
            raise e
    return None


def download_file(url, dst, cache=True):
    if cache:
        try:
            head = requests.head(url, allow_redirects=True, timeout=10)
            head.raise_for_status()
            content_length = head.headers.get("Content-Length")
            if (
                os.path.isfile(dst)
                and content_length is not None
                and int(content_length) == os.path.getsize(dst)
            ):
                log.debug("Getting from cache: %s", dst)
                return dst
        except Exception as e:  # pylint: disable=broad-except
            log.debug("HEAD request failed for %s: %r; falling back to GET", url, e)

    resp = requests.get(url, stream=True, timeout=30)
    resp.raise_for_status()
    itercontent = resp.iter_content(chunk_size=io.DEFAULT_BUFFER_SIZE)
    safe_create_dir(os.path.dirname(dst))
    with open(dst, "wb") as fp:
        for chunk in itercontent:
            if chunk:  # skip keep-alive chunks
                fp.write(chunk)
    return dst


def unpack_archive(src, dst):
    assert src.endswith("tar.gz")
    with tarfile.open(src, mode="r:gz") as fp:
        if sys.version_info >= (3, 12):
            fp.extractall(dst, filter="data")
        else:
            def _safe_members(tf):
                dst_real = os.path.realpath(dst)
                for m in tf.getmembers():
                    target = os.path.realpath(os.path.join(dst, m.name))
                    if not (target == dst_real or target.startswith(dst_real + os.sep)):
                        raise tarfile.ExtractError(f"Blocked unsafe tar member: {m.name}")
                    yield m
            fp.extractall(dst, members=_safe_members(fp))
    return dst


def get_installer_script():
    return os.path.abspath(sys.argv[0])


def get_systype():
    type_ = platform.system().lower()
    arch = platform.machine().lower()
    if type_ == "windows":
        arch = "amd64" if platform.architecture()[0] == "64bit" else "x86"
    return "%s_%s" % (type_, arch) if arch else type_


def safe_remove_dir(path, raise_exception=False):
    try:
        return rmtree(path)
    except Exception as e:  # pylint: disable=broad-except
        if raise_exception:
            raise e
    return None


def pepver_to_semver(pepver):
    return re.sub(r"(\.\d+)\.?(dev|a|b|rc|post)", r"\1-\2.", pepver, 1)


def where_is_program(program, envpath=None):
    env = os.environ.copy()
    if envpath:
        env["PATH"] = envpath

    # try OS's built-in commands
    try:
        result = (
            subprocess.check_output(
                ["where" if IS_WINDOWS else "which", program], env=env
            )
            .decode()
            .strip()
        )
        first = result.splitlines()[0] if result else ""
        if first and os.path.isfile(first):
            return first
    except (subprocess.CalledProcessError, OSError):
        pass

    # look up in $PATH
    for bin_dir in env.get("PATH", "").split(os.pathsep):
        if os.path.isfile(os.path.join(bin_dir, program)):
            return os.path.join(bin_dir, program)
        if os.path.isfile(os.path.join(bin_dir, "%s.exe" % program)):
            return os.path.join(bin_dir, "%s.exe" % program)

    return program
