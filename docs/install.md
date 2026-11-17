# Installing Steed

Installation, the desktop launcher, graphics and video codecs, Google sign-in and updating. [Documentation index](README.md).

## Installation

Requires Python 3.10 or later and a graphical desktop session. Dependencies use PyQt6 and PyQt6-WebEngine 6.11.0 with Qt 6.11.x. Linux x86-64 wheels require glibc 2.34 or later; ARM64 wheels require glibc 2.39 or later.

The quickest way, from the project directory:

```bash
./install.sh             # .venv, packages, and Steed in your applications menu
./install.sh --default   # the same, and Steed becomes your default browser
./install.sh --update    # later: the newest Steed (git pull), then the same
```

Nothing is installed outside the folder except the menu entry. Or do the same by
hand: create a virtual environment if one does not already exist:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade -r requirements.txt
```

Run the browser with that environment:

```bash
./steed
```

The executable launcher always uses the project's virtual environment. You can
also run `.venv/bin/python main.py` directly. Running `python3 main.py` without
activating the environment may use a different PyQt installation.

### Launch without a terminal

After installing dependencies, register this checkout in your Linux application
menu once:

```bash
.venv/bin/python -m browser.install_launcher
```

Open **Steed** from the applications menu, and pin it to your panel if desired.
The launcher runs with `Terminal=false`, so no terminal needs to stay open.
It uses this checkout's `.venv` and works regardless of the desktop's working
directory. No root access or standalone executable build is needed.

The executable `steed` in the project directory also starts the browser directly.
Keep the checkout and `.venv` in place; if you move them, run the installer again
from the new location. To remove the menu entry, delete `steed.desktop` from
`${XDG_DATA_HOME:-$HOME/.local/share}/applications/`.

Launching Steed again while it is running never starts a second copy. What it
does is Settings → General → **When Steed is opened again**: show the window
you were using (the default), open a new window, or open a new tab in your
window (`Ctrl+N` always opens a new window). Closing a window closes only that
window; windows opened from it stay open. A window closed while others stay
open can be brought back with Tabs → Reopen Closed Window; when the last window
closes, or you quit with `Ctrl+Q`, every window comes back at the next start. `./steed https://example.org` opens the link
as a tab in the most recently used window, and `./steed page.html` opens a
local file. Only HTTP/HTTPS addresses and existing local pages, PDFs, images
and text files are passed on,
through a private socket in your runtime directory.

**Default browser.** Settings → General → **Make Steed your default browser**
registers Steed's menu entry for web links and HTML files (writing it first if
needed) and makes it the default with `xdg-settings` (or `xdg-mime` where the
desktop has no `xdg-settings`). Links from other apps, and HTML files opened
from a file manager, then open in Steed. The menu entry uses Steed's own icon
(`ui/assets/steed.svg`). If the running browser
does not answer within two seconds, a warning is shown instead.

### Graphics troubleshooting

The browser lets Qt select its graphics backend. Older versions forced `--use-gl=desktop`, which can abort with `--use-gl=desktop is not supported with the current configuration`.

If that option was also set in your shell environment, clear it before launching:

```bash
unset QTWEBENGINE_CHROMIUM_FLAGS
.venv/bin/python main.py
```

For a separate GPU-driver problem, try disabling GPU acceleration for that run:

```bash
QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" .venv/bin/python main.py
```

The browser preserves explicit Chromium options and enables PipeWire screen capture without forcing a graphics backend.

### Video codecs (H.264 and AAC) — optional

The PyPI Qt WebEngine plays WebM/VP8/VP9, AV1, Opus and MP3, but not the
patent-licensed H.264 video and AAC audio. Videos and "GIFs" (short MP4 files,
as on Discord, Tenor and Giphy) that come only in those formats do not play.
`browser/build_codecs.py` builds the same Qt WebEngine version from source with
proprietary codecs and swaps it into `.venv`; Steed's code does not change.
The build is for use on this computer, not for redistribution.

```bash
.venv/bin/python -m browser.build_codecs check     # prints the apt command for missing packages
.venv/bin/python -m browser.build_codecs build     # hours; run again to continue after a stop
.venv/bin/python -m browser.build_codecs install   # backs up the PyPI files, then swaps
.venv/bin/python -m browser.build_codecs status    # which engine is in use and what plays
.venv/bin/python -m browser.build_codecs restore   # put the PyPI engine back
.venv/bin/python -m browser.build_codecs clean     # free the build's disk space
```

Steed works without it; only videos that come solely as H.264/AAC need it. The
build needs about 15 GB of disk while it runs and takes several hours (about
five on an 8-core, 16 GB machine). Afterwards `clean` removes the source,
compiled files, Qt's development files and build tools (about 13 GB), keeping
only the finished engine (`stage/`, under 0.5 GB) so `install` still works after
a pip reinstall. It lists what it removes and asks first (`--yes` skips the
question); a later build downloads and compiles everything again.

The build works in `~/src/steed-qtwebengine` (`--work-dir` to change; it checks
for 60 GB free to leave room) with one compile job per 4 GB of memory by default (`--jobs`;
Chromium's largest combined source files need about that much each, and six
jobs ran out of memory on a 16 GB machine). A stopped build continues where it
left off when `build` is run again. It downloads Qt's source for the exact `PyQt6-WebEngine-Qt6` version
in `.venv`, checks Qt's published checksum, and builds against Qt's matching
development files (installed there with aqtinstall, as are cmake and ninja).
`install` refuses a build of another version, or one whose libraries lack any
function the PyPI ones export. Reinstalling or upgrading `PyQt6-WebEngine-Qt6`
with pip puts the PyPI engine back; `status` says so, and `install` (or a new
`build` for a new version) restores codecs. `requirements.txt` pins the exact
version for this reason.

Measured on the development machine, the codec build uses the same memory as
the PyPI engine (383 against 386 MB with one tab once idle; the engine library's
resident pages are the same in every process) and the same speed, and plays
H.264 with AAC (a 3-second test video decoded all 90 frames). Its library has
three different internal libstdc++ exports (digit tables), which nothing in
Steed's environment uses; `install` refuses only missing names that PyQt6 or a
Qt library actually imports.

If that happens, Steed says so a few seconds after it starts (once per Qt
version if you choose **Don't remind me for this version**), with a button to
`steed:features`. The check compares the engine library's size and modification
time with the ones recorded at install, so it costs nothing measurable at
startup; `python -m browser.build_codecs status` compares the full checksum.

### Engine & features (`steed:features`)

**View → Engine & Features…** or `steed:features` shows the Qt WebEngine
version, the Chromium version its web features come from, the Chromium version
of its security fixes, and whether the video-codec build is installed. Below,
44 web features are tested in a real https page in a private throwaway profile
(video and audio formats, JavaScript, page features, CSS, devices, sign-in and
storage, built-in AI). Each missing one says why: newer than this engine (a Qt
WebEngine upgrade brings it), left out of Qt WebEngine on Linux (an upgrade will
not), or missing from the PyPI engine (`browser/build_codecs.py`). **Check again**
repeats the test, for example after swapping the engine and restarting.

### Knowing about updates

Once a day (Settings → General → **Check for updates**, on by default) Steed
runs `git fetch` in its folder and asks PyPI for the newest Qt WebEngine. If
there is something new, the status bar says so once, and `steed:features` →
**Updates** shows it (with **Check now**). Nothing is installed by itself:
`./install.sh --update` brings the newest Steed, and the engine is upgraded as
below. The check sends nothing about your browsing.

### Upgrading Qt WebEngine

`browser/upgrade_engine.py` moves Steed to newer PyQt6 / Qt WebEngine releases
without risking the working setup:

```bash
.venv/bin/python -m browser.upgrade_engine check      # installed and newest versions
.venv/bin/python -m browser.upgrade_engine try        # .venv-upgrade: install and test
.venv/bin/python -m browser.upgrade_engine try --quick --compare before.json
.venv/bin/python -m browser.upgrade_engine apply      # pin and install what passed
.venv/bin/python -m browser.upgrade_engine rollback   # versions from before apply
```

`try` installs the newest releases into `.venv-upgrade` (never `.venv`) and
reports the Chromium version and web-feature results (as on `steed:features`),
the startup smoke test, the test suite (`--quick` runs a subset of nine files)
and, with `--compare`, the timing check against an earlier result. Its report
is `.venv-upgrade/steed-trial.json`. `apply` refuses unless that trial passed,
records the current versions for `rollback`, pins the four packages exactly in
`requirements.txt` and installs them into `.venv`. A video-codec build is for one
version: after `apply`, run `python -m browser.build_codecs build` and `install` again.

### Google sign-in

Google sign-in compatibility is enabled by default in **General → Settings**. It sends a Firefox User-Agent to `https://accounts.google.com/`, following [qutebrowser's account-host workaround](https://github.com/qutebrowser/qutebrowser/issues/5182). Other sites use your configured/default User-Agent. Main-page GET redirects into or out of the account host are restarted to avoid Qt losing the per-site header; POST requests are never replayed.

Restart with `.venv/bin/python main.py`, then open `https://accounts.google.com/` and sign in normally. The compatibility setting takes precedence over the global User-Agent override for this host. Reload sign-in tabs after changing the setting.

Local HTTPS tests verify redirects, form bodies, and the opt-out on the pinned Qt 6.11 runtime. The user reported account sign-in working on October 1, 2026; persistence after restart and third-party sign-in remain unconfirmed. [Google can restrict embedded-browser sign-ins](https://support.google.com/accounts/answer/7675428), and this workaround cannot guarantee acceptance for every account. External-browser OAuth does not create a signed-in web session in this browser.

The same HTTPS fixture stalled on the older system Qt 6.4.2 installation. Use the project's virtual environment for Google sign-in; older-Qt startup compatibility does not establish login compatibility.
