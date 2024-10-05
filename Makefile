prefix ?= $(HOME)/bin

.PHONY: lint
lint:
	black --diff .
	mypy .
	ruff check .

$(prefix)/cbs: main.py
	install $< $@

install: $(prefix)/cbs
