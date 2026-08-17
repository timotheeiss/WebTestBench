"""
Convenient prompt registry so callers can fetch any prompt by key:
from prompt import PROMPT_REGISTRY; prompt = PROMPT_REGISTRY["checklist_draft"]
"""

from .checklist_generation import PROMPT_CHECKLIST_GENERATION
from .defect_detection import PROMPT_DEFECT_DETECTION
from .defect_detection_based_gold import (
    PROMPT_DEFECT_DETECTION_BASED_GOLD,
    PROMPT_DEFECT_DETECTION_BASED_GOLD_WITH_SCREENSHOTS,
)
from .defect_detection_based_gold_with_hints import (
    PROMPT_DEFECT_DETECTION_BASED_GOLD_WITH_HINTS,
    PROMPT_DEFECT_DETECTION_BASED_GOLD_WITH_HINTS_AND_SCREENSHOTS,
)
from .match_item import PROMPT_MATCH_ITEM



USER_PROMPT = {
    "checklist_generation": PROMPT_CHECKLIST_GENERATION,
    "defect_detection": PROMPT_DEFECT_DETECTION,
    "defect_detection_based_gold": PROMPT_DEFECT_DETECTION_BASED_GOLD,
    "defect_detection_based_gold_with_screenshots": (
        PROMPT_DEFECT_DETECTION_BASED_GOLD_WITH_SCREENSHOTS
    ),
    "defect_detection_based_gold_with_hints": PROMPT_DEFECT_DETECTION_BASED_GOLD_WITH_HINTS,
    "defect_detection_based_gold_with_hints_and_screenshots": (
        PROMPT_DEFECT_DETECTION_BASED_GOLD_WITH_HINTS_AND_SCREENSHOTS
    ),
    "match_item": PROMPT_MATCH_ITEM,
}

__all__ = [
    "PROMPT_CHECKLIST_GENERATION",
    "PROMPT_DEFECT_DETECTION",
    "PROMPT_DEFECT_DETECTION_BASED_GOLD",
    "PROMPT_DEFECT_DETECTION_BASED_GOLD_WITH_SCREENSHOTS",
    "PROMPT_DEFECT_DETECTION_BASED_GOLD_WITH_HINTS",
    "PROMPT_DEFECT_DETECTION_BASED_GOLD_WITH_HINTS_AND_SCREENSHOTS",
    "PROMPT_MATCH_ITEM",
]
