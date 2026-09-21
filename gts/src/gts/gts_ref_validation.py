from enum import Enum


class GtsRefValidationMode(str, Enum):
    NONE = "none"
    ANY_PRESENT = "any-present"
    ANY_VALID = "any-valid"
