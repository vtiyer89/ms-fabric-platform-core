"""The metadata-reader cell that every workload notebook embeds.

This file is the SINGLE SOURCE. `render_metadata_reader.py` writes it into each consuming
notebook between generated markers; `tests/test_metadata_reader_sync.py` asserts every embedded
copy is byte-identical to this one. Hand-editing a copy inside a notebook will fail that test.

Why generated rather than a library: Fabric notebooks have no import path to platform-core at
run time. The alternative to generation is four hand-maintained copies with no compiler, which
is how this drifts (NEXT-SESSION-ACTION-PLAN.md, Track 5).

The reader deliberately knows nothing about which environment it is in. Each environment has
its own metadata lakehouse, so a logical name resolves to exactly one row; a second row means
two environments were seeded into one table, which `_one_row` rejects rather than guessing.
"""

# The reader body is delimited with ''' because it contains its own """ docstrings.
READER_SOURCE = '''
# --- BEGIN GENERATED metadata reader -- edit scripts/metadata_reader_source.py, not this ---
# Resolves Fabric GUIDs by LOGICAL NAME from the md_* tables, at run time.
#
# md_workspace and md_item reach this notebook as OneLake shortcuts in its own attached
# lakehouse, so the two-part names below resolve against that attachment and this cell contains
# no GUID and no workspace display name. The shortcuts are declared in the lakehouse's
# shortcuts.metadata.json and their targets are rewritten per environment at deploy time --
# see ms-fabric-platform-core/docs/metadata-driven-resolution.md.

ONELAKE = "onelake.dfs.fabric.microsoft.com"


def _one_row(rows, what):
    """Exactly one row, or fail with the reason. Never silently takes the first.

    Zero rows means the seeder has not run since this logical name was added, or the name is
    misspelled. More than one means two environments were seeded into the same table, and
    picking either would resolve to the wrong environment's GUIDs.
    """
    if not rows:
        raise LookupError(
            f"{what} is not in this environment's metadata table. Either nb_seed_metadata has "
            f"not run since it was added to metadata/environments/<env>.yml, or the logical "
            f"name is misspelled. Nothing has been read."
        )
    if len(rows) > 1:
        raise LookupError(
            f"{what} resolves to {len(rows)} rows ({[r['environment'] for r in rows]}). This "
            f"lakehouse's metadata tables should hold exactly one environment."
        )
    return rows[0]


def md_workspace_id(logical_name):
    """Workspace GUID for a logical workspace name, e.g. 'bronze'."""
    rows = (
        spark.sql("SELECT workspace_id, environment FROM dbo.md_workspace WHERE logical_name = :n",
                  args={"n": logical_name})
        .collect()
    )
    return _one_row(rows, f"workspace '{logical_name}'")["workspace_id"]


def md_item(logical_name):
    """(workspace_id, item_id) for a logical item name, e.g. 'lh_bronze'."""
    rows = (
        spark.sql(
            "SELECT item_id, workspace_logical_name, environment FROM dbo.md_item "
            "WHERE logical_name = :n",
            args={"n": logical_name},
        )
        .collect()
    )
    row = _one_row(rows, f"item '{logical_name}'")
    return md_workspace_id(row["workspace_logical_name"]), row["item_id"]


def md_table_path(item_logical_name, table_name, schema="dbo"):
    """abfss:// path to a Delta table in a lakehouse named by logical name.

    This is the call that replaces a hardcoded cross-workspace GUID pair. Both halves come from
    the metadata table, so a workspace recreated under the same display name self-heals on the
    next seed run with no deploy.
    """
    workspace_id, item_id = md_item(item_logical_name)
    return f"abfss://{workspace_id}@{ONELAKE}/{item_id}/Tables/{schema}/{table_name}"


def md_config(config_key):
    """A business-config value the data team can change without a deploy."""
    rows = (
        spark.sql("SELECT config_value, environment FROM dbo.md_config WHERE config_key = :k",
                  args={"k": config_key})
        .collect()
    )
    return _one_row(rows, f"config key '{config_key}'")["config_value"]

# --- END GENERATED metadata reader ---
'''.strip()

BEGIN_MARKER = '# --- BEGIN GENERATED metadata reader'
END_MARKER = '# --- END GENERATED metadata reader ---'
