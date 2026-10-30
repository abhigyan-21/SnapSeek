"""Native desktop entry point. The semantic model loads only after a scan."""

from snapseek.desktop import main


if __name__ == "__main__":
    raise SystemExit(main())
