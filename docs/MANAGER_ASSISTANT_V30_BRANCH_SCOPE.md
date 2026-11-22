# Manager Assistant V30 — Multi-Branch Scope

This update changes Manager Assistant branch access from a single fixed branch to a professional branch-scope model.

## Branch access modes

Each Assistant has:

- **All Manager Branches** — dynamic access to every active branch currently managed by the Manager. If the Manager gains a branch, the Assistant gains it automatically. If the Manager loses a branch, access disappears automatically.
- **Selected Branches** — access only to branches explicitly selected by the Manager. The selection is always intersected with the Manager's current branches, so an Assistant can never exceed the Manager's authority.

`ManagerAssistant.pharmacy` remains the default/initial branch for compatibility and first-login selection. It is not the authorization source.

## Branch switching

Assistants with more than one authorized branch now receive the same branch switcher pattern as Managers. Every switch is server-validated against the Assistant's effective branch scope.

## Feature permissions

Branch scope and feature permissions remain separate. An Assistant may have access to several branches but still be limited to selected modules such as Sales, Inventory, Customers, Chat, Attendance, Presence, Reports, etc.

## Security rules

- Assistant cannot add another Assistant.
- Assistant cannot change subscription, branch ownership, manager, or their own permissions.
- Direct URLs/API requests are checked server-side.
- Selected branches are restricted to the Manager's current branches.
- All-branch access is dynamic rather than a copied list.
- Deactivated Assistants immediately lose branch access.

## Chat

The Manager's All Branches thread is available to Assistants only when their scope is **All Manager Branches**. Selected-scope Assistants can use branch chat/direct employee conversations only for branches in their effective scope; this avoids exposing conversations from branches outside their authorization.

## Subscription seats

Active Assistant seats remain subscription-controlled. Deactivated Assistant accounts remain in the database and audit history but do not consume an active seat.

## Audit

Creation, permission changes, branch-scope changes, activation, and deactivation are recorded in `ManagerAssistantAudit`.

## Production migration

Apply migrations normally:

```text
python manage.py migrate
```

No database deletion, flush, fake migration, or migration-folder reset is required.
