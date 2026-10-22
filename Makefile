.PHONY: run run-debug dev test lint clean

# Run
run:
	python -m src

run-debug:
	TELECLAW_DEBUG=1 python -m src

# Development
dev:
	pip install -e ".[dev]"

test:
	python -m pytest tests/ -x -q --tb=short

lint:
	python -c "import py_compile, glob; [py_compile.compile(f, doraise=True) for f in glob.glob('src/*.py') + glob.glob('*.py')]"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf *.egg-info dist build
