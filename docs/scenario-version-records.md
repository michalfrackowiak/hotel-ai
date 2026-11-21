# Versioned booking scenario records

The reception app stores one scenario per Polish or English chat or voice scope. Each language can also have a shared template. A channel draft records the exact sealed shared version it inherits from in `base_version`. Leaving the field empty makes the draft independent. Shared templates have no parent.

A scenario can retain several versions, each with numbered identity, ordered steps and one destination per event from each source step. The graph belongs to one version. A saved step stays in its original version. Replace a transition to change its endpoints. The future publisher must resolve the shared defaults into a complete graph before sealing a version so changing a shared template never changes an active booking flow. Step configuration currently stores a JSON object. The configuration schema and allowed booking actions will come with graph validation.

`published_at` identifies previously sealed versions. The scenario's `published_version` points to the current selection. A future rollback can select a prior sealed version while keeping the retained versions and their graph records available to active conversations. Instance write guards reject attempts to change the selection, seal a version directly or modify a sealed graph. Publication needs a transaction that owns graph mutations, validates ownership and every path, and prevents queryset or concurrent draft writes from changing a sealed graph. These guards do not cover bulk writes or raw SQL. Publish, rollback, audit and conversation pinning remain follow-up work.

The migration only adds empty tables. The existing scenario editor and guest flows continue to use the existing prompts.
