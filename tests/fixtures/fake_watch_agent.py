"""A stand-in agent for watches: reads the prompt on stdin and answers like
a model would, NOTIFY when the price in "Now" is under $50, else QUIET."""

import re
import sys

prompt = sys.stdin.read()
now = prompt.split("Now:", 1)[1] if "Now:" in prompt else ""
prices = [float(p) for p in re.findall(r"\$(\d+(?:\.\d+)?)", now)]
if "under $50" in prompt and prices and min(prices) < 50:
    print(f"Thinking it over…\nNOTIFY: The price dropped to ${min(prices):g}.")
else:
    print("QUIET")
