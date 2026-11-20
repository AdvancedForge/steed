"""Spot page text that talks to AI agents instead of people.

Web pages can hide instructions meant for an agent ("ignore your previous
instructions…"). Steed cannot tell intent, but it can point out text that
looks like that, so an agent is reminded that it is page content, not the
user, and the task journal records that the page had it.
"""

import re

PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,30}\b(previous|prior|above|"
        r"earlier|all|your|the)\b[^.\n]{0,20}\b(instructions?|prompts?|rules|"
        r"directions|guidelines)\b",
        r"\byou are now\b[^.\n]{0,40}\b(assistant|agent|ai|model|mode|dan)\b",
        r"\b(new|updated|real|actual)\s+(system\s+)?instructions?\s*:",
        r"\bsystem\s+prompt\b",
        r"\b(attention|note|message)\s*(to|for)?\s*:?\s*(any\s+|all\s+)?"
        r"(ai\s+(agents?|assistants?|models?)|llms?|language models?|agents?)\b",
        r"\bif you are an? (ai|llm|language model|assistant|agent|bot)\b",
        r"<\|im_start\|>|\[/?INST\]|</?system>",
    )
)
LIMIT = 3


def agent_instructions(texts):
    """Up to three short excerpts of text that addresses an AI agent."""
    found = []
    for text in texts:
        if not isinstance(text, str):
            continue
        for pattern in PATTERNS:
            for match in pattern.finditer(text):
                start = max(0, match.start() - 20)
                excerpt = " ".join(text[start : match.end() + 40].split())[:120]
                if excerpt not in found:
                    found.append(excerpt)
                if len(found) >= LIMIT:
                    return found
    return found
