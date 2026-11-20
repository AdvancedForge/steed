# Security assessment: agents in Steed

A structured internal review of the agent surface, November 2026. It is not
an external audit; the [release plan](RELEASE_PLAN.md) still asks for one.
[Documentation index](README.md).

## What is being protected, and from whom

**Assets.** Your signed-in sessions (cookies, local storage) in each profile;
saved passwords and one-time-code secrets in each profile's encrypted
`passwords.vault`; payment card details you type; files in your home folder;
devices on your network; Steed's own settings and grants.

**Attackers.**

- **An agent that goes wrong**, through its own mistakes or because a web page
  steered it (prompt injection). It may call any tool it has, in any order,
  with any arguments.
- **Hostile web pages**, including pages on a site you are signing in to
  (injected HTML in a forum or CMS page), and pages that try to tamper with
  Steed's own scripts.
- **Other local users.** Not other processes of your own user: anything
  running as you can already read your files, and Steed cannot stop it.

**Out of scope.** What a coding agent (Codex, Claude Code, an ACP agent) does
with its own tools outside Steed: its shell, files and network are governed
by its own permission model. Steed governs only what such an agent does
through Steed.

## Boundaries and how they hold

| Boundary | How it is enforced |
| --- | --- |
| Connecting to Steed | A private socket (0700 directory, owner-only), a per-run token in a 0600 file that refuses symlinks; HTTP is loopback-only with an exact Host check, no browser origins, and a bearer token. Chats get single-use, 60-second task bindings. |
| What a task may do | Exact origins, actions, profile, limits and file paths, approved by you (or by your access settings, saved rules or, if you turned it on, auto-approval). Checked again on every page action and network request. |
| Agents and pages | Page reads come from Steed's own script world; values of form fields are never returned; the journal keeps origins, not page text. Restricted sessions run no site scripts. |
| Passwords and cards | Agents cannot read, type into or submit password or card fields (by DOM action, by custom-button or keyboard activation, or by screenshot click and typing). Vault sign-in fills them without the agent seeing them, only on forms that look like signing in and that send to the same site in the same window. |
| You stay in charge | Pause All Agents holds every agent at the gateway; Take Back ends a tab hand-off; cancelling a task ends its grants. |

## Findings and what changed

Two reviewers looked at agent connections and authorization, and at what
agents can do to and read from pages. Findings, most severe first:

**Fixed.**

1. *Vault sign-in submitted any form with a password field*, so an agent
   could steer it into a change-email, delete-account or checkout form. It now
   signs in only on forms that look like signing in: at most three fields, one
   password, no new-password or card fields, no wording about deleting,
   changing account details or paying. It refuses otherwise
   (`not_a_sign_in_form`). A one-time code is never written into a card's
   security-code field.
2. *Vault sign-in's same-site check could be fooled* by a field named
   `action`, or a submit button with `formaction`/`formtarget` pointing to
   another site. It now reads the form's real destination, the button's
   overrides included, and requires the same window. This also protects your
   own autofill.
3. *Custom buttons, keyboard activation and drags skipped the password and
   card rule*, so on a page without a `<form>` an agent could press "Sign in"
   or "Pay" and let the site's script send what you typed. Any press is now
   refused while a filled password or card field shares its form (or, outside
   forms, the page).
4. *Screenshot clicks and typing had no such rule.* They now refuse to click
   into or type into password and card fields (including payment providers'
   frames), and refuse a click or Enter near a filled one.
5. *A password whose reveal toggle made it plain text, or text drawn as
   dots, was not recognised.* The type attribute and `-webkit-text-security`
   now count; card fields are also recognised by label and placeholder.
6. *A failed vault sign-in left the password in the field*, where an agent
   could reveal and photograph it. Steed now empties what it filled.
7. *A remembered sign-in approval covered every page of the site for three
   minutes.* It now covers only the same page (a login's next step).
8. *"Always" access settings approved any origin or file path*, including
   your router, `localhost` and `~/.config/autostart`. "Always" now covers only
   public websites and files in Downloads; anything else asks.
9. *A remembered "allow every time" from a private profile covered your
   signed-in profiles.* It no longer does.
10. *Recovered tasks were still approved automatically* by access settings or
    auto-approval. They now always ask, as documented.
11. *Pause All Agents let agents start or resume tasks, and relay batches
    kept going.* The pause now comes first; stopping things is still allowed.
12. *One-shot model questions (watches, tab organizer) followed redirects
    with the API key.* They no longer follow redirects or use cookies.
13. *Replay pictures could keep a typed card number or a revealed password,
    and their parent folder was listable by others.* No picture is taken while
    a filled password or card field shows or while you have control; the
    folder is private.

**Known limitations** (documented rather than fixed):

- **DNS rebinding.** A named site's permission includes DNS answers on your
  network; a site that later resolves to a local address could be reached.
  Steed checks origins, not resolved addresses (the engine offers no hook).
- **The run token is readable by your own processes.** A coding agent's own
  shell can read `agent-access.json` and open a connection outside its chat.
  Such a connection still needs your approval for anything (unless "Always",
  saved rules or auto-approval cover it). Giving chats a credential of their
  own is planned.
- **Connection slots.** Up to four connections; a process with the token can
  hold them. Turning agent access off and on frees them.
- **Heuristics.** Card fields, sign-in forms and page text aimed at agents
  are recognised by patterns. A generically named card field, a sign-in form
  with unusual wording, or paraphrased injection can slip past. Form-associated
  custom elements (some design-system password fields) are not inspected.
- **Site scripts.** In live sessions a site's own scripts can send anything
  the page holds; Steed's rules cover what agents do, not what the site does.
  One-click purchases with a saved card are not caught.
- **Card preview widgets** that draw the number as text appear in page reads.
- **Already dispatched actions** (a submitted form, a running app call) finish
  after a pause or cancel.

## How to report a problem

See [SECURITY.md](../.github/SECURITY.md).
