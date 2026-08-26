# Change Brief: Harden external-control index publication

## Review finding

The first external-control index candidate validated and wrote each report safely,
but two output-boundary gaps remained:

1. the output directory could be placed inside the evidence input root, allowing
   index files to contaminate the protected report namespace;
2. JSON and Markdown were individually atomic, but a late second-file failure
   could leave a public directory containing only the JSON index.

Neither issue changed external state, but both weakened the integrity of the local
review package.

## Repair

External-control index publication now uses a directory transaction.

After the three protected report paths have been resolved, the evidence root is
retained only as an in-process path boundary. `write_outputs` then:

1. rejects an output path equal to or beneath that evidence root;
2. rejects symbolic-link, existing or malformed output paths;
3. creates a private sibling directory named `.<output>.staging`;
4. writes the JSON and Markdown files inside staging;
5. requires the JSON file before final publication;
6. atomically renames the complete staging directory to the requested output path.

Any file-write or final-rename failure removes the staging directory. An existing
output directory is never overwritten, so a prior accepted index remains unchanged.

The evidence-root value is not written into either output. The transaction changes
only local filesystem publication and does not authorize or invoke an external
operation.

## Compatibility

The transactional behavior is activated only for the external-control index output
prefixes. Existing alert and controlled-runtime evidence package behavior is
unchanged and remains covered by the complete repository suite.

The index command and output names remain:

```bash
python3 scripts/build_external_control_evidence_index.py \
  --metadata .bootstrap/evidence/dev/external-control-index-metadata.json \
  --evidence-root .bootstrap/evidence/external-controls \
  --output-dir .bootstrap/evidence/dev/external-control-index
```

```text
external-control-evidence-index.json
external-control-evidence-index.md
```

## Validation

Focused regression tests cover:

- successful publication of both files as one directory;
- rejection of output beneath the protected evidence root;
- refusal to overwrite an existing index package;
- forced Markdown-write failure after JSON creation with no public or staging
  residue;
- preservation of the specific symbolic-link failure category.

## Authority boundary

A complete local index remains review evidence only. Transactional publication does
not prove provider collection, change GitHub or Databricks settings, authorize a
plan or apply, execute a workload, mutate data, deliver an alert, run retention or
contact production.
