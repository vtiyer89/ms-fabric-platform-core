"""Create the metadata schema/tables (if missing) and upsert seed rows into the platform SQL database.

Lightweight, POC-scoped seeder for `metadata.SourceSystemConfig` / `metadata.TargetStoreConfig` /
`metadata.SourceObjectConfig` (schema supplied by the data team 2026-09-14/15; scoping decision
in docs/metadata-db-mapping.md). This is deliberately NOT the old metadata framework's seeder: no
live GUID resolution, no owner=ci/ops merge rule. Seed content is version-controlled YAML in
platform/metadata/, applied idempotently on every run via SQL MERGE keyed on each table's natural
key.

fabric-cicd's SQLDatabasePublisher only creates/updates the item shell (SQL_DATABASE is in
fabric_cicd.constants.SHELL_ONLY_PUBLISH) -- it never executes DDL. This script is what actually
creates the schema and tables, by connecting directly to the deployed database and running the
scripts in platform/sql/. Run it after `db_platform_metadata.SqlDatabase` has been deployed.

Auth: same service-principal client-secret as deploy_fabric_item.py (AZURE_CLIENT_ID /
AZURE_CLIENT_SECRET / AZURE_TENANT_ID), used both to call the Fabric REST API (resolve the
database's serverFqdn) and to get an AAD access token for the SQL connection itself.

Usage:
    python scripts/seed_metadata_db.py --workspace-id <platform-workspace-guid> --environment TEST
    python scripts/seed_metadata_db.py --workspace-id <platform-workspace-guid> --environment TEST --dry-run
"""

import argparse
import json
import os
import struct
import sys
from pathlib import Path

import pyodbc
import requests
import yaml
from azure.identity import ClientSecretCredential

FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
SQL_DATABASE_ITEM_TYPE = "SQLDatabase"
SQL_DATABASE_ITEM_NAME = "db_platform_metadata"
# pyodbc's AAD-token attribute -- see Microsoft's documented pattern for connecting to Azure
# SQL / Fabric SQL Database with an azure-identity token instead of a connection-string password.
SQL_COPT_SS_ACCESS_TOKEN = 1256

SQL_DIR = Path(__file__).resolve().parent.parent / "platform" / "sql"
METADATA_DIR = Path(__file__).resolve().parent.parent / "platform" / "metadata"
PLACEHOLDER_MARKER = "PLACEHOLDER-RESOLVE-VIA-FABRIC-API"


def get_database_server_fqdn(workspace_id: str, credential: ClientSecretCredential) -> tuple[str, str]:
    """Resolve the deployed SqlDatabase item's serverFqdn and its own item id (the database name).

    Uses the same attribute the fabric-cicd $items.<Type>.<name>.<attr> binding mechanism reads
    (constants.PROPERTY_PATH_ATTR_MAPPING[SQL_DATABASE]["sqlendpoint"] -> properties.serverFqdn),
    just called directly here since we need it in Python, not inside a parameter.yml rule.
    """
    token = credential.get_token("https://api.fabric.microsoft.com/.default").token
    headers = {"Authorization": f"Bearer {token}"}

    items_resp = requests.get(
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/items",
        headers=headers,
        params={"type": SQL_DATABASE_ITEM_TYPE},
        timeout=30,
    )
    items_resp.raise_for_status()
    items = [i for i in items_resp.json().get("value", []) if i["displayName"] == SQL_DATABASE_ITEM_NAME]
    if not items:
        sys.exit(
            f"[error] No {SQL_DATABASE_ITEM_TYPE} item named '{SQL_DATABASE_ITEM_NAME}' found in "
            f"workspace {workspace_id}. Deploy platform/ first (deploy-platform.yml)."
        )
    item_id = items[0]["id"]

    item_resp = requests.get(
        f"{FABRIC_API_BASE}/workspaces/{workspace_id}/sqlDatabases/{item_id}",
        headers=headers,
        timeout=30,
    )
    item_resp.raise_for_status()
    properties = item_resp.json().get("properties", {})
    server_fqdn = properties.get("serverFqdn")
    database_name = properties.get("databaseName", SQL_DATABASE_ITEM_NAME)
    if not server_fqdn:
        sys.exit(
            f"[error] {SQL_DATABASE_ITEM_NAME} has no properties.serverFqdn yet. The item may "
            f"still be provisioning -- Fabric SQL databases are not always immediately connectable "
            f"after the shell publish returns."
        )
    return server_fqdn, database_name


def connect(server_fqdn: str, database_name: str, credential: ClientSecretCredential) -> pyodbc.Connection:
    """AAD-token auth against the Fabric SQL database, per Microsoft's documented pyodbc pattern."""
    token = credential.get_token("https://database.windows.net/.default").token
    token_bytes = token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

    connection_string = (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server={server_fqdn},1433;"
        f"Database={database_name};"
        "Encrypt=yes;TrustServerCertificate=no;"
    )
    return pyodbc.connect(connection_string, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})


def probe_connection(conn: pyodbc.Connection) -> None:
    """Run a trivial no-op query before any DDL, to isolate connection/identity-resolution
    failures from anything statement-specific. If this fails the same way the real DDL does,
    it proves the failure has nothing to do with CREATE SCHEMA or any particular statement --
    the connection's security context can't resolve at all, for any command.
    """
    print("[probe] running SELECT 1 to isolate identity-resolution failures from DDL content...")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 AS ok;")
    row = cursor.fetchone()
    print(f"[probe] SELECT 1 succeeded (returned {row.ok}) -- connection's security context resolves fine.")


def apply_ddl(conn: pyodbc.Connection, dry_run: bool) -> None:
    for sql_file in sorted(SQL_DIR.glob("*.sql")):
        statements = [s.strip() for s in sql_file.read_text().split("GO") if s.strip()]
        if dry_run:
            print(f"[dry-run] would execute {sql_file.name} ({len(statements)} statement(s))")
            continue
        cursor = conn.cursor()
        for statement in statements:
            print(f"[exec] {sql_file.name}: {statement[:120]}{'...' if len(statement) > 120 else ''}")
            cursor.execute(statement)
        conn.commit()
        print(f"[ok] applied {sql_file.name}")


def upsert_source_systems(conn: pyodbc.Connection, environment: str, dry_run: bool) -> int:
    data = yaml.safe_load((METADATA_DIR / "source_systems.yaml").read_text())
    rows = data["source_systems"]

    merge_sql = """
    MERGE metadata.SourceSystemConfig AS target
    USING (SELECT ? AS SourceSystemName, ? AS SourceType, ? AS Config) AS source
        ON target.SourceSystemName = source.SourceSystemName
    WHEN MATCHED AND target.IsActive = 1 THEN
        UPDATE SET SourceType = source.SourceType, Config = source.Config,
                   UpdatedDate = SYSUTCDATETIME(), ModifiedBy = SUSER_SNAME()
    WHEN NOT MATCHED THEN
        INSERT (SourceSystemName, SourceType, Config)
        VALUES (source.SourceSystemName, source.SourceType, source.Config);
    """
    # IsActive = 0 rows are left alone entirely -- never resurrected by a seed run. This is the
    # one rule that does translate from the old framework's owner=ci/ops merge, even without an
    # owner column (see docs/metadata-db-mapping.md, "What does not translate").

    if dry_run:
        for row in rows:
            print(f"[dry-run] would upsert SourceSystemConfig: {row['source_system_name']}")
        return len(rows)

    cursor = conn.cursor()
    for row in rows:
        cursor.execute(
            merge_sql,
            row["source_system_name"],
            row["source_type"],
            json.dumps(row["config"]),
        )
    conn.commit()
    return len(rows)


def upsert_target_stores(conn: pyodbc.Connection, environment: str, dry_run: bool) -> int:
    data = yaml.safe_load((METADATA_DIR / "target_stores.yaml").read_text())
    rows = data["targets"]
    placeholder = data.get("placeholder_marker", PLACEHOLDER_MARKER)

    merge_sql = """
    MERGE metadata.TargetStoreConfig AS target
    USING (SELECT ? AS TargetName, ? AS TargetPath, ? AS LayerName, ? AS TargetWorkSpaceId,
                  ? AS TargetLakehouseId, ? AS TargetSchema, ? AS Config) AS source
        ON target.TargetName = source.TargetName
    WHEN MATCHED AND target.IsActive = 1 THEN
        UPDATE SET TargetPath = source.TargetPath, LayerName = source.LayerName,
                   TargetWorkSpaceId = source.TargetWorkSpaceId,
                   TargetLakehouseId = source.TargetLakehouseId,
                   TargetSchema = source.TargetSchema, Config = source.Config,
                   UpdatedDate = SYSUTCDATETIME(), ModifiedBy = SUSER_SNAME()
    WHEN NOT MATCHED THEN
        INSERT (TargetName, TargetPath, LayerName, TargetWorkSpaceId, TargetLakehouseId,
                TargetSchema, Config)
        VALUES (source.TargetName, source.TargetPath, source.LayerName, source.TargetWorkSpaceId,
                source.TargetLakehouseId, source.TargetSchema, source.Config);
    """

    written = 0
    for row in rows:
        per_env = row["per_environment"].get(environment)
        if per_env is None:
            print(f"[warn] {row['target_name']}: no per_environment entry for {environment}, skipping")
            continue
        if per_env["target_lakehouse_id"] == placeholder:
            print(
                f"[warn] {row['target_name']}: target_lakehouse_id is still the placeholder "
                f"'{placeholder}' for {environment} -- seeding it anyway, but nothing that reads "
                f"this row can resolve a real lakehouse until it's filled in. Not treated as fatal "
                f"for this POC (mirrors the 'unset FABRIC_PLATFORM_WORKSPACE_ID warns rather than "
                f"failing' convention in CLAUDE.md)."
            )
        if dry_run:
            print(f"[dry-run] would upsert TargetStoreConfig: {row['target_name']}")
            written += 1
            continue
        cursor = conn.cursor()
        cursor.execute(
            merge_sql,
            row["target_name"],
            row["target_path"],
            row["layer_name"],
            per_env["target_workspace_id"],
            per_env["target_lakehouse_id"],
            row["target_schema"],
            json.dumps(row["config"]),
        )
        conn.commit()
        written += 1
    return written


def upsert_source_objects(conn: pyodbc.Connection, dry_run: bool) -> int:
    """Bridges SourceSystemConfig and TargetStoreConfig: which object, at which layer, loaded how.

    source_system_name / target_name are natural-key references, not raw IDs -- SourceSystemId /
    TargetStoreID are IDENTITY-generated in the other two tables and unknowable ahead of time in
    version-controlled JSON, so they're resolved live via subquery in the MERGE's USING clause.
    A natural key that doesn't resolve (typo, or seeded before its parent row exists) hits the
    NOT NULL constraint on SourceSystemId/TargetStoreID and fails loudly, by design -- no need
    for a separate Python-side check.
    """
    data = yaml.safe_load((METADATA_DIR / "source_objects.yaml").read_text())
    rows = data["source_objects"]

    merge_sql = """
    MERGE metadata.SourceObjectConfig AS target
    USING (
        SELECT
            (SELECT SourceSystemId FROM metadata.SourceSystemConfig WHERE SourceSystemName = ?) AS SourceSystemId,
            ? AS SourceObjectName,
            ? AS LayerName,
            CAST((SELECT TargetStoreConfigId FROM metadata.TargetStoreConfig WHERE TargetName = ?) AS NVARCHAR(200)) AS TargetStoreID,
            ? AS LoadType,
            ? AS Config
    ) AS source
        ON target.SourceSystemId = source.SourceSystemId
           AND target.SourceObjectName = source.SourceObjectName
           AND target.LayerName = source.LayerName
    WHEN MATCHED AND target.IsActive = 1 THEN
        UPDATE SET TargetStoreID = source.TargetStoreID, LoadType = source.LoadType,
                   Config = source.Config, UpdatedDate = SYSUTCDATETIME(), ModifiedBy = SUSER_SNAME()
    WHEN NOT MATCHED THEN
        INSERT (SourceSystemId, SourceObjectName, LayerName, TargetStoreID, LoadType, Config)
        VALUES (source.SourceSystemId, source.SourceObjectName, source.LayerName,
                source.TargetStoreID, source.LoadType, source.Config);
    """

    if dry_run:
        for row in rows:
            print(f"[dry-run] would upsert SourceObjectConfig: {row['source_object_name']}/{row['layer_name']}")
        return len(rows)

    cursor = conn.cursor()
    for row in rows:
        cursor.execute(
            merge_sql,
            row["source_system_name"],
            row["source_object_name"],
            row["layer_name"],
            row["target_name"],
            row["load_type"],
            json.dumps(row["config"]),
        )
    conn.commit()
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace-id", required=True, help="Platform workspace holding db_platform_metadata")
    parser.add_argument("--environment", required=True, help="DEV or TEST -- selects target_stores.yaml's per_environment values")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be executed, change nothing")
    args = parser.parse_args()

    credential = ClientSecretCredential(
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
        tenant_id=os.environ["AZURE_TENANT_ID"],
    )

    server_fqdn, database_name = get_database_server_fqdn(args.workspace_id, credential)
    print(f"[info] resolved {SQL_DATABASE_ITEM_NAME} -> {server_fqdn}/{database_name}")

    conn = None if args.dry_run else connect(server_fqdn, database_name, credential)
    if conn is not None:
        probe_connection(conn)

    apply_ddl(conn, args.dry_run)
    source_count = upsert_source_systems(conn, args.environment, args.dry_run)
    target_count = upsert_target_stores(conn, args.environment, args.dry_run)
    # Must run after both of the above: its MERGE resolves SourceSystemId/TargetStoreID by
    # looking up their natural keys in the tables those two functions just populated.
    object_count = upsert_source_objects(conn, args.dry_run)

    print(f"[done] SourceSystemConfig: {source_count} row(s) processed")
    print(f"[done] TargetStoreConfig: {target_count} row(s) processed")
    print(f"[done] SourceObjectConfig: {object_count} row(s) processed")


if __name__ == "__main__":
    main()
