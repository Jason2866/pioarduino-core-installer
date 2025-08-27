pioarduino Core Installer
=========================

A standalone installer for `pioarduino Core` using modern Python packaging tools.

**Important:** This installer now requires Python 3.10 or newer and uses `uv` for fast, reliable dependency management.

Features
--------

* Fast installation using `uv` package manager (required)
* Isolated virtual environment creation
* Cross-platform compatibility (Windows, macOS, Linux)
* Python 3.10+ support

Requirements
------------

* Python 3.10 or newer
* `uv` package manager (automatically downloaded if not available)
* Internet connection for downloading packages

Development
-----------

This project uses modern Python packaging with `pyproject.toml` and `uv`.

**Note:** If `uv` is not installed on your system, the installer will automatically download and install it for you.

Quick start for development::

    # Install uv if not already installed
    curl -LsSf https://astral.sh/uv/install.sh | sh
    
    # Clone and setup development environment
    git clone https://github.com/Jason2866/pioarduino-core-installer.git
    cd pioarduino-core-installer
    
    # Install dependencies with uv
    uv sync --dev
    
    # Run tests
    uv run pytest
    
    # Format code
    uv run black pioinstaller tests
    uv run isort pioinstaller tests
    
    # Run linter
    uv run pylint pioinstaller
