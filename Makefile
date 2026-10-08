# Requires GNU make and Python 3.9+; no pip packages.
# Default: mathematics, current year and previous two calendar years.
# Output: DATA_DIR/YYYY-MM.json.gz. Old months are reused; the current and
# previous month are refreshed. REFRESH=1 refreshes every requested month.
# Historical revisions/categories may change: use REFRESH=1 to update them.
# REFRESH=1 also adds author_count to snapshots created by older versions.
# Metadata: CC0 1.0, https://info.arxiv.org/help/api/tou.html

.DEFAULT_GOAL := help
.PHONY: help fetch-metadata unpack index serve

PYTHON ?= python3
YEARS ?= 5
END_YEAR ?= $(shell $(PYTHON) -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).year)')
START_YEAR ?= $(shell $(PYTHON) -c 'print(int("$(END_YEAR)") - int("$(YEARS)") + 1)')
DATA_DIR ?= data/arxiv
QUERY ?= cat:math.*
REFRESH ?= 0
PORT ?= 8000

help:
	@echo 'make fetch-metadata                     # recent mathematics metadata'
	@echo 'make fetch-metadata YEARS=5              # five calendar years'
	@echo 'make fetch-metadata START_YEAR=2022 END_YEAR=2025'
	@echo 'make fetch-metadata QUERY=cat:math.NA     # numerical analysis'
	@echo 'make fetch-metadata REFRESH=1            # refresh historical months too'
	@echo 'make fetch-metadata QUERY= DATA_DIR=data/all-arxiv # all categories'
	@echo 'make unpack                             # create ordinary JSON, offline'
	@echo 'make index                              # index existing monthly files'
	@echo 'make serve                              # open http://localhost:8000'

fetch-metadata:
	$(PYTHON) fetch_arxiv.py --start-year "$(START_YEAR)" --end-year "$(END_YEAR)" --data-dir "$(DATA_DIR)" --query "$(QUERY)" $(if $(filter 1,$(REFRESH)),--refresh,)

unpack:
	$(PYTHON) fetch_arxiv.py --unpack --data-dir "$(DATA_DIR)"

index:
	$(PYTHON) fetch_arxiv.py --index --data-dir "$(DATA_DIR)"

# The page defaults to data/arxiv; its directory control accepts another
# DATA_DIR served beneath this project. No website build step is needed.
serve: index
	$(PYTHON) -m http.server "$(PORT)" --bind 127.0.0.1
