from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import regex
from jsonschema import ValidationError, validators
from jsonschema.validators import validator_for as jsonschema_validator_for

PATTERN_TIMEOUT_SECONDS = 1.0


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
