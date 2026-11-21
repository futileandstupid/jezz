"""Module execution entrypoint enabling ``python -m jezz``."""

import sys

from jezz.cli import main

if __name__ == "__main__":
    raise SystemExit(main(tuple(sys.argv[1:])))
