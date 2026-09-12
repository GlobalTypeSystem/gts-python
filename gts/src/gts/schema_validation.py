from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import regex
from jsonschema import Draft7Validator, FormatChecker, ValidationError, validators
from jsonschema.validators import validator_for as jsonschema_validator_for

PATTERN_TIMEOUT_SECONDS = 1.0

# Shared format checker for instance/trait validation.
#
# A bare ``FormatChecker()`` draws from jsonschema's shared, class-level checker
# registry, in which draft3-only checkers overwrite their draft6/7 successors
# (e.g. "time" ends up as bare ``HH:MM:SS`` and rejects a valid RFC 3339 value
# like "10:30:00Z"). Conversely, ``Draft7Validator.FORMAT_CHECKER`` fixes those
# but lacks "uuid" (added to JSON Schema only in draft 2019-09).
#
# Starting from the bare checker (which provides "uuid") and overlaying the
# draft-07 checkers (which restore correct RFC 3339 "time"/"date-time") yields
# the complete, correct standard-format set. This uses only built-in jsonschema
# checkers -- no custom format functions.
FORMAT_CHECKER = FormatChecker()
FORMAT_CHECKER.checkers.update(Draft7Validator.FORMAT_CHECKER.checkers)


def _validate_pattern(
    validator: Any, pattern: str, instance: Any, schema: Any
) -> Iterator[ValidationError]:
    if not isinstance(instance, str):
        return
    try:
        if regex.search(pattern, instance, timeout=PATTERN_TIMEOUT_SECONDS) is None:
            yield ValidationError(f"{instance!r} does not match {pattern!r}")
    except TimeoutError:
        yield ValidationError("regular expression match timed out")
    except regex.error as error:
        yield ValidationError(f"invalid regular expression: {error}")


def validator_for(schema: Any) -> Any:
    return validators.extend(
        jsonschema_validator_for(schema), {"pattern": _validate_pattern}
    )
