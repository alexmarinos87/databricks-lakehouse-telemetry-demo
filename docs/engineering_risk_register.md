# Engineering Risk Register

**Evidence reviewed:** 2026-08-23

Source and local Spark evidence can mitigate a repository defect but cannot close Databricks, GitHub settings, notification, ownership, or consumer risks without independent effective-state evidence.

Every risk remains open until its closure rule is met. A source mitigation removes or bounds a repository defect; it does not prove effective Databricks, GitHub, notification, ownership, or consumer behaviour.

The complete residual-risk statements, current evidence paths, external dependencies, and next evidence requirements are governed in [`governance/engineering_risk_register.json`](../governance/engineering_risk_register.json).

## Status model

| Dimension | Value | Meaning |
| --- | --- | --- |
| Source | Source mitigated | Repository code, configuration, tests, or policy address the identified source defect. |
| Source | Source gap open | A repository-owned design or implementation gap remains. |
| Source | Not source-controlled | The relevant control is an external setting; repository automation can only describe or bootstrap it. |
| Runtime | Runtime evidence pending | The source control exists, but effective Databricks or consumer behaviour has not been proved. |
| Runtime | Externally blocked | Required workspace, identity, repository-setting, or notification bootstrap is unavailable or incomplete. |
| Runtime | Not applicable | No external runtime evidence is required for that risk. |

## Current risks

| Risk | Priority | Source status | Runtime status | Title |
| --- | --- | --- | --- | --- |
| R-001 | Critical | Source mitigated | Externally blocked | Authenticated Databricks deployment remains unproven |
| R-002 | High | Source mitigated | Runtime evidence pending | Deterministic resource-name resolution needs workspace proof |
| R-003 | High | Source mitigated | Runtime evidence pending | Lakeflow expectations configuration needs runtime refresh evidence |
| R-004 | High | Source mitigated | Runtime evidence pending | Local Spark evidence does not equal Databricks runtime evidence |
| R-005 | High | Source mitigated | Runtime evidence pending | Warehouse assignment and join semantics need live Delta evidence |
| R-006 | High | Source mitigated | Runtime evidence pending | Attributed-downtime semantics need consumer and runtime validation |
| R-007 | High | Not source-controlled | Externally blocked | Main-branch protection is not active |
| R-008 | Medium | Source mitigated | Runtime evidence pending | Bounded deployment operations still need provider-side evidence |
| R-009 | High | Source mitigated | Runtime evidence pending | Manifest-last publication needs live migration and interruption proof |
| R-010 | High | Source mitigated | Runtime evidence pending | Immutable ingestion replay needs Auto Loader evidence |
| R-011 | High | Source gap open | Runtime evidence pending | Owner-run saved queries have no accepted ownership policy |
| R-012 | High | Source mitigated | Runtime evidence pending | Target isolation needs rendered-plan and workspace proof |
| R-013 | High | Source mitigated | Runtime evidence pending | Quality gates need live failure-persistence evidence |
| R-014 | High | Source mitigated | Runtime evidence pending | Forecast readiness and vintages need client-facing runtime proof |
| R-015 | High | Source mitigated | Externally blocked | Deployment and runtime identity separation needs effective-permission proof |
| R-016 | High | Source mitigated | Externally blocked | Operational alerts and retention are defined but inactive |
| R-017 | Medium | Source mitigated | Runtime evidence pending | Runtime compatibility baseline needs Databricks execution proof |

## External blockers

- **R-001** — `issue:44`, `databricks:workload-identity-federation`.
- **R-007** — `issue:44`, `github:repository-settings-access`.
- **R-015** — `issue:44`, `databricks:service-principal-bootstrap`.
- **R-016** — `databricks:workspace-sql-warehouse`, `external:notification-destination`.

## Closure rule

A risk may be closed only through one durable review record that includes:

1. the accepted source change or the verified external setting;
2. reproducible local and, where applicable, effective runtime evidence;
3. residual-risk and rollback implications;
4. the named human reviewer who accepted closure;
5. an update to both the JSON source and this rendered document.

Agent confidence, a green source-only test, a stale pull-request reference, or an intended-but-unapplied setting is not closure.
