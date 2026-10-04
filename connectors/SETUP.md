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
- **No expiry is documented for a Todoist personal token**, so treat it as
  long-lived and revoke it in the same settings screen. OAuth access tokens, by
  contrast, are documented to expire an hour after issue, and an invalid or expired
  token answers `401` with the documented `UNAUTHORIZED` error code.

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
- Set a short expiry on the fine-grained token: the documented `expires_in` value is
  between 1 and 366 days, `none` for a non-expiring token, and 30 days by default or
  less under an organization lifetime policy.
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

## Asana

`connectors/recipes/asana.json`, origin `https://app.asana.com`, bearer
authentication with a personal access token. Read-only.

| Action | Wire contract |
| --- | --- |
| Identity | `GET /api/1.0/users/me`, subject `/data/gid`, label `/data/name` |
| Read one project | `GET /api/1.0/projects/{destination}` by numeric project GID |
| Read one task | `GET /api/1.0/tasks/{destination}` by numeric task GID |

Notes on the wire shape:

- A personal access token is **documented as used like an OAuth access token**,
  passed in the `Authorization` header, so there is no second token header. The
  token then carries whatever access its creator has, which is why the profile only
  reads.
- Responses wrap the object in a top-level `data` object, so identity and every read
  project through `/data/...`. The label is `name`, never `email`, because `email`
  is opt-in through field selection and is often absent.
- GIDs are the numbers in Asana URLs, for example `1331` in
  `https://app.asana.com/0/12345/1331/list`. Each tool description says where to
  find the number it needs.
- `due_on`, `start_on` and `owner` on a project, and `due_on`, `start_on`,
  `assignee`, `completed_at` and `parent` on a task, are documented as possibly
  null and are returned as explicit nulls.
- **Asana publishes no default field list.** The documented signal is the per-field
  `[Opt In]` marker, and a field without it is returned by default. Every projected
  field here is unmarked: `gid`, `resource_type`, `name`, `notes`, `archived`,
  `completed`, `due_on`, `start_on`, `owner`, `assignee`, `completed_at` and
  `permalink_url`. The opt-in fields are deliberately **not** projected: `created_by`,
  `dependencies`, `dependents`, `external`, `html_notes`, `num_subtasks`,
  `current_status.html_text`, `project_brief` and `created_from_template`. `notes` is
  not opt-in, so it is projected; `html_notes` is, so it is not.
- The only field Asana documents as always returned is the `gid` of an included
  object. `permalink_url` has no opt-in marker, so it is expected by default, but
  that is an expectation rather than a documented guarantee.
- Errors are HTTP status codes plus a JSON `errors` array, so there is no
  application-level flag that could mask a failure.
- Collections are **not** included: listing pages with `limit` and `offset` and
  returns a `next_page` offset, which is an offset protocol rather than the reviewed
  cursor.
- Task creation is **not** included: Asana documents creating a task by naming a
  workspace or project in the request body, and no reviewed path can carry that
  destination. Guessing one from a draft would be wrong, so nothing claims it.
- Rate limits are documented as 150 requests per minute on the Free plan and 1,500
  per minute on paid plans, allocated per token, with `Retry-After` on a 429.

### Least-permission setup

1. In Asana open your account settings and create a **personal access token**.
2. Paste it into Steed's Connect step for that account and select only the read
   actions you want to expose. The token is stored in the desktop keyring.
3. Revoke it from the same Asana page at any time; disconnecting in Steed removes
   the stored credential.

Because a personal access token has exactly the access of the user who created it,
the narrowest reduction available to a user is a dedicated Asana account with access
to only the projects or tasks you intend to read. Asana documents personal access
tokens as **persistent by default**, with no documented expiry duration, so treat one
as long-lived: revoke it from **My Apps** when it is no longer needed, and review it
regularly the way Asana's own guidance says.

Official references:

- [Personal access tokens](https://developers.asana.com/docs/personal-access-token)
- [Get user](https://developers.asana.com/reference/getuser)
- [Get project](https://developers.asana.com/reference/getproject)
- [Get task](https://developers.asana.com/reference/gettask)
- [Errors](https://developers.asana.com/docs/errors)
- [Pagination](https://developers.asana.com/docs/pagination)
- [Rate limits](https://developers.asana.com/docs/rate-limits)

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
- A GitLab token expires at **midnight UTC** on its expiry date. With no date set
  the expiry is 365 days out and cannot exceed 365 days by default; an expired
  token stops working and cannot be reactivated, so replace it rather than reusing
  it.

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
| Microsoft 365 | packaged (read-only, supplied delegated token) | native OAuth, `@odata.nextLink` paging and writes remain deferred |
| Notion | gated (first read expressible) | data-source listing is a POST read; a real integration token is still unverified |
| Slack | gated | channel destinations and `ok:false` error checking |
| Asana | packaged (read-only) | none outstanding; collections and task creation are excluded by design |
| Dropbox | gated | reads are RPC-style POST request objects |
| Linear | gated | reads are GraphQL POSTs with variables |
| Raindrop.io | packaged (read-only) | none outstanding; the `result` flag stays unchecked, which matters for writes |
| Airtable | gated | base/table pinning, optional identity label, offset paging |
| Calendly | gated | identity is a URI; next page is a full URL |
| Readwise / Reader | gated | `Authorization: Token`; auth probe returns 204 with no identity |
| ClickUp | gated | bare personal token header; non-expiring OAuth tokens |
| Trello | gated | key+token credentials, or OAuth 2.0 PKCE acceptance |
| Figma | gated | personal tokens need `X-Figma-Token`; no version header is documented |
| Zotero | template (read-only) | one sign-in without a pinned library ID needs identity-derived path binding; listing needs Link-header paging |
| Nextcloud | gated | self-hosted host with port, WebDAV/XML, mandatory OCS header |
| Joplin | gated | local plaintext origin, query-parameter token, no identity |
| Spotify | gated | OAuth-only access, nullable label, offset paging |

### Engine constraints every gate is measured against

- A profile origin is one exact public HTTPS domain: no port, no path, no
  credentials. That alone rules out self-hosted and local services.
- The transport supplies `Authorization`, `Accept` and `Content-Type`, plus a
  reviewed operation-ID header when applicable. Recipe v5/v6 also supports four
  reviewed non-secret static headers; credential-derived headers remain gated.
- Identity is one GET returning HTTP 200 JSON with a validated subject and a
  non-empty label. Recipe v6 supports ordered reviewed label alternatives. URI
  subjects and an empty `204` probe remain unsupported.
- Reads are GET only, with optional bounded cursor pagination through one
  reviewed query parameter. Offset, page-number, keyset and `Link`-header paging
  are not followed.
- A write is a single POST with no automatic retry, and an uncertain write is
  reported as uncertain because nothing in this subset can reconcile it.

### POST-read cases prepared for the next shared contract

The next maintainer runtime contract is a typed read-only POST with bounded
reviewed inputs. Three families are ready to promote the moment it is published,
and their cases are already written against official sources in
`connectors/fixtures/executable_cases.json` under `gated_post_read`:

| Family | Read | Why it cannot be expressed today |
| --- | --- | --- |
| Notion | `POST /v1/data_sources/{id}/query` | reads are GET-only; a write is a single POST that requires an artifact |
| Linear | `POST /graphql` | same, plus an `errors` array that can accompany HTTP 200 |
| Dropbox | `POST /2/users/get_current_account` and `POST /2/files/list_folder` | identity is a POST too, so nothing in the family is expressible |

Each case records the official request body fields, the documented response shape,
the documented failure statuses, the documented limits and prohibitions, and the
smallest missing behaviour. A few are worth knowing before anyone implements them:

- **Notion** requires the `Notion-Version` header, which is now supported, and its
  body is entirely optional; the documented prohibitions include not parsing or
  storing cursors beyond passing them back as `start_cursor`, not sending
  `in_trash`, and not assuming a sort order when none is given. A query is capped at
  10,000 results, and a response may contain fewer results than `page_size`.
- **Linear** documents that a query can **partially succeed with HTTP 200**, so a
  status-only check would report partial data as success. Rate limiting is documented
  as HTTP 400 with a `RATELIMITED` code inside the errors array, and a single query
  is capped at 10,000 complexity points.
- **Dropbox** documents `account_id` as the account key rather than email, and
  `email` as unreliable without `email_verified`. Listing is capped at 2,000 entries,
  and duplicate concurrent list calls can return a rate-limit error.

These cases stay gated on purpose. A read is never disguised as a create, arbitrary
queries are not permitted, and no origin or header rule is bypassed. When the shared
contract lands, the cases promote through real discovery, parser, source-review,
adapter and transport tests rather than being hand-waved into profiles.

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

### Notion is now a first-read candidate

Main's reviewed static headers and reviewed identity label alternatives clear the two
things that previously gated Notion, so its queue entry keeps only `post_reads`: a
data-source listing is `POST /v1/data_sources/{id}/query` with a request object. The
required `Notion-Version` header and the documented null `name` are both handled by
capabilities that exist today, which is why neither appears as a gate any more. A
Notion profile is still not shipped here: it belongs with the runtime owner's
acceptance of a real integration token.

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
  expected response below is version-specific. Main now accepts this header as a
  reviewed static header, so the requirement is expressible.
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

Microsoft 365 now offers a supplied-token, read-only subset. Native OAuth,
nextLink paging, writes and other providers' unsupported capabilities remain
gated until their shared contracts land.
Google registration remains deferred. A connector is never packaged merely to
make the catalog look larger.

---

## Candidate contracts

`connectors/fixtures/notion.json`, `connectors/fixtures/zotero.json` and
`connectors/fixtures/figma.json` are implementation-ready reference contracts for
the priority gated families, and `connectors/fixtures/executable_cases.json` turns
them into comparable cases that run through the real parser and adapter.

### What the executable cases prove, and what they cannot

Each case states its exact method, origin and path, API version, headers split into
static and credential-derived values, identity and read projections, a byte bound
where the response matters, and its expected outcome — plus **which current behaviour
already passes** and the **smallest missing behaviour**. A case may therefore fail
because a shared capability is absent rather than because a recipe is wrong, and
`services/` is never changed to make one pass.

Two cases are decided by what main now carries:

- `notion_static_version_header` **passes**. Main supports reviewed non-secret
  static headers, and `Notion-Version` with a canonical date is one of the four
  accepted names, so the header Notion documents as required can be sent. The case
  asserts the header value that actually went out.
- `notion_null_name_identity` **passes**. Main supports reviewed identity label
  alternatives, so the documented `oneOf string or null` name falls back to another
  reviewed pointer instead of refusing the account, and the fallback is visible in
  the source review.

Two are decided by gates that are still open:

- `figma_credential_token_header` shows the real `RecipeHttp` refusing a
  credential-derived `X-Figma-Token` header before any socket work. A token in a
  service-specific header is a different requirement from a static version header,
  and this is the former.
- `figma_document_read_body_bound` records the arithmetic that makes the read bound:
  the document route documents the `document` tree as a non-optional response field,
  the transport refuses a body over 128 KiB, and no file endpoint documents a size
  limit. It is marked `not_executable_locally` because a profile origin is one exact
  public HTTPS domain, so the real transport can never be pointed at a loopback
  fixture. `figma_oversized_success_is_a_failure` exists so nobody relaxes that bound
  while adding a design subset: a successful HTTP 200 that exceeds it must stay a
  failure, never a partial read. They are **not** profiles: nothing is installed, no
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

## Raindrop.io

`connectors/recipes/raindrop.json`, origin `https://api.raindrop.io`, bearer
authentication with an access token or test token. Read-only.

| Action | Wire contract |
| --- | --- |
| Identity | `GET /rest/v1/user`, subject `/user/_id`, label `/user/fullName` |
| Read one collection | `GET /rest/v1/collection/{destination}` by numeric collection ID |
| List root collections | `GET /rest/v1/collections`, projecting `/items` |

Notes on the wire shape:

- Every documented v1 route lives under `https://api.raindrop.io/rest/v1`, so the
  prefix is part of each reviewed path rather than part of the origin, which keeps
  the origin rule intact.
- An access token, including a test token, is documented in `Authorization: Bearer`
  with no second token header.
- A collection's identifier is `_id`, not `id`, and a single collection is wrapped in
  an `item` object. The root list is under `items`. There is no `hidden` field on a
  collection; `hidden` exists only on a group inside the user document.
- Raindrop documents the single-collection route in the **singular**,
  `GET /rest/v1/collection/{id}`, while the root list is the plural
  `GET /rest/v1/collections`. There is no documented plural per-collection route, so
  the reviewed path uses the documented singular form.
- The documentation warns that responses can carry fields it does not describe and
  that using them is unsafe, so only documented fields are projected.
- **Success is signalled twice.** No failure body is documented for these two GET
  routes, so nothing here claims one. What *is* documented is that every failure
  example elsewhere uses `{"result": false, "error": ..., "errorMessage": ...}` and
  never includes an `item` or `items` key, which is why a failed read is expected to
  fail closed instead of looking like an empty account. Whether a `result: false`
  document can arrive with HTTP 200 is not documented, so the profile neither
  relies on the status alone nor checks the flag. That is a shared
  application-error-flag gap and the reason no Raindrop write is offered.
- Listing is documented as `page` and `perpage`, so no cursor is declared and only
  the documented single response is read.
- Rate limits are documented as 120 requests per minute per authenticated user for
  OAuth requests. Whether that number also applies to a test token is not documented.
- Creating a bookmark needs an explicit link input from the user, which no reviewed
  destination can carry, so this profile stays read-only.

### Least-permission setup

1. In Raindrop open the App Management Console for your application, or use the test
   token shown in your application settings, and copy the token.
2. Paste it into Steed's Connect step and select only the read actions you want to
   expose. The token is stored in the desktop keyring.
3. Revoke it from the Raindrop app console at any time; disconnecting in Steed
   removes the stored credential.

Official references:

- [Authenticated user](https://developer.raindrop.io/v1/user/authenticated)
- [Collections methods](https://developer.raindrop.io/v1/collections/methods)
- [Making authorized calls](https://developer.raindrop.io/v1/authentication/calls)
- [API overview and status codes](https://developer.raindrop.io/readme)

---

## Microsoft 365 (read-only, you supply the token)

`connectors/recipes/microsoft365-read.json`, origin
`https://graph.microsoft.com`, bearer authentication with a **delegated Microsoft
Graph access token that you obtain yourself**. Read-only.

| Action | Wire contract | Least delegated scope |
| --- | --- | --- |
| Identity | `GET /v1.0/me`, subject `/id`, label `/userPrincipalName` | `User.Read` |
| Read one drive item | `GET /v1.0/me/drive/items/{destination}` | `Files.Read` |
| Calendar, first page | `GET /v1.0/me/events`, projecting `/value` | `Calendars.Read` |
| To Do lists, first page | `GET /v1.0/me/todo/lists`, projecting `/value` | `Tasks.Read` |

**The one honest caveat:** `GET /v1.0/me` requires a delegated token, and Steed has
no Microsoft sign-in. You obtain a delegated access token with your own tooling and
paste it into the Connect step; it is stored in the desktop keyring and never appears
in a recipe, result or log. Native OAuth sign-in, scope normalization and refresh
remain shared framework work, so nothing here claims one-click sign-in.

Notes on the wire shape:

- Every documented request in this profile needs only `Authorization: Bearer`. The
  API version lives in the path as `/v1.0`, and no version or consistency header is
  documented as required for these reads.
- `id` is documented as not nullable and `userPrincipalName` as required when a user
  is created, so `userPrincipalName` is the label. `mail` is returned by default but
  its nullability is not formally documented, so it is never the label.
- Use the Graph driveItem `id` from an item response; a sharing URL or its encoded
  sharing token uses a different Graph route and is not accepted as an item ID.
- A drive item carries **file** or **folder** metadata as mutually exclusive facets
  that are *absent* rather than null, so neither facet is projected; only fields
  present for either kind are selected, and a folder reads exactly like a file.
  `parentReference` is documented as present only when the item has a parent, so it is
  not projected either.
- Collections return `@odata.nextLink` as an absolute URL. This profile **never
  follows it**, so both collection reads return the first page only, and each tool
  description says so where the read is offered.
- Errors are HTTP status codes plus a single top-level `error` object with a
  machine-readable `code`, so there is no application-level flag to mask a failure.
  Throttling answers `429` with `Retry-After`; specific numeric limits for these
  routes are not documented and none is claimed.
- No write is offered: writing needs a delegated scope this profile does not
  request.

### Least-permission setup

1. Obtain a delegated access token for **your own** Microsoft account with only the
   scopes in the table above. Do not use an application/client-credentials token:
   `/me` requires a delegated token.
2. In Steed, install the profile, paste the token into the Connect step and select
   only the reads you want.
3. Revoke or let it expire according to your tooling; disconnecting in Steed removes
   the stored credential immediately.

Official references:

- [Get user](/v1.0/me identity)](https://learn.microsoft.com/en-us/graph/api/user-get?view=graph-rest-1.0)
- [Get driveItem](https://learn.microsoft.com/en-us/graph/api/driveitem-get?view=graph-rest-1.0)
- [List events](https://learn.microsoft.com/en-us/graph/api/user-list-events?view=graph-rest-1.0)
- [List To Do lists](https://learn.microsoft.com/en-us/graph/api/todo-list-lists?view=graph-rest-1.0)
- [Errors](https://learn.microsoft.com/en-us/graph/errors)
- [Throttling](https://learn.microsoft.com/en-us/graph/throttling)

---

## Zotero (one library, imported template)

`connectors/recipes/templates/zotero-library-template.json`, origin
`https://api.zotero.org`, bearer authentication with a Zotero API key. Read-only,
and a **template rather than a packaged profile** for the same reason as GitHub's:
a library read needs the account's numeric library ID in the path, and that number
must come from the user.

| Action | Wire contract |
| --- | --- |
| Identity | `GET /keys/current`, subject `/userID`, label `/username` |
| Read one item | `GET /users/{pinned library id}/items/{destination}` by item key |

1. Copy the template to your own location, for example `~/steed-zotero-library.json`.
2. Replace every `REPLACE-WITH-YOUR-ZOTERO-USER-ID` with the numeric user ID for
   your library. It is shown on the Zotero API Keys page and returned by
   `GET /keys/current` as `userID`.
3. Import the copy with **App Connections → Add Connector Recipe…**, read the source
   review and install it.

What the source review tells you, because it matters here:

- **The library ID is user-supplied and unchecked.** Steed re-checks account
  identity on every call, but it never compares a number written in a path with the
  account's own user ID. If the pinned ID belongs to another library, the request is
  simply sent and Zotero answers on its own terms — an unknown item is documented as
  `404`. The ID is not checked against the connected identity: another accessible
  library may return data, so review the intended library ID before installation.
- The item key is the eight-character key such as `ABCD2345`; the profile projects
  only `key`, `version`, `itemType`, `dateAdded` and `dateModified`.
- **A title is deliberately not projected.** Zotero publishes no item schema, and a
  note has no title at all, so projecting one would break reads for notes and
  attachments instead of describing them.
- The key's `access.user.library` flag decides whether items can be read at all; no
  write permission is needed for this read-only subset.
- `Zotero-API-Version: 3` is documented as *recommended*, and version 3 is the
  documented default, so this subset does not require the header.
- Nothing is created or modified. Collections are excluded because listing uses
  `limit`, `start` and a `Link` header rather than the reviewed cursor.

Official references:

- [Web API v3 basics](https://www.zotero.org/support/dev/web_api/v3/basics)
- [Syncing and key verification](https://www.zotero.org/support/dev/web_api/v3/syncing)

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
wire-shape subset. Microsoft 365's supplied-token read profile is packaged;
native OAuth, `@odata.nextLink` paging and write verification remain priority
follow-ups. Its earlier read proof in
`connectors/compatibility/microsoft-read.json` is left unchanged.

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
