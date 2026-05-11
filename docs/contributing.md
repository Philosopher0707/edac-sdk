# Contributing

## Development Setup

```bash
git clone https://github.com/edac-team/edac.git
cd edac
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
python -m pytest tests/ -v
```

With coverage:

```bash
python -m pytest tests/ --cov=edac --cov-report=html
```

## Linting

```bash
ruff check edac/ tests/
ruff format edac/ tests/
mypy edac/
```

## Pre-commit

```bash
pre-commit install
pre-commit run --all-files
```

## Documentation

Build and serve locally:

```bash
cd docs
mkdocs serve
```

## Code Style

- Python 3.11+ type hints everywhere
- Google-style docstrings
- 100-character line limit
- `ruff` for linting and formatting
- `mypy` for type checking

## Adding a New Layer

1. Create `edac/<layer>/__init__.py`
2. Add core classes with type hints
3. Add tests in `tests/test_<layer>.py`
4. Add a router in `edac/server/routers/` if HTTP API is needed
5. Register in `edac/server/api.py` lifespan
6. Update `docs/architecture.md` and `docs/api.md`

## Pull Request Checklist

- [ ] Tests pass (`pytest tests/`)
- [ ] Lint passes (`ruff check`)
- [ ] Type checks pass (`mypy`)
- [ ] Documentation updated
- [ ] README updated if public-facing change
