# Use uv for all operations

# Always use phony targets
.PHONY: all lint isort format test install-dev pack before-commit clean publish

# Default target
all: before-commit

lint:
	uv run pylint --rcfile=./.pylintrc ./pioinstaller

isort:
	uv run isort ./pioinstaller ./tests

format:
	uv run black ./pioinstaller ./tests

test:
	uv run pytest --verbose --capture=no --exitfirst tests

install-dev:
	uv sync --dev

pack:
	mkdir -p dist
	uv run pioinstaller pack dist/

before-commit: isort format lint test

clean:
	find . -name \*.pyc -delete
	find . -name __pycache__ -delete
	rm -rf .cache

publish:
	uv build
	uv publish
