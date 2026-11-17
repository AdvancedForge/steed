# Repository Guidelines

## Project Structure & Module Organization

Steed is a Linux desktop browser using Python 3.10+, PyQt6, and Qt WebEngine.
`main.py` starts the app; `agent_mcp.py` is the MCP adapter agents launch (its
path is in their saved settings, so it stays at the root). The browser core
lives in `browser/`: `browser_window.py` coordinates windows and
`profile_manager.py` owns shared engine profiles; history, cookies, downloads,
filtering, passwords and extensions have separate modules. Widgets, dialogs and
Steed's own pages (`ui/web/`) live in `ui/`; agent tasks, permissions and
providers in `services/`. Regression tests, smoke scripts and fixtures (such as
`tests/fixtures/codec_test.html`) live in `tests/`. `steed` and
`browser/install_launcher.py` provide executable and desktop launchers. Helper
commands run as `.venv/bin/python -m browser.<name>`.

Keep README.md brief. Detailed instructions go in `docs/` (one file per topic,
listed in `docs/README.md`); where things stand and planned work go in
ROADMAP.md, and what each round changed goes in `docs/history/progress-log.md`
(newest first).

## Build, Test, and Development Commands

Run from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
./steed
.venv/bin/python -m browser.install_launcher
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m unittest discover -s tests -v
```

These create the environment, install runtime/development dependencies, launch
Steed, register its application-menu entry, and run lint, formatting, and tests.
No separate compilation step is required. Use the project's virtual environment
rather than system PyQt. For headless checks, prefix the test command with
`QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu"`.

## Coding Style & Naming Conventions

Use four-space indentation, `snake_case` modules/functions, `PascalCase` classes,
and `UPPER_CASE` constants. Add type hints and short docstrings where useful.
Ruff settings in `pyproject.toml` target Python 3.10 and 88-column formatting.
Use `.venv/bin/python -m ruff format .` to format changes.

## Testing Guidelines

Tests use `unittest`; name files `test_*.py` and methods `test_*`. Cover changed
behavior with meaningful regressions, including failure paths and profile
isolation. No numeric coverage threshold is configured. Use temporary profiles,
local pages, and synthetic credentials. Run `tests/smoke_browser.py` separately
for startup/render/shutdown; docs/development.md explains additional smoke checks
and their dependencies. Record manual desktop checks separately from automated
results.

## Commit & Pull Request Guidelines

History uses short, descriptive subjects such as `fix google login` and
`reorganize docs`; Conventional Commit prefixes are not required. Keep commits
focused. PRs should describe the problem, resulting behavior, validation commands
and results, and remaining limitations. Link relevant issues and include
screenshots for visible UI changes.

## Architecture & Configuration

Keep engine operations on the Qt thread and destroy pages before profiles.
Preserve shared-window ownership and incognito isolation. User state lives in
`~/.custom_browser/`; never commit real profiles, vaults, or credentials. Passwords live only in each
profile's encrypted `passwords.vault`; never write them to JSON, logs or tests.
