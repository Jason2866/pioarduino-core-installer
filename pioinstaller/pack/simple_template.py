#!/usr/bin/env python3
# Simple PlatformIO Core installer that uses uv

import subprocess
import sys


def main():
    """Install and run pioinstaller using uv"""

    # Check if uv is available
    try:
        subprocess.check_call(
            ["uv", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("uv is required but not found. Please install uv first:")
        print("  curl -LsSf https://astral.sh/uv/install.sh | sh")
        sys.exit(1)

    # Install pioinstaller using uv
    try:
        subprocess.check_call(
            [
                "uv",
                "pip",
                "install",
                "--quiet",
                "git+https://github.com/Jason2866/pioarduino-core-installer@uv_refactor",
            ]
        )

        # Run pioinstaller
        subprocess.check_call(["uv", "run", "pioinstaller"] + sys.argv[1:])

    except subprocess.CalledProcessError as e:
        print(f"Error installing or running pioinstaller: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
