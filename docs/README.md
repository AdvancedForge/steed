# Steed documentation

How to install, use and configure Steed, how agents work in it, and how it is
built. The [README](../README.md) is the short overview; the
[roadmap](../ROADMAP.md) says where things stand and what is next.

## [Installing Steed](install.md)

Installation, the desktop launcher, graphics and video codecs, Google sign-in and updating.

[Launch without a terminal](install.md#launch-without-a-terminal), [Graphics troubleshooting](install.md#graphics-troubleshooting), [Video codecs (H.264 and AAC) — optional](install.md#video-codecs-h264-and-aac--optional), [Engine & features (`steed:features`)](install.md#engine--features-steedfeatures), [Knowing about updates](install.md#knowing-about-updates), [Upgrading Qt WebEngine](install.md#upgrading-qt-webengine), [Google sign-in](install.md#google-sign-in).

## [Using Steed](using-steed.md)

Everyday browsing: the welcome tour, New Tab and bookmarks, tabs, profiles, passwords, cookies, downloads and the rest.

[Welcome, New Tab and bookmarks](using-steed.md#welcome-new-tab-and-bookmarks), [Usage](using-steed.md#usage), [Profiles and stored data](using-steed.md#profiles-and-stored-data), [Tabs and groups](using-steed.md#tabs-and-groups), [Cookies and global sharing](using-steed.md#cookies-and-global-sharing), [Downloads and ad blocking](using-steed.md#downloads-and-ad-blocking).

## [Configuration](configuration.md)

Settings, `config.json` keys and where Steed keeps its data.

[Settings workspace](configuration.md#settings-workspace), [Internal pages and shared styling](configuration.md#internal-pages-and-shared-styling), [Configuration Options](configuration.md#configuration-options).

## [Agents in Steed](agents.md)

Any assistant you like: a model Steed runs itself, Codex, Claude Code, ACP agents, or any MCP client.

[Any model, nothing else to install](agents.md#any-model-nothing-else-to-install), [Staying in charge](agents.md#staying-in-charge), [Daily browser access and permission diagnostics](agents.md#daily-browser-access-and-permission-diagnostics), [Unsafe automatic agent approval (YOLO mode)](agents.md#unsafe-automatic-agent-approval-yolo-mode), [External-agent conversation sidebar](agents.md#external-agent-conversation-sidebar), [HTTP connection](agents.md#http-connection), [Shared tools and permissions](agents.md#shared-tools-and-permissions), [Watching and taking control](agents.md#watching-and-taking-control), [Continuing previous work](agents.md#continuing-previous-work), [Checking an archived app receipt](agents.md#checking-an-archived-app-receipt), [Results and connection limits](agents.md#results-and-connection-limits).

## [Agent task services](task-services.md)

How agent tasks, permissions, approvals, files, workers and app connections work inside Steed.

[Remembering agent approvals](task-services.md#remembering-agent-approvals), [Internal command API](task-services.md#internal-command-api), [Read-only page inspection](task-services.md#read-only-page-inspection), [Target-based link navigation](task-services.md#target-based-link-navigation), [Controlled edits and scrolling](task-services.md#controlled-edits-and-scrolling), [Explicit form submission](task-services.md#explicit-form-submission), [Bounded state waits](task-services.md#bounded-state-waits), [Scoped task downloads](task-services.md#scoped-task-downloads), [Retained task artifacts](task-services.md#retained-task-artifacts), [Persistent agent file library](task-services.md#persistent-agent-file-library), [Generated files and custom integration packages](task-services.md#generated-files-and-custom-integration-packages), [Running an agent-built local worker](task-services.md#running-an-agent-built-local-worker), [Worker dependency decision](task-services.md#worker-dependency-decision), [Agent-designed connector recipes](task-services.md#agent-designed-connector-recipes), [Agent-designed write boundary](task-services.md#agent-designed-write-boundary), [Reviewed API-version headers](task-services.md#reviewed-api-version-headers), [Reviewed identity-label alternatives](task-services.md#reviewed-identity-label-alternatives), [Sequential connector request plans](task-services.md#sequential-connector-request-plans), [Complex provider workflow decision](task-services.md#complex-provider-workflow-decision), [Shared Google registration decision](task-services.md#shared-google-registration-decision), [Browser-managed application operations](task-services.md#browser-managed-application-operations), [Clearing saved app results](task-services.md#clearing-saved-app-results), [Google app connections](task-services.md#google-app-connections), [Reviewed and direct document edits](task-services.md#reviewed-and-direct-document-edits), [Scoped task uploads](task-services.md#scoped-task-uploads), [Existing signed-in task profiles](task-services.md#existing-signed-in-task-profiles), [Grant enforcement and persistence](task-services.md#grant-enforcement-and-persistence), [Active-content networking audit](task-services.md#active-content-networking-audit), [Native networking prototype](task-services.md#native-networking-prototype), [Active-script engine decision](task-services.md#active-script-engine-decision), [Bounded engine feasibility assessment](task-services.md#bounded-engine-feasibility-assessment).

## [Architecture](architecture.md)

How the code is laid out, engine and storage internals, current limitations and the planned architecture.

[Project Structure](architecture.md#project-structure), [Engine and storage internals](architecture.md#engine-and-storage-internals), [Current limitations](architecture.md#current-limitations), [Planned architecture](architecture.md#planned-architecture).

## [Development](development.md)

Checks to run, the verification record and references.

[Development checks](development.md#development-checks), [Verification record](development.md#verification-record), [References](development.md#references).

## Elsewhere

- [Release plan](RELEASE_PLAN.md): the road to a public release.
- [Screenshots](screenshots/).
- [History](history/): what each round changed ([progress log](history/progress-log.md))
  and hand-off notes from earlier work, kept for reference.
