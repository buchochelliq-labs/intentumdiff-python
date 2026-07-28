# Schema-aware JSON/YAML diff profiles

Config and keyed-data documents are not anonymous objects and arrays: schema-aware profiles
tell the matcher which fields are identities, which arrays are keyed collections, and which
paths matter.

## Built-in behavior

- JSON `pair` nodes match by ancestor key path + pair key; array item objects match by stable
  `id`/`name`/`key`-style labels.
- YAML mapping/flow pairs match by key path; labelled sequence items match by recovered
  identity labels.
- ADF and Databricks(-workflow) entities (activities, tasks, clusters, parameters, libraries,
  dependencies) match by normalized identity.

## Runtime schema resolution

- JSON: a top-level `$schema` URL resolves; YAML: the `# yaml-language-server: $schema=...`
  modeline.
- Known provider schemas resolve for dbt and Databricks bundle files; remote schemas fetch
  over HTTPS into a local cache (stale cache used on refresh failure).
- **User-registerable profiles**: register your own schema descriptor + local JSON Schema to
  get identity-aware diffs for your own document dialects (see the config reference in
  [USAGE.md](USAGE.md)).
