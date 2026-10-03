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
