# Steed

A Python desktop browser built with PyQt6 and Qt WebEngine. Originally developed
for a Framework 12 laptop, Steed currently targets Linux desktop use with separate
browsing profiles and direct KeePassXC password management.

## Highlights

- Separate profiles and an incognito search profile.
- Saved tab sessions, tab groups, and recently closed tab restoration.
- Address suggestions from per-profile browsing history.
- Find in page with match counts and per-tab page zoom.
- Cookie controls, a global-cookie baseline, downloads, and ad blocking.
- In-browser KeePassXC login selection, filling, and explicit saving.
- A desktop launcher, dark mode, and familiar navigation shortcuts.

## Quick start

Requires Python 3.10+ and a graphical desktop. From the project directory:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./steed
```

To launch from your Linux applications menu without keeping a terminal open:

```bash
.venv/bin/python install_launcher.py
```

Then open **Steed** from the applications menu. Password features require
KeePassXC; see the [setup guide](DOCUMENTATION.md#third-party-password-managers).

## Project status and documentation

Browser essentials are implemented and covered by automated checks. Remaining
manual checks are tracked in the roadmap; the user has authorized proceeding.
The agent-service foundation includes task storage, a Qt command broker,
independent private pages, reusable browser services, and user-approved task
permissions. Task browsing retrieves documents in private profiles or explicitly
approved existing profiles alongside your tabs, with approval popups and limits on
sites and budgets.
Page inspection returns bounded text and validated references. Task clients can
follow links, edit supported controls, submit supported forms, scroll, and wait
for page state with explicit permissions. Task downloads require approved exact
filenames and never overwrite existing files. Task uploads require approved source
files and upload sites; selection and submission are separate actions. Active
scripts remain planned.
Optional local MCP access over stdio or authenticated local HTTP connects clients
to the running browser's task capabilities. You can watch a task's page, take
control through supported actions, and return it to the agent. See
[agent setup](DOCUMENTATION.md#local-agent-access-mcp).
Bookmarks and generic extension support are planned.

- [DOCUMENTATION.md](DOCUMENTATION.md): installation, usage, configuration,
  troubleshooting, development checks, and technical design.
- [ROADMAP.md](ROADMAP.md): current progress, remaining browser checks, and future
  milestones.
