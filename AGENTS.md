# Repository Guidelines

## Project Structure & Module Organization

Steed is a Linux desktop browser using Python 3.10+, PyQt6, and Qt WebEngine.
Root modules contain application logic: `main.py` starts the app,
`browser_window.py` coordinates windows, and `profile_manager.py` owns shared
engine profiles. History, cookies, downloads, filtering, and passwords have
separate modules. Widgets and dialogs live in `ui/`; regression tests and
standalone smoke scripts live in `tests/`. `codec_test.html` is a local media
fixture. `steed` and `install_launcher.py` provide executable and desktop launchers.

Keep README.md brief; put detailed instructions in DOCUMENTATION.md and progress,
acceptance checks, and planned work in ROADMAP.md.

## Build, Test, and Development Commands

Run from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
./steed
.venv/bin/python install_launcher.py
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
for startup/render/shutdown; DOCUMENTATION.md explains additional smoke checks
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
