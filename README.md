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
Optional local MCP access over stdio or authenticated HTTP lets external agents
browse approved sites alongside your tabs, with task permissions, watch/handoff,
file transfers and retained drafts/receipts. Agent-built Python packages can generate
attachments and compose approved app tools in an isolated local worker after
exact-package approval. Users share selected input files and export results from
Task Permissions. Active website scripts and generic extensions remain planned.
Google Docs/Gmail connections keep credentials in the desktop keyring and offer
persistent account access switches. Real-account acceptance remains pending. See
[agent setup](DOCUMENTATION.md#local-agent-access-mcp) and
[Google setup](DOCUMENTATION.md#google-app-connections), plus
[local worker requirements](DOCUMENTATION.md#running-an-agent-built-local-worker).
Website and agent prompts offer one-time or saved approvals, with explicit
site/action/profile scope and separate screens to remove saved choices.
Bookmarks and generic extension support are planned.

- [DOCUMENTATION.md](DOCUMENTATION.md): installation, usage, configuration,
  troubleshooting, development checks, and technical design.
- [ROADMAP.md](ROADMAP.md): current progress, remaining browser checks, and future
  milestones.
