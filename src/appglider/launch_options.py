"""Per-app launch arguments and environment variables, kept apart from the catalog."""

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

MAX_ARGUMENTS = 32
MAX_VARIABLES = 32
MAX_VALUE = 1024
ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")


@dataclass(frozen=True)
class LaunchOptions:
    arguments: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()

    def __bool__(self) -> bool:
        return bool(self.arguments or self.environment)


def _plain(value: str) -> bool:
    return len(value) <= MAX_VALUE and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def parse_arguments(text: str) -> tuple[str, ...]:
    """Split one line of arguments the way a shell would; raise ValueError with a reason."""
    try:
        arguments = tuple(shlex.split(text))
    except ValueError as error:
        raise ValueError(f"The arguments could not be read: {error}.") from error
    if len(arguments) > MAX_ARGUMENTS:
        raise ValueError(f"Use at most {MAX_ARGUMENTS} arguments.")
    if not all(_plain(argument) for argument in arguments):
        raise ValueError("Arguments must be plain text of up to 1024 characters.")
    return arguments


def parse_environment(text: str) -> tuple[tuple[str, str], ...]:
    """Read space-separated NAME=value pairs; a later pair replaces an earlier one."""
    try:
        words = shlex.split(text)
    except ValueError as error:
        raise ValueError(f"The environment could not be read: {error}.") from error
    variables: dict[str, str] = {}
    for word in words:
        name, separator, value = word.partition("=")
        if not separator or not ENVIRONMENT_NAME.fullmatch(name):
            raise ValueError(
                f'"{name}" is not a valid variable. Write NAME=value, with letters, '
                "digits and underscores in the name."
            )
        if not _plain(value):
            raise ValueError(f"The value of {name} must be plain text of up to 1024 characters.")
        variables[name] = value
    if len(variables) > MAX_VARIABLES:
        raise ValueError(f"Use at most {MAX_VARIABLES} variables.")
    return tuple(variables.items())


def format_arguments(options: LaunchOptions) -> str:
    return shlex.join(options.arguments)


def format_environment(options: LaunchOptions) -> str:
    return " ".join(shlex.quote(f"{name}={value}") for name, value in options.environment)


def _options(data: object) -> LaunchOptions:
    if not isinstance(data, dict):
        raise ValueError("Invalid launch options.")
    arguments, environment = data.get("args", []), data.get("env", {})
    if (
        not isinstance(arguments, list)
        or not isinstance(environment, dict)
        or len(arguments) > MAX_ARGUMENTS
        or len(environment) > MAX_VARIABLES
        or not all(isinstance(item, str) and _plain(item) for item in arguments)
        or not all(
            isinstance(name, str)
            and ENVIRONMENT_NAME.fullmatch(name)
            and isinstance(value, str)
            and _plain(value)
            for name, value in environment.items()
        )
    ):
        raise ValueError("Invalid launch options.")
    return LaunchOptions(tuple(arguments), tuple(environment.items()))


def load_launch_options(path: Path) -> dict[str, LaunchOptions]:
    if not path.exists():
        return {}
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("The launch options file is too large.")
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or any(
            not isinstance(key, str) or len(key) > 256 for key in data
        ):
            raise ValueError("Invalid launch options.")
        return {key: _options(value) for key, value in data.items()}
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read {path}. The file was preserved: {error}") from error


def options_json(options: dict[str, LaunchOptions]) -> dict[str, object]:
    return {
        key: {"args": list(value.arguments), "env": dict(value.environment)}
        for key, value in options.items()
    }
