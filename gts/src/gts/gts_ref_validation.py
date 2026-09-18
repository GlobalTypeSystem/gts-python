from enum import Enum


class GtsRefValidationMode(str, Enum):
    NONE = "none"
    PRESENCE = "presence"
    FULL = "full"
