# Change Brief: Verify effective Databricks federation after bootstrap

## Problem

The repository can create or verify deployment and runtime federation policies,
but the bootstrap commands are write-oriented and do not independently prove
the complete effective identity state afterwards.

A successful policy-create response does not prove that:

- a numeric service-principal ID resolves to the intended application ID;
- the principal is active;
- the deployment and runtime roles remain distinct across all environments;
- the principal has no account-admin role;
- every required GitHub environment subject has exactly one matching policy;
- no additional federation policy broadens the principal's trust boundary;
- no legacy OAuth client secret remains attached to the principal.

This evidence gap blocks a defensible transition from bootstrap to an
authenticated plan-only development run.

## Outcome

Add one independent, read-only verifier that:

1. parses `.bootstrap/databricks-federation.json` and
   `.bootstrap/runtime-identity.json` separately;
2. fails when their repository, account, audience or deployment identities
   disagree;
3. proves deployment and runtime identities are globally disjoint;
4. reads every referenced account service principal;
5. verifies numeric ID, application ID, active state and absence of the
   account-admin role;
6. enumerates all federation policies with bounded pagination;
7. requires exactly one policy per configured role and GitHub environment;
8. rejects missing, conflicting, duplicate or unexpected policies;
9. enumerates service-principal OAuth secrets and requires zero results;
10. writes sanitized JSON and Markdown evidence.

## Interfaces

### Inputs

```text
.bootstrap/databricks-federation.json
.bootstrap/runtime-identity.json
an already authenticated account-admin Databricks CLI profile
```

No token, client secret or account identifier is accepted as a command-line
argument.

### Commands used

```text
databricks account service-principals get
databricks account service-principal-federation-policy list
databricks account service-principal-secrets list
```

Only read operations are allowed. Every subprocess has a finite deadline and
every paginated inventory has a finite page limit.

### Outputs

```text
databricks-federation-verification.json
databricks-federation-verification.md
```

The evidence records fingerprints, counts, booleans, configured roles and
environments, and stable drift categories. It excludes raw account hosts,
account IDs, application IDs, numeric service-principal IDs, policy IDs, secret
IDs, credential values and provider diagnostics.

## Acceptance criteria

The verifier returns success only when:

- both ignored configs describe the same repository, account and audience;
- every deployment application ID agrees across the two configs;
- deployment and runtime numeric/application identities do not overlap;
- every referenced principal is active and resolves to the expected IDs;
- no referenced principal has the account-admin role;
- every required exact-subject policy exists once with the GitHub Actions issuer,
  configured audience and default `sub` claim;
- no unconfigured federation policy exists on a referenced principal;
- no OAuth client secret exists on a referenced principal.

Any provider read failure or malformed/truncated inventory fails closed with a
sanitized category.

## Compatibility

The verifier does not change the bootstrap configuration format. It supports the
intentional reuse of one runtime principal across `dev-plan` and `dev`, or across
`prod-plan` and `prod`, while preventing any principal from crossing the global
deployment/runtime boundary.

The verifier treats referenced principals as dedicated to the configured
repository subjects. An intentionally shared principal with unrelated
federation policies must be redesigned or explicitly separated rather than
silently passing this control.

## Failure and recovery

A non-zero result is evidence of incomplete or drifting external state. Repair
the specific account configuration and rerun the verifier. Do not weaken the
expected issuer, audience, subject, identity separation or secretless boundary
to make verification pass.

The command is read-only. Recovery from a verifier failure requires no rollback.
Source rollback is a normal revert of the squash commit.

## Non-goals

This increment does not:

- create, update or delete service principals;
- create, update or delete federation policies;
- create or delete OAuth secrets;
- grant workspace, Unity Catalog, job, pipeline or SQL permissions;
- prove workspace assignment or Service Principal User relationships;
- exchange a GitHub OIDC token;
- validate or plan a Databricks bundle;
- deploy or execute the lakehouse;
- activate a schedule or touch production data.

Workspace least-privilege successes and expected denials remain separate live
evidence in issue #44.

## Validation

Repository validation must prove:

- strict cross-config parsing and identity separation;
- exact-policy success, missing, mismatch, duplication and unexpected-policy
  behaviour;
- inactive, account-admin and secret-bearing principal rejection;
- numeric/application mapping drift detection;
- bounded read-only CLI commands and pagination;
- timeout and repeated-token sanitization;
- symbolic-link output rejection;
- evidence non-disclosure.

No Spark Runtime run is required because this change does not modify Spark
transformations, notebooks, Spark dependencies or runtime fixtures.
