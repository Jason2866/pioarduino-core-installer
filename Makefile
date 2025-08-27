# Use uv for all operations

lint:
	uv run pylint --rcfile=./.pylintrc ./pioinstaller

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
	uv run pioinstaller pack

before-commit: isort format lint test

clean:
	find . -name \*.pyc -delete
	find . -name __pycache__ -delete
	rm -rf .cache

publish:
	uv build
	uv publish
