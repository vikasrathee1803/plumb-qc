# ADR-0012: Shared baseline store is a configured path, not a Snowflake write

Date: 2026-06-07. Status: accepted.

The spec's Phase 2 should-have is "shared team baselines stored in a
Snowflake stage or object store". A Snowflake stage write needs PUT, and
the core invariant is read-only everywhere: the engine refuses anything
that is not a read, with a proving test. Writing baselines to a Snowflake
stage would break that invariant.

Decision: the shared baseline store (SharedFileStore) is a configured
filesystem path. That path can be a network share or a mounted object
store (s3fs, Azure Files, rclone mount), which is the object-store option
the spec allows, without any Snowflake write. It uses the same Parquet
plus manifest layout as the local store and adds a shared index.json for
team visibility. It sits behind the BaselineStore Protocol, so callers
(the regression checks, the CLI) are unchanged; make_baseline_store is the
one factory seam where a future native S3 or Azure SDK backend would slot
in.

Configuration: ~/.plumb/baselines.yml ({kind: shared, path: ...}) or the
PLUMB_BASELINE_DIR environment variable.

Reversibility: cheap. The Protocol and factory isolate the backend.
