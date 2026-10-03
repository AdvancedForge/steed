# Default connector profiles

These JSON files are **starter subsets**, not complete service integrations. They
are data: the unchanged recipe interpreter parses them, and no service adapter is
added to the browser runtime. Every profile here has run through synthetic wire
tests in `tests/test_default_connector_profiles.py`. None of them has been tested
against a live service, and no live account or provider consent flow has been
exercised.

Profiles are described as what they are, so review can be quick and honest:

- the exact origin, identity route and pointers;
- every read route and every write route;
- the exact JSON body a reviewed create will send;
- what the profile deliberately cannot do.

## Install flow (always user-owned)

1. Open **App Connections**.
2. Choose **Add a Service…** for a packaged profile, or **Add Connector Recipe…**
   to import your own reviewed copy from disk.
3. Read the source review: origin, identity request, per-tool effect, method,
   path, destination type, payload bindings or template, and the recipe SHA-256.
4. Connect the account with that service's own token. The token is written only
   to the desktop keyring; Steed never stores it in recipes, task results or logs.
5. Choose which actions this account may offer to tasks.

Installing a profile connects nothing and grants no task authority. Every create
or send is separately reviewed as an exact JSON payload before dispatch, and an
uncertain write is reported as uncertain so you check the service's own receipt
by hand. Steed never resends automatically.

Profiles only appear after you install them; a packaged profile is not a default
account and carries no approval.

---

## Todoist

`connectors/recipes/todoist.json`, origin `https://api.todoist.com`, bearer
authentication with a personal API token.

Scope shipped:

| Action | Wire contract |
| --- | --- |
| Identity | `GET /api/v1/user`, subject `/id`, label `/email` |
| Read one task | `GET /api/v1/tasks/{destination}` returning `/id` and `/content` |
| List tasks | `GET /api/v1/tasks`, `/results`, cursor `next_cursor`, at most 3 pages |
| Create inbox task | `POST /api/v1/tasks` with `content` and `description` only |

Deliberately excluded: project or section selection, task completion, deletion,
and the Sync API. An omitted `project_id` targets the Inbox project, so the
reviewed payload always lands in the Inbox unless Todoist changes that contract.

Notes on the wire shape:

- Todoist's own identity document also returns the account's personal API token
  in a top-level `token` field. Only `/id` and `/email` are selected, so that
  value is never placed in a result, review or task context.
- `description` is optional on a task. The packaged profile therefore selects
  only `/id` and `/content`, so a task without a description still reads. A
  profile that selects a field the service omitted fails the read instead of
  inventing a value.
- Todoist publishes per-endpoint request limits and returns an HTTP error status
  when one is exceeded. `list_tasks` is bounded to three pages; Steed does not
  retry, and a quota failure on a create is reported as uncertain rather than
  resent.

### Least-permission setup

1. In Todoist open **Settings → Integrations → Developer** and copy the personal
   API token.
2. In Steed, install the profile, then paste the token into the Connect step for
   that account and choose only the actions you want to expose.
3. Revoke the token in the same Todoist screen at any time. Steed removes stored
   credentials when you disconnect the account.

A Todoist personal token is account-wide and cannot be scoped per project, so
this subset keeps its authority minimal by only reading selected tasks, reading a
bounded task listing, and creating Inbox tasks that you review first.

Official references:

- [Todoist API](https://developer.todoist.com/api/v1/)
- [Todoist authorization and scopes](https://developer.todoist.com/api/v1/#tag/Authorization)

---

## GitHub

`connectors/recipes/templates/github-owner-template.json`, origin
`https://api.github.com`, bearer authentication with a fine-grained personal
access token.

This one is a **template, not a packaged profile**, because the owner has to be
fixed in source you reviewed. A packaged file with a placeholder owner would be
offered in the picker while being unable to reach anything, so the template lives
outside the packaged catalog and is imported deliberately.

Scope shipped:

| Action | Wire contract |
| --- | --- |
| Identity | `GET /user`, subject `/id`, label `/login` |
| Read one repository | `GET /repos/{owner}/{destination}` returning `/id`, `/full_name`, `/html_url` |
| Create issue | `POST /repos/{owner}/{destination}/issues` with `title` and `body` |

Deliberately excluded: arbitrary owners or repositories chosen at call time, issue
updates and comments, pull requests, releases, and list pagination.

Notes on the wire shape:

- `GET /user` returns a `login` and a numeric `id`. Its `email` is `null` unless
  the token can read it, so a private email is never the account label.
- Only the repository **name** is a `{destination}` segment, and the owner is
  written into the reviewed path. A destination such as `other-owner/repo`,
  `../../other/repo`, or anything with a query or scheme is rejected before any
  request.
- Issue creation sends exactly `title` and `body`. No labels, assignees, projects
  or milestone are inferred from the draft.
- Steed does not pin an `X-GitHub-Api-Version` header, so requests use GitHub's
  documented unversioned default. Version pinning is not claimed.
- GitHub applies primary and secondary rate limits (documented as 5,000 requests
  per hour for authenticated REST requests, plus secondary limits). Steed does not
  retry; a create that fails after dispatch is reported as uncertain, so check
  GitHub's own issue list before resending.

### Owner step (user-owned)

1. Copy `connectors/recipes/templates/github-owner-template.json` to your own
   location, for example `~/steed-github.json`.
2. Replace every `REPLACE-WITH-YOUR-GITHUB-OWNER` with the owner that holds your
   repositories, for example `my-github-login`. The reviewed source review shows
   the resulting paths before anything is installed.
3. In Steed choose **App Connections → Add Connector Recipe…**, select the copy,
   read the source review and install it.
4. Connect the account with the token below, then choose the actions to expose.

### Least-permission setup

1. In GitHub open **Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token**.
2. Set a short expiry, choose the account, and restrict **Repository access** to
   only the repositories you want, rather than "All repositories".
3. Grant only **Issues: Read and write** (or **Issues: Read-only** if you only
   want reads). Repository metadata access that the token needs is granted
   automatically. Do not add Contents, Pull requests, Actions, Administration or
   any "Account permissions" scope.
4. Paste the token into Steed's Connect step. Steed stores it in the desktop
   keyring and never writes it into a recipe, task or result.
5. Revoke it in the same GitHub page at any time; disconnecting in Steed removes
   the stored credential.

Official references:

- [Get the authenticated user](https://docs.github.com/en/rest/users/users#get-the-authenticated-user)
- [Get a repository](https://docs.github.com/en/rest/repos/repos#get-a-repository)
- [Create an issue](https://docs.github.com/en/rest/issues/issues#create-an-issue)
- [REST API versions](https://docs.github.com/en/rest/about-the-rest-api/api-versions)
- [Rate limits for the REST API](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
- [Fine-grained personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)

---

## GitLab.com

`connectors/recipes/gitlab.json`, origin `https://gitlab.com`, bearer
authentication with a personal access token.

Scope shipped:

| Action | Wire contract |
| --- | --- |
| Identity | `GET /api/v4/user`, subject `/id`, label `/username` |
| Read one project | `GET /api/v4/projects/{destination}` returning `/id`, `/path_with_namespace`, `/web_url` |
| Create issue | `POST /api/v4/projects/{destination}/issues` with `title` and `description` |

Deliberately excluded: self-hosted or private GitLab instances, namespace project
paths such as `group/project`, issue updates and deletes, and issue listing. Issue
listing is left out because GitLab pages with an offset and a `Link` header, which
the current cursor pagination cannot follow; that is shared framework work, not a
connector workaround.

Notes on the wire shape:

- Destinations are numeric project IDs. A namespace path cannot be expressed,
  because a reviewed path segment never contains a slash.
- Issue creation sends exactly `title` and `description`. GitLab interprets a
  leading slash command in an issue title or description as a quick action, so
  the `create_issue` tool description repeats that warning where the source
  review and the task's own payload preview are read, and Steed sends the
  reviewed draft unchanged without stripping or guessing commands.
- Nothing here proves that a create committed. There is no reviewed
  reconciliation route and no idempotency header, so an uncertain create is
  reported as uncertain and you check the GitLab issue list yourself.

### Least-permission setup

1. In GitLab open **Preferences → Access tokens** (or **User settings → Access
   tokens**) and create a token with a short expiry date.
2. Choose the narrowest scope you need:
   - `read_api` for reads only, or
   - `api` when you also want reviewed issue creation. Issue creation is not
     available to `read_api`.
3. Name the token so you can recognise it, for example `steed-connector`, and
   record the expiry date.
4. Paste it into Steed's Connect step for that account and select only the
   actions you want to expose to tasks. The token is stored in the desktop
   keyring.
5. Revoke it on the same page at any time; disconnecting in Steed removes the
   stored credential.

Official references:

- [GitLab REST authentication](https://docs.gitlab.com/api/rest/authentication/)
- [Retrieve the current user](https://docs.gitlab.com/api/users/#retrieve-the-current-user)
- [Get single project](https://docs.gitlab.com/api/projects/#get-single-project)
- [Create an issue](https://docs.gitlab.com/api/issues/#create-an-issue)



---

## Wider queue: assessed, not shipped

`connectors/fixtures/assessed_candidates.json` records the API assessment for all
21 service families, including the three shipped above. Each row has one
official starting reference, the documented authentication header, the documented
identity route and label, the paging model, the error contract, the host, and the
gate that must be cleared before a profile may be packaged.

Assessed candidates are **not** connector implementations. Nothing in that file is
installed, and a row is never a default account or an approval.

| Service | Status | Blocking gate |
| --- | --- | --- |
| Google | maintained | Framework-owned provider; no parallel recipe |
| Todoist | packaged | none outstanding for the shipped subset |
| GitHub | template | owner must be fixed in newly reviewed source |
| GitLab.com | packaged | none outstanding for the shipped subset |
| Microsoft 365 | gated | native OAuth, `@odata.nextLink` paging and writes unverified |
| Notion | gated | every request needs a `Notion-Version` header |
| Slack | gated | channel destinations and `ok:false` error checking |
| Asana | gated | token syntax/sign-in acceptance and workspace inputs |
| Dropbox | gated | reads are RPC-style POST request objects |
| Linear | gated | reads are GraphQL POSTs with variables |
| Raindrop.io | gated | `/rest/v1` prefix, `result` flag, offset paging |
| Airtable | gated | base/table pinning, optional identity label, offset paging |
| Calendly | gated | identity is a URI; next page is a full URL |
| Readwise / Reader | gated | `Authorization: Token`; auth probe returns 204 with no identity |
| ClickUp | gated | bare personal token header; non-expiring OAuth tokens |
| Trello | gated | key+token credentials, or OAuth 2.0 PKCE acceptance |
| Figma | gated | personal tokens use `X-Figma-Token` |
| Zotero | gated | version header, root-array collections, Link-header paging |
| Nextcloud | gated | self-hosted host with port, WebDAV/XML, mandatory OCS header |
| Joplin | gated | local plaintext origin, query-parameter token, no identity |
| Spotify | gated | OAuth-only access, nullable label, offset paging |

### Engine constraints every gate is measured against

- A profile origin is one exact public HTTPS domain: no port, no path, no
  credentials. That alone rules out self-hosted and local services.
- Request headers are limited to `Authorization`, `Accept` and `Content-Type`
  plus at most one reviewed operation-ID header, so services that require their
  own header cannot be expressed.
- Identity must be one GET returning HTTP 200 JSON with a usable subject string
  and a non-empty label string. A URI subject or an empty `204` probe cannot
  identify an account.
- Reads are GET only, with optional bounded cursor pagination through one
  reviewed query parameter. Offset, page-number, keyset and `Link`-header paging
  are not followed.
- A write is a single POST with no automatic retry, and an uncertain write is
  reported as uncertain because nothing in this subset can reconcile it.

### Promoting a candidate

1. Clear its auth, identity, protocol and runtime gate using official
   documentation only. Do not bypass a header restriction, and do not disguise a
   read as a write tool.
2. Add synthetic documented responses under `connectors/fixtures/` and
   unchanged-runtime wire-shape tests in
   `tests/test_default_connector_profiles.py`, covering a nullable identity
   label, destination escapes, the exact reviewed payload, and the documented
   failure or quota path.
3. Promote to `connectors/recipes/<slug>.json` only when those tests pass, then
   add its least-permission setup section above.
4. Record live consent and account acceptance separately from automated results.
   An untested service is never described as fully verified.

### Shared framework work raised by this queue

These are missing capabilities, not connector problems, and are not worked around
here:

- per-request provider headers (Notion `Notion-Version`, Figma `X-Figma-Token`,
  Zotero `Zotero-API-Version`, Nextcloud `OCS-APIRequest`);
- non-Bearer token schemes (Readwise `Token`, ClickUp bare tokens, Trello
  key+token) and a trusted way to accept them;
- identity shapes beyond a subject string and label, including URI subjects and
  204 probes;
- offset, page-number, keyset, `Link`-header and `@odata.nextLink` paging;
- POST reads with request objects or variables (Dropbox RPC, Linear GraphQL);
- application-level success flags (`ok:false`, Raindrop `result`, OCS `statuscode`);
- native OAuth sign-in acceptance, scope normalization and refresh (Microsoft
  365, Asana, Spotify, Trello OAuth 2.0);
- an explicitly scoped local-service adapter for Nextcloud and Joplin, without
  weakening the global HTTPS rule;
- reconciliation of uncertain writes, so a create could be confirmed by an
  operation ID rather than a manual check.
