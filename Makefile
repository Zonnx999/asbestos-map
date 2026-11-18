# Pipeline: fetch -> normalize -> geocode (only if VWORLD_KEY/KAKAO_KEY set) -> build_web
PY ?= python3

.PHONY: all fetch normalize geocode web clean-web
all: fetch normalize geocode web

fetch:
	$(PY) scripts/fetch_all.py

normalize:
	$(PY) scripts/normalize.py

# exits 0 with a notice when no key is set
geocode:
	$(PY) scripts/geocode.py

web:
	$(PY) scripts/build_web.py

# offline rebuild (no network): skip fetch
offline: normalize geocode web
