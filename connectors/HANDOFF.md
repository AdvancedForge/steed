# Default connector implementation handoff

Assessment: October 3, 2026. Framework baseline: `e5cacab` (reviewed recipe v2).
The user approved declarative request plans next and asked for an independently
implementable, conflict-free connector backlog. Start with the compatible subsets
below; do not add service adapters to browser Python.

## What “no code change” means

The ready subsets work through the **unchanged browser runtime** using JSON recipes,
service setup instructions and new provider-specific tests. Compatibility probes
in `compatibility/` exercise documented identity, read and write wire shapes through
`parse_recipe`, account connection and `RecipeConnector`, including actual payload
previews. They use synthetic accounts and an in-memory keyring. Four automated
checks pass; no live service/account or provider consent flow has been tested.

These are **importable starter subsets**, not full-featured default connectors.
Steed loads built-in Google plus user-reviewed installed recipes. Commit `6eaa4ff`
adds **App Connections → Add a Service…**, a shared packaged JSON picker. Connector
contributors supply data under
`connectors/recipes/`. Default availability must not silently connect accounts,
enable switches or skip installation/payload review.

## Priority list

| Priority | Service | Useful scope | Runtime compatibility now | Contributor action |
| --- | --- | --- | --- | --- |
| P0 | Google | Docs, Drive and Gmail | Existing maintained provider | Keep working; shared registration remains deferred. No parallel edits. |
| P0 | Microsoft 365 / Outlook / OneDrive / Calendar / To Do | Main alternative productivity suite | Selected GETs fit with a delegated bearer token; integrated sign-in, paging and writes are **not verified ready** | Prepare read subset and official setup notes; gate broader release on OAuth acceptance/framework work. |
| P1 | Todoist | Capture tasks from browsing and inspect active tasks | **Recipe-only ready subset:** personal token, task reads, bounded cursor list, inbox creates | Implement first; no project selection, completion/delete or undocumented request-ID lookup. |
| P1 | GitHub | Repository context and actionable issues | **Recipe-only ready subset:** personal token, owner-pinned repository reads/issue creates | Implement second; an owner must be fixed in reviewed source, not supplied as an arbitrary path. |
| P1 | GitLab.com | Project context and issues | **Recipe-only ready subset:** bearer-compatible personal token, numeric project reads/issue creates | Implement third; no self-hosted/private domains, arbitrary project paths or updates/deletes. |
| P1 | Notion | Personal/team knowledge and pages | **Needs framework work:** required version header and nested request objects; sign-in compatibility also needs acceptance | Record desired scopes and wire fixtures; do not bypass header restrictions. |
| P1 | Slack | Collaborative messages and context | **Needs framework work:** channel destinations, request parameters and application-level `ok` checks; workspace identity/sign-in needs acceptance | Prepare fixtures; never classify `ok:false` as a successful send. |
| P2 | Asana | Project/task context and task capture | Read shape fits; actual token syntax/sign-in acceptance is unresolved. Writes need nested `data` and workspace/project inputs | Prepare response fixtures/setup notes; do not declare PATs universally compatible with Steed's strict bearer parser. |
| P2 | Dropbox | Files and shared material | RPC-style POST reads, request objects and content endpoints exceed current format | Wait for framework support; no credential-bearing arbitrary requests. |
| P2 | Linear | Issues/projects | GraphQL POST reads, variables and error handling exceed current format | Wait for framework support; do not disguise reads as mutation tools. |

Priorities express product usefulness, not claimed popularity or completed support.
Implement the three ready subsets first; Microsoft is a high-priority follow-up,
not an excuse to widen the current authentication or network contract.

## Wider connector queue

The user requested more variety after the initial shortlist. These candidates
are **API-assessed backlog, not additional verified recipe-only implementations**.
Keep the three proven subsets above first; promote a candidate only after its
documented auth/identity/error contract passes a synthetic wire-shape test. A
working external MCP server also does not automatically make its underlying API
compatible with Steed recipes. Each row links its official starting reference.

| Priority | Category / candidate | First useful capability | Gate before packaging |
| --- | --- | --- | --- |
| P1 | Bookmarks — [Raindrop.io](https://developer.raindrop.io/v1/user/authenticated) | Read collections and saved links; later save browsing references | Bearer/identity shapes appear compatible; verify token format and the `result` success flag. Bookmark creates require an explicit URL input, not a guessed draft-to-URL conversion. |
| P1 | Structured data — [Airtable](https://airtable.com/developers/web/api/introduction) | Read chosen tables/records; later capture structured notes | Verify `/meta/whoami`, base/table pinning, scopes and offset paging. Record writes need nested payloads and field mappings; framework v3 is in progress, not this handoff's baseline. |
| P2 | Scheduling — [Calendly](https://developer.calendly.com/api-docs/calendly-api/users/get-current-user) | Read scheduling links and selected meetings | Identity uses a full URI, rejected by the current subject contract. Queries, scopes and provider plan access need verification. Bookings need separately reviewed structured inputs. |
| P2 | Knowledge — [Readwise / Reader](https://readwise.io/reader_api) | Read saved research/highlights; save selected articles | Authentication is `Authorization: Token`, not Bearer; its auth probe returns 204 without account identity. Needs trusted auth/identity support and explicit URL inputs. |
| P2 | Tasks — [ClickUp](https://developer.clickup.com/docs/authentication) | Read chosen tasks/lists; capture tasks | Personal tokens use raw Authorization; OAuth bearer tokens are currently non-expiring, incompatible with the current bounded-expiry exchange. Do not pretend PATs work by adding Bearer. |
| P2 | Boards — [Trello](https://developer.atlassian.com/cloud/trello/guides/rest-api/authorization/) | Read boards/cards; capture cards | Legacy key+token/OAuth1 differs from Bearer. New OAuth2 also exists; independently verify registration, native PKCE and identity before choosing a supported route. |
| P2 | Design — [Figma](https://developers.figma.com/docs/rest-api/authentication/) | Read selected design/file metadata and comments | Personal tokens use a service-specific header; OAuth has app/callback requirements. Confirm a compatible sign-in and scopes; binary image delivery is a separate workspace capability. |
| P2 | Research — [Zotero](https://www.zotero.org/support/dev/web_api/v3/basics) | Read selected references; later capture citations | Bearer API-key auth exists, but verify an account-bound identity endpoint without placing a secret in its URL. Library pinning, version headers, root-array responses and write revisions need acceptance. |
| P2 | Private cloud — [Nextcloud](https://docs.nextcloud.com/server/latest/developer_manual/client_apis/WebDAV/basic.html) | Read/export user-selected files and calendar material | WebDAV uses methods/XML/binary beyond the JSON recipe engine. Private/local servers need an explicitly scoped local-network adapter, not weakened global HTTPS rules. |
| P2 | Local notes — [Joplin](https://joplinapp.org/help/api/references/rest_api/) | Capture a note and read selected notes | Local HTTP API with query-token auth requires a distinct user-approved local-service adapter. Keep tokens out of recipe paths, logs and task results. |
| P3 | Personal media — [Spotify](https://developer.spotify.com/documentation/web-api/reference/get-current-users-profile) | Read chosen playlist metadata | API/profile shape is a candidate; verify current developer access, nullable labels and OAuth scopes/refresh. Playback control and playlist mutations are separate effects and are not inferred from read access. |

This is 21 service families including the first shortlist, spanning productivity,
developer tools, communication, files, knowledge, structured data, research,
bookmarks, design, scheduling and media. It is a menu of useful integrations,
not a promise to install every service by default. Start with common choices and
let users opt into the rest. Framework blockers stay with the framework agent;
the connector contributor can prepare source links, desired scopes and synthetic
fixtures in their owned paths without changing shared runtime code.

## Verified starter contracts

- **Todoist:** API origin `https://api.todoist.com`, identity `GET /api/v1/user`,
  subject `/id`, label `/email`. Read one `/api/v1/tasks/{destination}`; list
  `/api/v1/tasks` using `/results`, `/next_cursor`, `cursor`, at most three pages.
  Create `POST /api/v1/tasks` with `content ← artifact.title` and
  `description ← artifact.content`; omitted `project_id` targets the Inbox.
  Official current OpenAPI confirms these fields and the cursor response. Keep
  payload/result limits visible; listing large accounts can exceed Steed quotas.
- **GitHub:** origin `https://api.github.com`, identity `GET /user`, numeric subject
  `/id`, label `/login` (email can be null). The proof pins owner `octocat` in
  `/repos/octocat/{destination}` and `/repos/octocat/{destination}/issues`.
  Create fields are `title` and `body`; a real installation needs a new reviewed
  owner-specific source, not the proof account. Use fine-grained PAT repository
  permissions. Version/media headers are recommended, not required; the current
  engine uses GitHub's documented unversioned default. Do not promise version
  pinning, arbitrary owner/repo destinations, list pagination or OAuth yet.
- **GitLab.com:** identity `GET /api/v4/user`, subject `/id`, label `/username`;
  the documented bearer header works with personal tokens. Reads use
  `/api/v4/projects/{destination}`; create uses
  `/api/v4/projects/{destination}/issues` with `title` and `description`.
  Destinations should be numeric project IDs. Issue quick actions are service
  behavior; review the actual submitted draft. No lookup proves creation by a
  Steed operation ID, so uncertain creates require manual checking.
- **Microsoft read proof:** `https://graph.microsoft.com/v1.0/me`, subject `/id`,
  label `/userPrincipalName` rather than nullable `/mail`. The proof reads a drive
  item, the first calendar page and the first To Do list page. It does not follow
  `@odata.nextLink`; current cursor paging cannot follow those URLs. Full native
  OAuth needs an actual compatible registration/PKCE/token-response check;
  normalized scopes, refresh and expiry cannot be inferred from parsing a recipe.

## Ownership and branch rules

The **connector contributor** owns new files only under `connectors/recipes/`,
`connectors/fixtures/`, `connectors/SETUP.md` and
`tests/test_default_connector_profiles.py`. Compatibility probes here are references,
not installed defaults. Keep each ready service in a small separate commit.
Branch from the published handoff commit, e.g. `connectors/starter-profiles`, and
open a PR; do not push to `main`, rewrite history or touch another agent's checkout.

The **framework agent** owns `services/`, `ui/`, `agent_mcp.py`, shared existing
recipe tests, MCP smoke scripts, `README.md`, `DOCUMENTATION.md` and `ROADMAP.md`.
It is implementing catalog discovery and declarative plans while preserving v1/v2.
Do not edit these files for a service-specific workaround. Describe missing
capabilities in the PR/issue and continue with compatible subsets. No credentials,
real account fixtures, dependency installations or external sends belong in a PR.

## Acceptance for each service PR

1. Supply valid v1/v2 JSON plus source links, least-permission setup and accurate
   limits. No fake API fields or guessed reconciliation/idempotency semantics.
2. Run `.venv/bin/python -m unittest discover -s tests -p test_connector_compatibility.py -v`
   and provider-specific tests, then Ruff/format. Cover nullable identity labels,
   destination escape attempts, omitted optional response fields, quota failures,
   exact reviewed payloads and uncertain writes without replay where relevant.
3. Use synthetic documented responses. Source review, account setup and every
   create/send payload approval must remain user-owned. Tokens stay in the keyring.
4. Describe automated results separately from live consent/account acceptance.
   Publish data/tests even if live credentials are unavailable; do not label an
   untested service fully verified. The framework agent integrates catalog display.

## Official references

- [Todoist current API and OpenAPI](https://developer.todoist.com/api/v1/),
  [personal-token quick start](https://developer.todoist.com/).
- GitHub [authenticated identity](https://docs.github.com/en/rest/users/users#get-the-authenticated-user),
  [repositories](https://docs.github.com/en/rest/repos/repos#get-a-repository),
  [issue creation](https://docs.github.com/en/rest/issues/issues#create-an-issue),
  [unversioned default](https://docs.github.com/en/rest/about-the-rest-api/api-versions).
- GitLab [bearer/PAT authentication](https://docs.gitlab.com/api/rest/authentication/),
  [current user](https://docs.gitlab.com/api/users/#retrieve-the-current-user),
  [issue creation](https://docs.gitlab.com/api/issues/#create-an-issue).
- Microsoft [identity](https://learn.microsoft.com/en-us/graph/api/user-get?view=graph-rest-1.0),
  [drive reads](https://learn.microsoft.com/en-us/graph/api/driveitem-get?view=graph-rest-1.0),
  [calendar](https://learn.microsoft.com/en-us/graph/api/user-list-events?view=graph-rest-1.0),
  [To Do lists](https://learn.microsoft.com/en-us/graph/api/todo-list-lists?view=graph-rest-1.0),
  [mail paging](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0).
- [Notion required version header](https://developers.notion.com/reference/versioning),
  [Slack application-level error contract](https://docs.slack.dev/reference/methods/auth.test/).
- Asana [user identity](https://developers.asana.com/reference/getuser),
  [PAT setup](https://developers.asana.com/docs/personal-access-token),
  [nested task writes](https://developers.asana.com/docs/quick-start).
- Dropbox [HTTP RPC conventions](https://www.dropbox.com/developers/documentation/http/documentation),
  Linear [GraphQL API](https://linear.app/developers/graphql).
