"""A stand-in agent for Organize Tabs: groups the numbered tabs by the first
word of their titles and calls a repeated address a duplicate, answering in
the JSON it was asked for (after some chatter, as models do)."""

import json
import re
import sys

groups, seen, duplicates = {}, set(), []
for number, title, url in re.findall(
    r"^(\d+)\. (.*?) — (\S+)$", sys.stdin.read(), re.MULTILINE
):
    if url in seen:
        duplicates.append(int(number))
        continue
    seen.add(url)
    groups.setdefault(title.split()[0], []).append(int(number))
plan = {
    "groups": [{"name": name, "tabs": tabs} for name, tabs in groups.items()],
    "duplicates": duplicates,
}
print("Here is a plan:\n" + json.dumps(plan))
