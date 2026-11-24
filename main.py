"""Compatibility launcher; the installed command is `pfc`."""

from pfc.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
