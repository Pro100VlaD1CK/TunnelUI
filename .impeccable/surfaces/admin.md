# TunnelUI administration
Mode: Operate. Targets: frontend/src/app, frontend/src/features.

Init: product truth captured from the explicit brief; code-first confirmed by user.
Shape: entry is Входящие, then Клиенты; desktop sidebar/mobile drawer; primary operation
is adding/editing a client. No dashboard. Client CRUD in this iteration creates global
records only; attaching/applying to endpoints remains explicitly unavailable in UI.
Craft: implement the pinned Ant Design admin direction from DESIGN.md. No visual
concept tournament: user already pinned density, library, navigation and reference.
States: auth expired, loading, empty, validation, API error/retry, pending, success.
Data: server-paginated clients, long Cyrillic names/comments, disabled records, zero inbounds.
Proof: real API persistence, session logout, desktop/mobile theme captures and tests.
Boundary: no simulated host state and no production actions.
