VENV = .venv
PYTHON = $(VENV)/bin/python

# ── Main commands ──────────────────────────────────────────────────────────────

query:
	$(PYTHON) query.py

sync:
	$(PYTHON) ingest.py

reset:
	$(PYTHON) ingest.py --reset

setup:
	$(PYTHON) setup_cron.py

# ── Query shortcuts ────────────────────────────────────────────────────────────

ask:
	$(PYTHON) query.py --sync

cron-status:
	$(PYTHON) setup_cron.py --status

cron-remove:
	$(PYTHON) setup_cron.py --remove

# ── Environment ────────────────────────────────────────────────────────────────

install:
	/opt/homebrew/bin/python3.11 -m venv $(VENV)
	$(VENV)/bin/pip install -r requirements.txt

.PHONY: query sync reset setup ask cron-status cron-remove install
