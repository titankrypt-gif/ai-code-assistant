# Marketplace threat model

This document records the threats the plugin marketplace is designed to resist,
the controls that address them, and the risks that remain. It is a design and
review aid, not a claim that the marketplace is risk-free.

The marketplace is a **distribution surface, not a privilege boundary**. A
catalog entry is untrusted metadata until it has passed source, schema,
integrity, signature, dependency, and authorization checks. Installing an entry
never executes plugin code and never grants a capability.

## Assets

- The integrity of the operator-controlled catalog and its source.
- Publisher identity: the trusted Ed25519 public keys in `PLUGIN_TRUSTED_KEYS`.
- Workspace capabilities, which are explicit grants, never implied by install.
- The workspace lockfile (`PluginLockEntry`): pinned version, source, sha256
  integrity, and dependencies.
- User and workspace data reachable through a plugin's declared capabilities.
- The availability and determinism of the scanner and its API.

## Adversaries and trust boundaries

| Adversary | Capability | Trust boundary |
| --- | --- | --- |
| Malicious or compromised publisher | Publishes a catalog entry or artifact | Catalog content and artifact host are untrusted |
| Network attacker | Controls DNS, a proxy, or a redirect on the catalog/artifact path | Remote fetches must be HTTPS, allowlisted, redirect-free, and bounded |
| Compromised catalog operator | Can change catalog entries | Operator is trusted; limiting that trust is a residual risk |
| Malicious workspace member | Can read marketplace metadata but not mutate installs | Owner-only install/update/rollback and explicit grants |
| Malicious plugin code | Runs only after an explicit in-process load/subscription | Install never loads or executes plugin code |
| Compromised artifact host | Serves different bytes than the catalog describes | Integrity is recorded now; download-time verification is future work |

## Threats and controls

### Supply chain: a catalog entry points at attacker-controlled content

- The catalog is operator-controlled: a local `PLUGIN_CATALOG_FILE`, the
  read-only `PLUGIN_MARKETPLACE_CATALOG`, or an allowlisted
  `PLUGIN_MARKETPLACE_URL`.
- Every entry is validated before use: canonical plugin id/version, HTTPS
  artifact source (or an opaque local identifier), sha256 `integrity`, an
  embedded manifest whose id/version match the entry, and optional detached
  signature metadata.
- Duplicate entries, malformed entries, and catalogs above the size/entry caps
  are rejected as a whole.
- Install resolves `(plugin_id, version)` server-side; a client can never
  supply a manifest, artifact URL, publisher key, or arbitrary version list.

### SSRF: a remote catalog or plugin request reaches internal services

- Remote catalog fetches go only through `app/services/plugin_network.py`.
- The URL must be HTTPS, its host must be on `PLUGIN_NETWORK_ALLOWLIST`, and
  redirects are refused. The request is timeout- and size-bounded.
- Private, loopback, link-local, and reserved IP literals are denied; hostnames
  that resolve to a private address are denied on a best-effort basis.
- An empty allowlist denies every outbound plugin request (fail closed).

### Publisher identity: a forged or unexpected signature is accepted

- `app/services/plugin_signatures.py` verifies Ed25519 signatures over the
  canonical manifest body (compact, sorted JSON excluding `signature`,
  `trust_state`, and `trust_publisher`).
- Forged, malformed, and unknown-publisher signatures are refused under every
  trust policy. `PLUGIN_TRUST_POLICY=required` also refuses unsigned entries.
- `PLUGIN_TRUSTED_KEYS` is validated at startup; invalid key material fails
  startup instead of silently treating every signature as invalid.
- Key material is never returned by the API or CLI.

### Integrity and version pinning: the installed thing changes underneath the user

- Each catalog entry carries a sha256 `integrity` digest; the lockfile records
  the resolved version, source, digest, and declared dependencies.
- Lockfile serialization is canonical and omits volatile timestamps, so changes
  are reviewable and reproducible.
- Updates resolve a catalog target and run atomically; a failed update leaves
  the previous installation intact. Rollback uses the lockfile/catalog history.
- Dependency resolution validates the catalog graph and rejects cycles and
  missing catalogued dependencies before any write.

### Capability isolation: installing a plugin grants access

- Install and enable never create a capability grant. Grants are explicit,
  per-workspace rows and are checked immediately before dispatch.
- A missing grant, unknown plugin, missing workspace, or mismatched workspace
  refuses the handler (fail closed).
- Global events without workspace context are delivered only to enabled
  plugins and are documented as an exception; workspace-scoped capability
  checks require a workspace id.
- Subscribers registered by internal application code (no `plugin_id`) are
  trusted handlers; plugin installs must always subscribe with their plugin id.

### Marketplace reads: catalog data leaks secrets or is treated as trusted

- The discovery API returns advertised metadata only: id, name, version,
  description, author, capabilities, permissions, dependencies, compatibility,
  integrity, source, and optional signature metadata.
- The embedded manifest is validated against `plugins/plugin.schema.json`; the
  catalog is never executed and never used to grant a capability.
- Catalog source URLs with embedded credentials are rejected by the UI, and
  the API never exposes trusted publisher key material.
- Read-only discovery is available to signed-in workspace members; install,
  update, and rollback remain owner-only and audited.

## Residual risks

- **Operator trust.** A compromised operator can add signed entries or trust a
  malicious publisher key. The marketplace cannot protect against a malicious
  root of trust; key custody and rotation are operational controls.
- **Artifact download is not implemented yet.** The catalog integrity digest is
  validated as a sha256 value and recorded, but the artifact is not downloaded.
  Any future download path must re-verify the digest before use, and the
  artifact host remains a supply-chain risk.
- **Best-effort DNS checks.** Private-address detection for hostnames depends on
  resolution. Keep `PLUGIN_NETWORK_ALLOWLIST` narrow and use HTTPS-only.
- **Stale catalog data.** Discovery and install decisions reflect the catalog
  read for that request (or the read-only discovery cache TTL). An operator
  editing a catalog does not retroactively change an installed lock entry.
- **Runtime plugin code.** Installs register metadata only. Once code is loaded
  or subscribed in-process, Python cannot sandbox it; capability checks and
  review remain the boundary.
- **Availability.** A catalog or artifact host can be unavailable; install and
  discovery fail closed rather than falling back to an unrelated source.

## Out of scope

- Compromise of the host, the Python process, the database, or the operator's
  credentials.
- Vulnerabilities in third-party Rust/Python dependencies (covered by
  dependency review and `cargo-deny`/pinning where applicable).
- Denial of service against the catalog or artifact host.
- Malicious maintainers or reviewers who intentionally approve a bad entry.
- Browser compromise or a malicious extension on the operator's machine.
- Marketplace monetization, ratings, and publisher reputation systems.

## Related documents

- [Security model](security.md) — implemented controls and review rows.
- [Plugin guide](plugins.md#marketplace-trust-model-end-to-end) — catalog,
  signature, install, update, and lockfile behavior.
- [Architecture](architecture.md) — runtime structure and module boundaries.
