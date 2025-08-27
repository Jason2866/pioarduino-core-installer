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
import logging
import os
import platform
import subprocess
import time

import click

from pioinstaller import __version__, core, exception, python, util

log = logging.getLogger(__name__)


VIRTUALENV_URL = "https://bootstrap.pypa.io/virtualenv/virtualenv.pyz"
PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
UV_URL = "https://github.com/astral-sh/uv/releases/latest/download/uv-{platform}.tar.gz"


def get_uv_platform():
    """Get the uv platform identifier for the current system."""
    platform_map = {
        ("Windows", "AMD64"): "x86_64-pc-windows-msvc",
        ("Windows", "x86"): "i686-pc-windows-msvc", 
        ("Darwin", "x86_64"): "x86_64-apple-darwin",
        ("Darwin", "arm64"): "aarch64-apple-darwin",
        ("Linux", "x86_64"): "x86_64-unknown-linux-gnu",
        ("Linux", "aarch64"): "aarch64-unknown-linux-gnu",
        ("Linux", "armv7l"): "armv7-unknown-linux-gnueabihf",
    }
    
    system = platform.system()
    machine = platform.machine()
    
    # Handle different arm64 representations on macOS
    if system == "Darwin" and machine in ("arm64", "aarch64"):
        machine = "arm64"
    
    key = (system, machine)
    if key in platform_map:
        return platform_map[key]
    
    # Default fallback
    if system == "Windows":
        return "x86_64-pc-windows-msvc"
    elif system == "Darwin":
        return "x86_64-apple-darwin"
    else:
        return "x86_64-unknown-linux-gnu"


def download_and_install_uv(cache_dir):
    """Download and install uv package manager."""
    uv_platform = get_uv_platform()
    uv_url = f"https://github.com/astral-sh/uv/releases/latest/download/uv-{uv_platform}.tar.gz"
    
    log.debug("Downloading uv from %s", uv_url)
    uv_archive_path = os.path.join(cache_dir, "tmp", f"uv-{uv_platform}.tar.gz")
    
    try:
        util.download_file(uv_url, uv_archive_path)
        
        # Extract uv binary
        import tarfile
        with tarfile.open(uv_archive_path, 'r:gz') as tar:
            # Extract all files to a temporary directory
            extract_dir = os.path.join(cache_dir, "tmp", "uv-extract")
            util.safe_remove_dir(extract_dir)
            os.makedirs(extract_dir, exist_ok=True)
            tar.extractall(extract_dir)
            
            # Find the uv binary in the extracted files
            uv_binary = None
            for root, dirs, files in os.walk(extract_dir):
                for file in files:
                    if file == "uv" or file == "uv.exe":
                        uv_binary = os.path.join(root, file)
                        break
                if uv_binary:
                    break
            
            if not uv_binary:
                raise exception.PIOInstallerException("Could not find uv binary in downloaded archive")
            
            # Copy uv to cache directory
            uv_dest = os.path.join(cache_dir, "uv" + (".exe" if util.IS_WINDOWS else ""))
            import shutil
            shutil.copy2(uv_binary, uv_dest)
            
            # Make executable on Unix systems
            if not util.IS_WINDOWS:
                os.chmod(uv_dest, 0o755)
            
            log.debug("uv installed at %s", uv_dest)
            return uv_dest
            
    except Exception as e:
        log.debug("Could not download or install uv: %s", str(e))
        return None


def get_uv_executable():
    """Get path to uv executable, download if needed."""
    # First try to find uv in PATH
    uv_exe = util.where_is_program("uv")
    if uv_exe:
        log.debug("Found uv in PATH: %s", uv_exe)
        return uv_exe
    
    # Try to find cached uv
    from pioinstaller import core
    cache_dir = core.get_cache_dir()
    cached_uv = os.path.join(cache_dir, "uv" + (".exe" if util.IS_WINDOWS else ""))
    
    if os.path.isfile(cached_uv):
        log.debug("Found cached uv: %s", cached_uv)
        return cached_uv
    
    # Download and install uv
    click.echo("Downloading and installing uv package manager...")
    uv_exe = download_and_install_uv(cache_dir)
    if uv_exe:
        click.echo("uv has been successfully installed!")
        return uv_exe
    
    return None


def get_penv_dir(path=None):
    if os.getenv("PLATFORMIO_PENV_DIR"):
        return os.getenv("PLATFORMIO_PENV_DIR")

    core_dir = path or core.get_core_dir()
    return os.path.join(core_dir, "penv")


def get_penv_bin_dir(path=None):
    penv_dir = path or get_penv_dir()
    return os.path.join(penv_dir, "Scripts" if util.IS_WINDOWS else "bin")


def create_core_penv(penv_dir=None, ignore_pythons=None):
    penv_dir = penv_dir or get_penv_dir()

    click.echo("Creating a virtual environment at %s" % penv_dir)

    # Get uv executable
    uv_exe = get_uv_executable()
    if not uv_exe:
        click.echo("Could not install uv, falling back to traditional venv + pip method")
        return create_core_penv_fallback(penv_dir, ignore_pythons)

    result_dir = None
    for python_exe in python.find_compatible_pythons(ignore_pythons):
        result_dir = create_venv_with_uv(uv_exe, python_exe, penv_dir)
        if result_dir:
            break

    if not result_dir and not python.is_portable():
        python_exe = python.fetch_portable_python(os.path.dirname(penv_dir))
        if python_exe:
            result_dir = create_venv_with_uv(uv_exe, python_exe, penv_dir)

    if not result_dir:
        click.echo("Could not create virtual environment with uv, falling back to traditional method")
        return create_core_penv_fallback(penv_dir, ignore_pythons)

    python_exe = os.path.join(
        get_penv_bin_dir(penv_dir), "python.exe" if util.IS_WINDOWS else "python"
    )
    init_state(python_exe, penv_dir)
    click.echo("Virtual environment has been successfully created!")
    return result_dir


def create_venv_with_uv(uv_exe, python_exe, penv_dir):
    """Create virtual environment using uv."""
    log.debug("Using uv with %s Python for virtual environment.", python_exe)
    
    # Remove existing directory if it exists
    util.safe_remove_dir(penv_dir)
    
    try:
        # Create venv with uv
        cmd = [uv_exe, "venv", "--python", python_exe, penv_dir]
        log.debug("Running: %s", " ".join(cmd))
        subprocess.check_call(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Verify the venv was created
        expected_python = os.path.join(
            get_penv_bin_dir(penv_dir), "python.exe" if util.IS_WINDOWS else "python"
        )
        if os.path.isfile(expected_python):
            log.debug("Successfully created venv at %s", penv_dir)
            return penv_dir
        else:
            log.debug("Expected python not found at %s", expected_python)
            return None
            
    except subprocess.CalledProcessError as e:
        log.debug("Failed to create venv with uv: %s", str(e))
        return None
    except Exception as e:
        log.debug("Error creating venv with uv: %s", str(e))
        return None


def create_core_penv_fallback(penv_dir=None, ignore_pythons=None):
    """Fallback method using traditional venv + pip."""
    penv_dir = penv_dir or get_penv_dir()

    result_dir = None
    for python_exe in python.find_compatible_pythons(ignore_pythons):
        result_dir = create_virtualenv(python_exe, penv_dir)
        if result_dir:
            break

    if not result_dir and not python.is_portable():
        python_exe = python.fetch_portable_python(os.path.dirname(penv_dir))
        if python_exe:
            result_dir = create_virtualenv(python_exe, penv_dir)

    if not result_dir:
        raise exception.PIOInstallerException(
            "Could not create PIO Core Virtual Environment. Please report to "
            "https://github.com/pioarduino/pioarduino-core-installer/issues"
        )

    python_exe = os.path.join(
        get_penv_bin_dir(penv_dir), "python.exe" if util.IS_WINDOWS else "python"
    )
    init_state(python_exe, penv_dir)
    update_pip(python_exe, penv_dir)
    click.echo("Virtual environment has been successfully created!")
    return result_dir


def create_virtualenv(python_exe, penv_dir):
    log.debug("Using %s Python for virtual environment.", python_exe)
    try:
        return create_with_local_venv(python_exe, penv_dir)
    except Exception as e:  # pylint:disable=broad-except
        log.debug(
            "Could not create virtualenv with local packages"
            " Trying download virtualenv script and using it. Error: %s",
            str(e),
        )
        try:
            return create_with_remote_venv(python_exe, penv_dir)
        except Exception as exc:  # pylint:disable=broad-except
            log.debug(
                "Could not create virtualenv with downloaded script. Error: %s",
                str(exc),
            )
    return None


def create_with_local_venv(python_exe, penv_dir):
    venv_cmd_options = [
        [python_exe, "-m", "venv", penv_dir],
        [python_exe, "-m", "virtualenv", "-p", python_exe, penv_dir],
        ["virtualenv", "-p", python_exe, penv_dir],
        [python_exe, "-m", "virtualenv", penv_dir],
        ["virtualenv", penv_dir],
    ]
    last_error = None
    for command in venv_cmd_options:
        util.safe_remove_dir(penv_dir)
        log.debug("Creating virtual environment: %s", " ".join(command))
        try:
            subprocess.run(command, check=True)
            return penv_dir
        except Exception as e:  # pylint:disable=broad-except
            last_error = e
    raise last_error  # pylint:disable=raising-bad-type


def create_with_remote_venv(python_exe, penv_dir):
    util.safe_remove_dir(penv_dir)

    log.debug("Downloading virtualenv package archive")
    venv_script_path = util.download_file(
        VIRTUALENV_URL,
        os.path.join(
            os.path.dirname(penv_dir), ".cache", "tmp", os.path.basename(VIRTUALENV_URL)
        ),
    )
    if not venv_script_path:
        raise exception.PIOInstallerException("Could not find virtualenv script")
    command = [python_exe, venv_script_path, penv_dir]
    log.debug("Creating virtual environment: %s", " ".join(command))
    subprocess.run(command, check=True)
    return penv_dir


def init_state(python_exe, penv_dir):
    version_code = (
        "import sys; version=sys.version_info; "
        "print('%d.%d.%d'%(version[0],version[1],version[2]))"
    )
    python_version = (
        subprocess.check_output(
            [python_exe, "-c", version_code], stderr=subprocess.PIPE
        )
        .decode()
        .strip()
    )
    state = {
        "created_on": int(round(time.time())),
        "python": {
            "path": python_exe,
            "version": python_version,
        },
        "installer_version": __version__,
        "platform": {
            "platform": platform.platform(),
            "release": platform.release(),
        },
    }
    return save_state(state, penv_dir)


def load_state(penv_dir=None):
    penv_dir = penv_dir or get_penv_dir()
    state_path = os.path.join(penv_dir, "state.json")
    if not os.path.isfile(state_path):
        raise exception.PIOInstallerException(
            "Could not found state.json file in `%s`" % state_path
        )
    with open(state_path) as fp:
        return json.load(fp)


def save_state(state, penv_dir=None):
    penv_dir = penv_dir or get_penv_dir()
    state_path = os.path.join(penv_dir, "state.json")
    with open(state_path, "w") as fp:
        json.dump(state, fp)
    return state_path


def update_pip(python_exe, penv_dir):
    click.echo("Updating Python package manager (PIP) in the virtual environment")
    try:
        log.debug("Creating pip.conf file in %s", penv_dir)
        with open(os.path.join(penv_dir, "pip.conf"), "w") as fp:
            fp.write("\n".join(["[global]", "user=no"]))

        try:
            log.debug("Updating PIP ...")
            subprocess.run(
                [python_exe, "-m", "pip", "install", "-U", "pip"], check=True
            )
        except subprocess.CalledProcessError as e:
            log.debug(
                "Could not update PIP. Error: %s",
                str(e),
            )
            log.debug("Downloading 'get-pip.py' installer...")
            get_pip_path = os.path.join(
                os.path.dirname(penv_dir), ".cache", "tmp", os.path.basename(PIP_URL)
            )
            util.download_file(PIP_URL, get_pip_path)
            log.debug("Installing PIP ...")
            subprocess.run([python_exe, get_pip_path], check=True)

        click.echo("PIP has been successfully updated!")
        return True
    except Exception as e:  # pylint:disable=broad-except
        log.debug(
            "Could not install PIP. Error: %s",
            str(e),
        )
        return False
