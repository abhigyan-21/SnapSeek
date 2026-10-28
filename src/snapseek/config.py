"""Configuration for locating the screenshot directory."""

import os
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when the screenshot directory configuration is invalid."""


def get_screenshot_directory() -> Path:
    """Read and validate ``SNAPSEEK_SCREENSHOT_DIR``."""
    configured_path = os.environ.get("SNAPSEEK_SCREENSHOT_DIR")
    if not configured_path:
        raise ConfigurationError(
            "SNAPSEEK_SCREENSHOT_DIR is not set. Set it to an existing directory."
        )

    directory = Path(configured_path).expanduser()
    if not directory.is_dir():
        raise ConfigurationError(
            f"Screenshot directory does not exist or is not a directory: {directory}"
        )
    return directory
