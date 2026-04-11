pioarduino Core Installer
=========================

A standalone installer for `pioarduino Core` using modern Python packaging tools.

**Important:** The installer requires Python 3.13 and uses `uv` to install pioarduino core. If Python 3.13 is not found, it will be installed automatically via `uv`.

Features
--------

* Fast installation using `uv` package manager
* Isolated virtual environment creation
* Cross-platform compatibility (Windows, macOS, Linux)
* Automatic Python 3.13 installation via `uv` if not present

Requirements
------------

* Python 3.13
* Internet connection for downloading packages

Development
-----------

This project uses modern Python packaging with `pyproject.toml` and `uv`.

Quick start for development (Linux and macOS)::

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
