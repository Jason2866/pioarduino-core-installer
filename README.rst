pioarduino Core Installer
=========================

A standalone installer for `pioarduino Core` using modern Python packaging tools.

**Important:** The installer checks for Python 3.10–3.13 and uses `uv` to install pioarduino core. If no compatible Python is found, it will install Python 3.13.

Features
--------

* Fast installation using `uv` package manager
* Isolated virtual environment creation
* Cross-platform compatibility (Windows, macOS, Linux)
* Python 3.10+ support

Requirements
------------

* Python 3.9 or newer for install
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
