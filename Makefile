# Instal# Use uv for all operations
lint:
	pylint --rcfile=./.pylintrc ./pioinstaller

isort:
	uv run isort ./tests
	uv run isort ./pioinstaller

format:
	uv run black ./pioinstaller
	uv run black ./tests

test:
	uv run pytest --verbose --capture=no --exitfirst tests

install-dev:
	uv sync --dev

pack:
	pioinstaller pack

before-commit: isort format lint testndencies with uv
install-dev:
	uv pip install -e ".[dev]"

# Alternative install with pip (for compatibility)
install-dev-pip:
	pip install -e ".[dev]"

lint:
	pylint --rcfile=./.pylintrc ./pioinstaller

isort:
	isort ./tests
	isort ./pioinstaller

format:
	black ./pioinstaller
	black ./tests

test:
	pytest --verbose --capture=no --exitfirst tests

pack:
	pioinstaller pack

before-commit: isort format lint

clean:
	find . -name \*.pyc -delete
	find . -name __pycache__ -delete
	rm -rf .cache
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/

# Build with uv
build:
	uv build

# Build with traditional tools (for compatibility) 
build-fallback:
	python -m build

# Publish with uv
publish:
	uv publish

# Publish with traditional tools (for compatibility)
publish-fallback:
	python -m twine upload dist/*