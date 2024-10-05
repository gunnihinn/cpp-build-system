.PHONY: lint
lint:
	black --diff .
	mypy .
	ruff check .
