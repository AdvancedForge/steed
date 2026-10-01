# Steed

A Python desktop browser built with PyQt6 and Qt WebEngine. Originally developed
for a Framework 12 laptop, Steed currently targets Linux desktop use with separate
browsing profiles and direct KeePassXC password management.

## Highlights

- Separate profiles and an incognito search profile.
- Saved tab sessions, tab groups, and recently closed tab restoration.
- Address suggestions from per-profile browsing history.
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
The first agent-service foundation is in development: task storage, a Qt command
broker, and independent private pages. External-agent access, bookmarks, and
generic extension support are planned.

- [DOCUMENTATION.md](DOCUMENTATION.md): installation, usage, configuration,
  troubleshooting, development checks, and technical design.
- [ROADMAP.md](ROADMAP.md): current progress, remaining browser checks, and future
  milestones.
