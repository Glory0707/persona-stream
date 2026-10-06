.PHONY: demo test scan install

install:
	pip install -e ".[dev]"

test:
	python -m pytest tests/ -q

demo:
	python synthetic/demo.py

scan:
	python eval/leak_scan.py
