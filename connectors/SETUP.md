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
| Read one task with its description | `GET /api/v1/tasks/{destination}` also returning `/description` |
| List tasks | `GET /api/v1/tasks`, `/results`, cursor `next_cursor`, at most 3 pages |
| Read one project | `GET /api/v1/projects/{destination}` returning `/id`, `/name`, `/is_shared`, `/is_favorite` |
| List projects | `GET /api/v1/projects`, `/results`, cursor `next_cursor`, at most 3 pages |
| Create inbox task | `POST /api/v1/tasks` with `content` and `description` only |

Deliberately excluded: project or section selection, task completion, deletion,
and the Sync API. An omitted `project_id` targets the Inbox project, so the
reviewed payload always lands in the Inbox unless Todoist changes that contract.

Notes on the wire shape:

- Todoist's own identity document also returns the account's personal API token
  in a top-level `token` field. Only `/id` and `/email` are selected, so that
  value is never placed in a result, review or task context.
- `description` is documented as optional on a task. `read_task` therefore stays
  minimal, so a task without a description still reads, and `read_task_detail`
  returns the description for a task that documents one. If a response omits the
  description, the detail read fails closed instead of inventing a value.
- `GET /api/v1/projects` documents the same `results` plus `next_cursor` shape as
  tasks, so project listing is bounded the same way.
- The documented project response is a union of the personal and workspace
  project views. Only fields present in both are selected; the workspace-only
  `access`, `role` and `inbox_project` fields are deliberately not selected, so a
  workspace project does not fail the read.
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

This one is a **template, not a packaged profile**, because the owner and the
repository have to be fixed in source you reviewed. A packaged file with
placeholder values would be offered in the picker while being unable to reach
anything, so the template lives outside the packaged catalog and is imported
deliberately. One imported copy covers one repository, which is also the smallest
thing a fine-grained token can be limited to.

Scope shipped:

| Action | Wire contract |
| --- | --- |
| Identity | `GET /user`, subject `/id`, label `/login` |
| Read the repository | `GET /repos/{owner}/{repository}` returning `/id`, `/full_name`, `/html_url` |
| Read one issue | `GET /repos/{owner}/{repository}/issues/{destination}` by issue number |
| Create issue | `POST /repos/{owner}/{repository}/issues` with `title` and `body` |

Deliberately excluded: arbitrary owners or repositories chosen at call time, issue
updates and comments, releases, and issue-list pagination.

Notes on the wire shape:

- `GET /user` returns a `login` and a numeric `id`. Its `email` is `null` unless
  the token can read it, so a private email is never the account label.
- The owner and repository are written into the reviewed path, so only an issue
  number is a `{destination}` segment. A destination such as `1/comments`,
  `../../issues/1`, or anything with a query or scheme is rejected before any
  request.
- On an issue, `number`, `title`, `state` and `html_url` are documented as
  required, while `body` and `state_reason` are documented as *string or null*.
  Both nullable fields are returned as an explicit `null` and are described that
  way in the tool description, so nothing is rewritten or invented. GitHub also
  documents that blank fields are included as `null` rather than omitted.
- The singular `assignee` field is not selected: GitHub's current documented API
  version removes it, and the `assignees` array is only optionally present.
- `GET` an issue documents `200`, `301`, `304`, `404` and `410`: a transferred
  issue answers `301`, an issue the token cannot read answers `404`, and a
  deleted issue answers `410`. Steed follows no redirect, so each of those is
  surfaced as a failed read rather than retried elsewhere.
- The same route documents that a **pull request** can be returned at the selected
  issue number, marked by an optional `pull_request` object. `read_issue` does not
  select that discriminator, so a pull request projects to exactly the same six
  fields as an issue and **the read does not establish the object type**. The tool
  description says so where the read is offered. Treat the result as "issue-or-pull
  request"; if you need to know which, read it again through a route that the
  reviewed profile does not offer yet.
- `state_reason` for an open issue is documented as nullable but not as
  specifically `null`; the profile passes through whatever GitHub sends.
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
   location, for example `~/steed-github-repository.json`.
2. Replace every `REPLACE-WITH-YOUR-GITHUB-OWNER` with the owner that holds the
   repository and every `REPLACE-WITH-YOUR-GITHUB-REPOSITORY` with the repository
   name, for example `my-github-login` and `my-project`. The reviewed source
   review shows the resulting paths before anything is installed. Repeat step 1
   for each repository you want.
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
- [Get an issue](https://docs.github.com/en/rest/issues/issues#get-an-issue)
- [Create an issue](https://docs.github.com/en/rest/issues/issues#create-an-issue)
- [Getting started with the REST API](https://docs.github.com/en/rest/using-the-rest-api/getting-started-with-the-rest-api)
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

### Per-project template (issue reads)

`connectors/recipes/templates/gitlab-project-template.json` is a **template, not a
packaged profile**, for the same reason the GitHub one is: a reviewed issue read
needs the numeric project ID *and* the issue IID, and a reviewed path may carry only
one caller-supplied segment. So the template fixes one project ID in source, exactly
as the GitHub template fixes an owner and repository, and the caller selects the
issue:

| Action | Wire contract |
| --- | --- |
| Read the reviewed project | `GET /api/v4/projects/{pinned id}` |
| Read one issue | `GET /api/v4/projects/{pinned id}/issues/{destination}` by per-project IID |
| Create issue | `POST /api/v4/projects/{pinned id}/issues` with `title` and `description` |

1. Copy the template to your own location, for example `~/steed-gitlab-project.json`.
2. Replace every `REPLACE-WITH-YOUR-GITLAB-PROJECT-ID` with the **numeric** project
   ID. GitLab also accepts a URL-encoded namespace path, but a reviewed path segment
   never contains a slash, so the numeric ID is the only form this subset can use.
   The project's ID appears on its GitLab page, and `GET /api/v4/projects/{id}`
   confirms it.
3. Import the copy with **App Connections → Add Connector Recipe…**, read the source
   review and install it. Repeat per project.

Notes on the issue read:

- The route takes the issue's **per-project IID**, which GitLab documents as an
  integer. Its own example uses `GET /projects/42/issues/5`, and states that the
  global issue ID is not valid there.
- GitLab publishes no field schema for a single issue, so only fields present in the
  documented read and create examples are projected: `iid`, `title`, `state`,
  `description` and `web_url`.
- `description` is documented as possibly null and is returned as an explicit null.
- `weight`, `epic` and `health_status` are documented as present only on Premium or
  Ultimate issues, so they are deliberately not projected: a Free tier response omits
  them and selecting them would make the read fail. The deprecated `assignee` field
  is not projected either, since GitLab documents `assignees` as its replacement.
- A missing issue and an issue the token cannot see both answer `404` in GitLab's
  general troubleshooting table, so the two are not distinguishable and the read fails
  closed either way.
- The documented create response includes `iid` and `web_url`, so a created issue can
  be confirmed by reading that IID afterwards. That confirm read exists and Steed can
  use it, but no reconciliation step runs it automatically: an uncertain create is
  still reported as uncertain for you to check by hand.

Notes on the wire shape:

- Destinations are numeric project IDs. A namespace path cannot be expressed,
  because a reviewed path segment never contains a slash.
- Issue creation sends exactly `title` and `description`. GitLab documents a
  **quick action** as a command that starts its own line in an issue
  description or comment, such as `/assign` or `/confidential`, so a draft that
  begins a line with a slash can change the created issue. The `create_issue`
  tool description repeats that warning where the source review and the task's own
  payload preview are read, and Steed sends the reviewed draft unchanged: no
  command is stripped, escaped or described as plain prose, and nothing here
  claims that GitLab interprets a title. GitLab's processing of the submitted
  text is service behaviour that you confirm on the created issue.
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
- [Retrieve a project issue](https://docs.gitlab.com/api/issues/#retrieve-a-project-issue)
- [Create an issue](https://docs.gitlab.com/api/issues/#create-an-issue)
- [API troubleshooting](https://docs.gitlab.com/api/rest/troubleshooting/)
- [Quick actions](https://docs.gitlab.com/user/project/quick_actions/)



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
| GitLab.com | packaged + template | none outstanding for the shipped subset |
| Microsoft 365 | gated | native OAuth, `@odata.nextLink` paging and writes unverified |
| Notion | gated | required `Notion-Version` header, nullable identity label, POST-read data-source query |
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
| Figma | gated | personal tokens need `X-Figma-Token`; no version header is documented |
| Zotero | gated | account `userID` must be bound into library paths; listing needs Link-header paging |
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

`connectors/fixtures/shared_capabilities.json` groups the missing capabilities
so the runtime owner can size one capability instead of eighteen service
exceptions. Each group records the engine limit it removes, the documented
requirement per service with an official reference, a minimal synthetic fixture
contract a framework-side test could use, and what it unblocks. Each assessed row
in `assessed_candidates.json` lists the group ids it needs, and the tests keep the
two files in agreement. None of these is worked around in a connector.

| Capability group | Services that need it |
| --- | --- |
| Reviewed static request headers | Notion, Nextcloud, Zotero |
| Credential-derived token headers | Figma, Zotero |
| Binding an identity-derived account id into a later read path | Zotero |
| Offset, page-number, keyset, Link-header and nextLink paging | GitHub, GitLab.com, Microsoft 365, Airtable, Raindrop.io, Calendly, Asana, Spotify, Joplin, Zotero, ClickUp |
| Non-Bearer token schemes and credential placement | Readwise / Reader, ClickUp, Trello, Joplin, Nextcloud, Spotify |
| Application-level success flags | Slack, Raindrop.io, Nextcloud |
| Account identity beyond one subject string and one label | Calendly, Readwise / Reader, Spotify, Airtable, Notion, Microsoft 365, Joplin |
| Native OAuth sign-in, scope normalization and refresh | Microsoft 365, Asana, Spotify, Trello |
| Reads that are POST requests with request objects | Dropbox, Linear, Notion |
| Approved local and self-hosted service origins | Nextcloud, Joplin, Zotero |
| Reviewed fixed query inputs | Figma, Microsoft 365, Airtable, Asana, ClickUp, Raindrop.io, Spotify, Calendly, Joplin |
| Reconciliation of uncertain writes | GitLab.com, Todoist, GitHub, Airtable |

Each requirement inside a group declares what it actually blocks, using
`identity`, `single_item_read`, `lists` and `writes`. That separation is what keeps
a family from looking blocked when only its listing is:

- **Reviewed static request headers** block a first read only for Notion and
  Nextcloud, where the header is documented as required on every request. Zotero's
  `Zotero-API-Version` is documented as *recommended*, and v3 is the documented
  default, so it blocks nothing yet.
- **Credential-derived token headers** are a different requirement from a static
  version header and are tracked separately. Figma needs `X-Figma-Token` for a
  personal access token and documents no version header at all; Zotero documents
  `Authorization: Bearer` with an API key, so it needs neither.
- **Binding an identity-derived account id into a later read path** is Zotero's
  real blocker for a first read: every library route starts with
  `/users/<userID>` and no account-scoped alias is documented.
- **Reviewed fixed query inputs** are a separate request-shaping capability, not
  authentication, not a header and not pagination. Only the reviewed cursor exists
  today. For Figma it is what bounds a document read (`depth`, `ids`); for the
  others it shapes a listing (`$select`, `view`, `sort`, `market`, `count` and the
  equivalents).
- **Paging** never blocks identity or a single-item read; it only blocks listing.
  Two cursor protocols are already supported and are explicitly *not* counted:
  Readwise / Reader (`pageCursor`/`nextPageCursor`) and Notion's data-source query
  (`start_cursor`/`next_cursor`). Readwise stays gated on its authentication
  scheme and its identity-less probe, not on paging.

### Notion assessment notes

Notion stays gated, but for narrower and better-documented reasons than a missing
identity route. The synthetic contract is `connectors/fixtures/notion.json`.

- Identity is documented: `GET /v1/users/me` is titled *Retrieve your token's bot
  user* and returns the user associated with the token. `object` and `id` are
  documented as always present, so the subject is stable.
- The documented user object marks `name` as *oneOf string or null*, so a bot with
  no name has no non-empty label. That is a label-contract limitation, distinct
  from an absent identity route, and the fixture carries one document with a name
  and one with `null`.
- `Notion-Version` is documented as required on all REST API requests. Documented
  versions are `2026-03-11`, `2025-09-03`, `2022-06-28`, `2022-02-22`,
  `2021-08-16` and `2021-05-13`. The fixture is bound to `2026-03-11`, and every
  expected response below is version-specific.
- Since `2025-09-03`, databases and data sources are split: `GET /v1/databases/{id}`
  returns a `data_sources` array and its documented response has **no**
  `properties`, while `GET /v1/data_sources/{id}` is the home of the properties
  schema. The older retrieve-a-database page is deprecated for versions up to and
  including `2022-06-28`, so a version-free fixture would point the runtime owner
  at the wrong wire contract. The two id types are not interchangeable.
- Querying a data source is `POST /v1/data_sources/{id}/query`, so Notion listing
  needs POST reads as well as the header.
- No credential appears in the fixture or in any header example; the token is a
  placeholder and lives in the user's keyring.

Official references: [Retrieve your token's bot user](https://developers.notion.com/reference/get-self),
[User object](https://developers.notion.com/reference/user),
[Versioning](https://developers.notion.com/reference/versioning),
[Changes by version](https://developers.notion.com/reference/changes-by-version),
[Retrieve a database](https://developers.notion.com/reference/retrieve-database),
[Retrieve a data source](https://developers.notion.com/reference/retrieve-a-data-source),
[Query a data source](https://developers.notion.com/reference/query-a-data-source),
[Upgrade guide 2025-09-03](https://developers.notion.com/reference/upgrade-guide-2025-09-03).

Reconciliation of uncertain writes also covers the three shipped starters. A
create that fails after dispatch is reported as uncertain today; a confirmed
outcome would need a reviewed probe step rather than a blind retry.

Microsoft 365 and every other service needing new header, paging, authentication
or OAuth support stays explicitly gated until those shared capabilities land.
Google registration remains deferred. A connector is never packaged merely to
make the catalog look larger.

---

## Candidate contracts

`connectors/fixtures/notion.json`, `connectors/fixtures/zotero.json` and
`connectors/fixtures/figma.json` are implementation-ready reference contracts for
the priority gated families. They are **not** profiles: nothing is installed, no
packaged recipe exists for any of them, and no placeholder profile is shipped. Each
contract carries, in the same shape:

- `origin` and an `api_version` that is either pinned to one explicit version or
  recorded as not required, with `blocks_a_read` stating which it is;
- the exact header placement, split into what the transport supports today, what
  would need a credential-derived header, and what must never appear in a path or
  query;
- the identity request with its exact response fields, including the nullable and
  conditionally-absent cases, plus whether identity has to hand a value to a later
  path;
- the smallest useful read, with every projected field tied to a documented
  reference and a stated reason for each field deliberately not projected;
- one realistic failure, with the documented statuses and an explicit
  `body_shape_documented` flag so a test never depends on an undocumented error
  envelope;
- `framework_gap`: the minimum missing behavior, the capabilities it belongs to,
  and what is **not** required for a first read.

Where the documentation is silent, the contract says so instead of guessing. Three
examples: Zotero publishes no formal item schema, so `data.title` is not projected
because a note has no title; Figma publishes no global error body and does not list
401; Notion documents only `code` and `message` on errors, so no envelope fields
are assumed.

What each one needs before a profile could exist:

| Candidate | Blocks a first read | Only blocks later work |
| --- | --- | --- |
| Notion | the required `Notion-Version` header, and a label contract that tolerates the documented null `name` | data-source listing, which is a POST read |
| Zotero | nothing structural: see the pinned-library alternative below | `Link`-header listing; the recommended version header |
| Figma | carrying a personal access token in `X-Figma-Token` | a document read, which needs a fixed `depth` query input |

**Figma: projecting less is not the same as transferring less.** `GET /v1/files/{key}`
documents the `document` node tree as a non-optional field of its response, and
Steed's transport fails any response body over 128 KiB, so a metadata-only
*projection* cannot make that endpoint small: the whole tree crosses the network
first. The contract therefore uses `GET /v1/files/{key}/meta`, the documented
endpoint whose return value contains no tree, and records the `file_metadata:read`
scope it needs. Figma publishes no response-size limit for any file endpoint, so
whether a real file of ordinary size fits the transport bound stays **pending**,
not claimed. Bounding a document read would additionally need a reviewed fixed
`depth` or `ids` input, which the transport cannot send today.

**Zotero: a pinned library id is a real alternative, not a workaround.** Binding
the account's own `userID` into `/users/{userID}/items/{itemKey}` stays the default
goal, because one sign-in should need no user-supplied account numbers. It is an
onboarding improvement rather than a hard blocker, though: a user-owned template
can fix the numeric library id in reviewed source, exactly as the GitHub template
fixes an owner and repository, and a limited read-only subset then works today.
Such a template must disclose, in the reviewed source text itself, that the number
is user-supplied and unchecked, that a mismatch appears only as the service's own
error, and which library access flag the key needs. No such template is shipped
here; it is recorded as an assessed alternative.

Figma's `GET /v1/me` is documented as a Tier 3 endpoint needing the
`current_user:read` scope. Tiers are rate-limit tiers, not plan gates, and whether
a personal access token may call it is not documented; a framework test should
settle that before relying on it.

### What each kind of evidence proves

Candidate contracts separate three kinds of claim, so a test is never mistaken for
a provider:

- **Documentation proves** that an endpoint, field, header, parameter or status
  exists, and what it is documented to mean, including which fields are documented
  as nullable or conditionally absent.
- **Framework tests prove** that Steed parses, reviews, bounds and dispatches a
  documented shape correctly, and that a payload, identity check, refusal or
  uncertain outcome happens instead of a request. A mock or synthetic fixture
  proves Steed's own behaviour only.
- **Only a real account proves** that a provider accepts a credential in the
  placement a profile uses, that a documented nullable field is actually sent, that
  a real response fits the transport bounds, and that an account was consented,
  connected and revoked.

Every candidate contract therefore carries an `empirical_status` block: what the
documentation settles, what is still **pending**, and what a real account would
settle. Pending items stay pending. No test result, and no unverified live
observation, promotes one.

---

## Provider acceptance checklist

This checklist is the same for every profile. Automated results and live
acceptance are recorded separately, and a profile is never described as verified
on the strength of synthetic tests alone.

### Automated (recorded on every change to the profile)

- [ ] The profile parses with the current interpreter and stays within v1/v2.
- [ ] Identity selects the documented subject and label, including a nullable
      label field that is never used as the account label.
- [ ] Every destination is one reviewed ID; path, query and scheme escapes are
      rejected before dispatch.
- [ ] Reads return exactly the documented fields; an omitted optional field fails
      closed rather than being invented.
- [ ] Each create sends exactly the reviewed JSON payload, with a payload
      preview and hash shown before dispatch.
- [ ] Documented failure, quota and revocation statuses are surfaced, and a
      create that fails after dispatch is reported as uncertain and never
      replayed.
- [ ] Synthetic responses live in `connectors/fixtures/`; no credential, real
      profile or external send is committed.

### Live acceptance (user-owned, recorded separately, never automated here)

- [ ] **Account**: service, account type, date, and the operator who ran it.
- [ ] **Consent**: the exact scopes or permissions granted, and the token type
      (personal token, fine-grained token, OAuth) with its expiry.
- [ ] **Connect**: the profile's identity request returned the expected subject
      and label for that real account.
- [ ] **Read**: at least one real read per tool, with the documented fields
      present as reviewed.
- [ ] **Create**: one reviewed create per write tool, with the service's own
      receipt or resulting item recorded, plus how an uncertain outcome was
      checked.
- [ ] **Exclusion check**: no completion, delete, admin or unrelated effect
      happened, and the reviewed payload matches what the service stored.
- [ ] **Revocation**: the token was revoked afterwards and Steed shows the
      account as needing reconnection.

Until every live box is filled in for a service, describe it as a synthetic
wire-shape subset. Microsoft 365 remains a priority follow-up and is gated on
OAuth acceptance, `@odata.nextLink` paging and write verification; its read proof
in `connectors/compatibility/microsoft-read.json` is left unchanged.

### Per-provider worksheet

`connectors/fixtures/acceptance_worksheet.json` holds one row per shipped
provider, with the synthetic expectations the automated checks already produce and
a separate block of **empty** live fields: setup and least permission, identity
result, read result, reviewed-create preview, uncertain-write handling,
disconnect and revoke behaviour, date, API version, and whether the case was
synthetic or live.

Every live field is empty on purpose. Filling one requires a maintainer-approved
live acceptance task, a user-owned token, and agreement on what may be created in a
real account. A synthetic fixture is never promoted to a live result by editing the
file, and no account has been connected and no test object has been sent to create
these rows.
