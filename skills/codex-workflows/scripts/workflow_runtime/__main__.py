"""Allow ``python -m workflow_runtime`` in an installed skill checkout."""

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
