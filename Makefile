VENV := .venv
PYTHON := $(VENV)/bin/python
PYTEST := $(VENV)/bin/pytest

.PHONY: test install clean

$(VENV):
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -e ".[dev]" -q

install: $(VENV)

test: $(VENV)
	$(PYTEST) tests/ -v

clean:
	rm -rf $(VENV) src/*.egg-info
