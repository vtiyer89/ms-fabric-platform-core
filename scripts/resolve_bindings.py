"""Rewrite Dev GUIDs to the target environment's GUIDs, in place, before publishing.

This is what replaces the five parameter.yml files.

    python scripts/resolve_bindings.py \
        --repository-directory ../ms-fabric-dd-trip-data/silver \
        --environment TEST --dry-run

How it works, and why it needs no per-rule configuration:

Every find_value in every parameter.yml was a Dev GUID. metadata/environments/dev.yml records
which logical name each of those GUIDs belongs to; the target environment's map records how to
find the same logical name there. Resolving the target and pairing the two gives a
{dev_guid -> target_guid} map, and GUIDs are globally unique, so a whole-file replace is exact —
the item_type/item_name scoping parameter.yml needed only existed to disambiguate, and there is
nothing to disambiguate.

Targets resolve LIVE against the Fabric API by display name, not from the md_* tables. The tables
may be empty on a first deploy or stale after one, and reading them would lose the self-healing
property parameter.yml had, where a workspace recreated under the same name simply works. The
tables are for runtime consumers; this is deploy time.

Two things a whole-GUID swap cannot express, both handled after the global pass:

  - jsonpath_overrides, for one Dev GUID that must become two different target values.
  - connections, which are tenant objects with no live resolution and come from CI variables.
"""

import argparse
import json
import os
import pathlib
import re
import sys

import yaml
from jsonpath_ng.ext import parse as jsonpath_parse

from fabric_api import api_get_all, resolve_item_id, resolve_workspace_id, session_for_service_principal

ENV_MAP_DIRECTORY = pathlib.Path(__file__).resolve().parent.parent / "metadata" / "environments"
GUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
# Fabric's "the current workspace" sentinel, and fabric-cicd's placeholder logical id.
NULL_GUID = "00000000-0000-0000-0000-000000000000"

# Fabric's own item metadata. These carry GUIDs that belong to the item definition rather than to
# any environment, so they are never rewritten and never reported as unknown.
IGNORED_FILENAMES = {".platform"}


def load_env_map(environment):
    path = ENV_MAP_DIRECTORY / f"{environment.lower()}.yml"
    if not path.exists():
        sys.exit(
            f"[error] no environment map at {path}.\n"
            f"[error] Every environment needs one; it is the only file edited per environment."
        )
    env_map = yaml.safe_load(path.read_text()) or {}
    declared = env_map.get("environment")
    if declared != environment:
        sys.exit(
            f"[error] {path} declares environment {declared!r} but this run was parameterised "
            f"as {environment!r}. Refusing to substitute the wrong environment's GUIDs."
        )
    return env_map


def dev_guids(dev_map):
    """{logical_name: guid} for each side, excluding anything marked non-substitutable."""
    workspaces, items, excluded = {}, {}, {}
    for name, spec in (dev_map.get("workspaces") or {}).items():
        if "workspace_id" not in spec:
            continue
        if spec.get("global_substitution", True):
            workspaces[name] = spec["workspace_id"]
        else:
            excluded[name] = spec["workspace_id"]
    for name, spec in (dev_map.get("items") or {}).items():
        if "item_id" in spec:
            items[name] = spec["item_id"]
    return workspaces, items, excluded


def resolve_targets(session, target_map, needed_workspaces, needed_items):
    """Resolve the target environment's GUIDs live, by display name."""
    all_workspaces = api_get_all(session, "/workspaces")
    workspace_ids, unresolved, items_by_workspace = {}, [], {}

    for name, spec in (target_map.get("workspaces") or {}).items():
        display_name = spec.get("display_name")
        if not display_name:
            unresolved.append(f"workspace {name!r}: target map has no display_name")
            continue
        resolved = resolve_workspace_id(display_name, all_workspaces)
        if resolved is None:
            unresolved.append(f"workspace {name!r}: display name {display_name!r} matched no workspace")
            continue
        workspace_ids[name] = resolved

    item_ids = {}
    for name, spec in (target_map.get("items") or {}).items():
        if name not in needed_items:
            continue
        ws_logical = spec["workspace"]
        if ws_logical not in workspace_ids:
            unresolved.append(f"item {name!r}: its workspace {ws_logical!r} did not resolve")
            continue
        ws_id = workspace_ids[ws_logical]
        if ws_id not in items_by_workspace:
            items_by_workspace[ws_id] = api_get_all(session, f"/workspaces/{ws_id}/items")
        resolved = resolve_item_id(spec["display_name"], spec["type"], items_by_workspace[ws_id])
        if resolved is None:
            unresolved.append(
                f"item {name!r}: {spec['type']} {spec['display_name']!r} not found in "
                f"workspace {ws_logical!r} — deploy that workspace first"
            )
            continue
        item_ids[name] = resolved

    missing_workspaces = [n for n in needed_workspaces if n not in workspace_ids]
    unresolved += [f"workspace {n!r}: not present in the target environment map" for n in missing_workspaces]
    return workspace_ids, item_ids, unresolved


def connection_values(env_map, label):
    """{logical_name: guid} for connections, read from the CI variables the map names."""
    values, missing = {}, []
    for name, spec in (env_map.get("connections") or {}).items():
        if "connection_id" in spec:
            values[name] = spec["connection_id"]
            continue
        variable = spec.get("from_env")
        if not variable:
            missing.append(f"connection {name!r}: neither connection_id nor from_env")
            continue

        # Either spelling. CI already sets FABRIC_PARAM_<NAME> for the $ENV: tokens parameter.yml
        # used, so accepting the prefix means no CI variable has to be renamed in this step. The
        # bare name wins if both are set.
        value = (os.environ.get(variable) or os.environ.get(f"FABRIC_PARAM_{variable}") or "").strip()
        if not value:
            missing.append(
                f"connection {name!r}: neither {variable} nor FABRIC_PARAM_{variable} is set"
            )
            continue
        values[name] = value
    return values, missing


def build_substitution_map(dev_map, target_map, session):
    dev_ws, dev_items, excluded = dev_guids(dev_map)
    target_ws, target_items, unresolved = resolve_targets(session, target_map, dev_ws, dev_items)

    dev_connections, dev_missing = connection_values(dev_map, "DEV")
    target_connections, target_missing = connection_values(target_map, "target")
    unresolved += dev_missing + target_missing

    substitutions = {}
    for name, dev_guid in dev_ws.items():
        if name in target_ws:
            substitutions[dev_guid] = (target_ws[name], f"workspace {name}")
    for name, dev_guid in dev_items.items():
        if name in target_items:
            substitutions[dev_guid] = (target_items[name], f"item {name}")
    for name, dev_guid in dev_connections.items():
        if name in target_connections:
            substitutions[dev_guid] = (target_connections[name], f"connection {name}")
        else:
            unresolved.append(f"connection {name!r}: present in DEV but not in the target map")

    return substitutions, excluded, unresolved, target_ws


def candidate_files(root):
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in IGNORED_FILENAMES:
            yield path


def apply_substitutions(root, substitutions, dry_run):
    """Whole-file replace. Returns (changed_files, applied_counts)."""
    applied = {dev: 0 for dev in substitutions}
    changed = []

    for path in candidate_files(root):
        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        updated = original
        for dev_guid, (target_guid, label) in substitutions.items():
            if dev_guid in updated:
                applied[dev_guid] += updated.count(dev_guid)
                updated = updated.replace(dev_guid, target_guid)

        if updated != original:
            changed.append(path)
            if not dry_run:
                path.write_text(updated, encoding="utf-8")
    return changed, applied


def apply_jsonpath_overrides(root, overrides, target_ws, dry_run):
    """The one case a whole-GUID swap cannot express. Applied after the global pass."""
    results = []
    for override in overrides or []:
        workspace_logical = override["workspace"]
        if workspace_logical not in target_ws:
            sys.exit(
                f"[error] jsonpath override {override.get('description')!r} targets workspace "
                f"{workspace_logical!r}, which did not resolve in the target environment."
            )
        value = target_ws[workspace_logical]
        expression = jsonpath_parse(override["path"])

        # An override is declared once per environment but applied per repository directory, so
        # most overrides are simply not applicable to most directories. "Not applicable" and
        # "applicable but matched nothing" must be told apart: the first is normal, the second is
        # the silent no-op this project keeps being bitten by.
        candidates = [
            path
            for path in candidate_files(root)
            if path.suffix == ".json" and override["item_name"] in str(path)
        ]
        if not candidates:
            print(f"[info] override {override.get('description')!r}: {override['item_name']} is "
                  f"not in this directory, skipping")
            continue

        matched_any = False
        for path in candidates:
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

            matches = expression.find(document)
            if not matches:
                continue
            matched_any = True
            for match in matches:
                results.append((override["description"], match.value, value, path))
            if not dry_run:
                expression.update(document, value)
                path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

        if not matched_any:
            sys.exit(
                f"[error] jsonpath override {override.get('description')!r} found "
                f"{override['item_name']} under {root} but its path matched nothing.\n"
                f"[error] path: {override['path']}\n"
                f"[error] An override that matches nothing is a silent no-op: the Dev value it "
                f"was meant to replace ships unchanged. Check the activity name against the "
                f"pipeline definition."
            )
    return results


def collect_logical_ids(root):
    """logicalId values from every .platform file under the directory.

    fabric-cicd rewrites these itself in _replace_logical_ids: each item's .platform carries a
    logicalId, and any reference to it inside another item's definition is swapped for the
    deployed GUID at publish time. That is how pl_bronze_nyc_taxi's copyJobId and both invoke
    pipelines' notebookId resolve without ever having had a parameter.yml rule. They are not
    environment values and must not be reported as unaccounted for.
    """
    logical_ids = set()
    for path in root.rglob(".platform"):
        try:
            config = json.loads(path.read_text(encoding="utf-8")).get("config", {})
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if config.get("logicalId"):
            logical_ids.add(config["logicalId"])
    return logical_ids


def guids_in_file(path):
    """{guid: reason_to_ignore or None}, structure-aware where the format allows it.

    A blunt regex over these files is far too noisy to act on — a semantic model alone carries
    tens of lineageTags — and a noisy guard gets switched off, which is how the silent-skip class
    comes back. Three exclusions, each on a structural rule rather than a hardcoded value:

      - JSON key exactly "id": Fabric names cross-references "<thing>Id" (workspaceId, notebookId,
        copyJobId) and self-identity plainly "id". A bare id is internal to the item.
      - TMDL lineageTag: semantic-model internal identity, regenerated by Power BI, never an
        environment reference.
      - The all-zero GUID: Fabric's "the current workspace" sentinel.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {}

    found = {}
    if path.suffix == ".json":
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            document = None
        if document is not None:
            def walk(node):
                if isinstance(node, dict):
                    for key, value in node.items():
                        if isinstance(value, str) and GUID_PATTERN.fullmatch(value):
                            found.setdefault(value, "internal id" if key == "id" else None)
                            if key != "id":
                                found[value] = None
                        walk(value)
                elif isinstance(node, list):
                    for value in node:
                        walk(value)
            walk(document)
            return found

    lineage = set(re.findall(r"lineageTag:\s*(" + GUID_PATTERN.pattern + ")", text))
    for guid in GUID_PATTERN.findall(text):
        reason = "lineageTag" if guid in lineage else None
        found.setdefault(guid, reason)
        if reason is None:
            found[guid] = None
    return found


def placeholder_guids(dev_map):
    """Sentinel GUIDs standing in for Dev values that do not exist yet.

    The Dev platform workspace is the only case today. A sentinel reads exactly like a real GUID
    in a diff, and the failure it causes — a workload lakehouse shortcutting into nothing —
    surfaces only at run time as "table not found", hours later and in one workspace.
    """
    found = {}
    for section in ("workspaces", "items"):
        for name, spec in (dev_map.get(section) or {}).items():
            if spec.get("placeholder"):
                guid = spec.get("workspace_id") or spec.get("item_id")
                if guid:
                    found[guid] = name
    return found


def assert_no_placeholder_guids(root, placeholders):
    """Fail if a sentinel survived substitution."""
    if not placeholders:
        return
    surviving = {}
    for path in candidate_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for guid, name in placeholders.items():
            if guid in text:
                surviving.setdefault(f"{name} ({guid})", set()).add(path.name)
    if surviving:
        lines = [f"{label}  in {', '.join(sorted(files))}" for label, files in sorted(surviving.items())]
        sys.exit(
            f"[error] {len(surviving)} placeholder GUID(s) are still present after substitution:\n  "
            + "\n  ".join(lines)
            + "\n\n[error] These are sentinels for Dev values that do not exist. Shipping one "
              "produces a shortcut pointing at nothing, which fails at run time as "
              "\"table not found\" rather than at deploy time. Check that the target "
              "environment's map resolves the logical name."
        )


def report_unknown_guids(root, substitutions, excluded):
    """GUIDs left behind that nothing accounts for.

    This is the guard that replaces assert_find_values_present, and it is strictly stronger.
    That check asked whether each configured find_value appeared somewhere, so it was blind to a
    GUID that no rule mentioned at all. This asks the opposite question — what is still here that
    we cannot explain — which is how the Dev "pipelines" workspace GUID was found: it was covered
    only by a key_value_replace, and therefore invisible to any find_value audit.
    """
    known_targets = {target for target, _ in substitutions.values()}
    accounted = (
        set(substitutions)
        | known_targets
        | set(excluded.values())
        | collect_logical_ids(root)
        | {NULL_GUID}
    )

    unknown = {}
    for path in candidate_files(root):
        for guid, ignore_reason in guids_in_file(path).items():
            if guid in accounted or ignore_reason:
                continue
            unknown.setdefault(guid, set()).add(path.name)
    return unknown


def check_offline(repository_directory, dev_environment="DEV"):
    """Validate what can be validated without credentials.

    The replacement for debug_parameterization.py, which was offline and is the thing the
    fabric-verify skill runs. It cannot resolve targets — that needs the API — but it catches
    the class of mistake that used to be caught by assert_find_values_present: a Dev GUID in the
    map that appears nowhere in the item definitions, which means the binding is stale and the
    substitution will silently do nothing.
    """
    root = pathlib.Path(repository_directory)
    if not root.is_dir():
        sys.exit(f"[error] --repository-directory {root} is not a directory")

    dev_map = load_env_map(dev_environment)
    workspaces, items, excluded = dev_guids(dev_map)
    placeholders = placeholder_guids(dev_map)

    corpus = {}
    for path in candidate_files(root):
        try:
            corpus[path] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

    present, absent = [], []
    for label, guid in [(f"workspace {n}", g) for n, g in workspaces.items()] + [
        (f"item {n}", g) for n, g in items.items()
    ]:
        where = [p.name for p, text in corpus.items() if guid in text]
        (present if where else absent).append((label, guid, where))

    for label, guid, where in sorted(present):
        note = " (placeholder)" if guid in placeholders else ""
        print(f"[ok]   {label}{note}: {guid} in {', '.join(sorted(set(where)))}")
    for label, guid, _ in sorted(absent):
        print(f"[--]   {label}: {guid} appears nowhere under {root}")

    print(
        f"\n[info] {len(present)} of {len(present) + len(absent)} Dev bindings are present here. "
        f"A binding absent from THIS directory is normal — the maps describe the whole estate."
    )
    print(
        "[info] Offline only. Target GUIDs, jsonpath overrides and the unknown-GUID guard all "
        "need credentials; run without --offline to check those."
    )
    return present, absent


def resolve(repository_directory, environment, dev_environment="DEV", dry_run=False,
            allow_unknown_guids=False, session=None):
    """Resolve and apply every binding for one repository directory.

    Importable so deploy_fabric_item.py can call it directly rather than shelling out — a
    subprocess would swallow the exit code detail that says which binding failed.
    """
    root = pathlib.Path(repository_directory)
    if not root.is_dir():
        sys.exit(f"[error] --repository-directory {root} is not a directory")

    if environment == dev_environment:
        sys.exit(
            f"[error] target environment and find-side environment are both {environment!r}. "
            f"That would substitute every GUID with itself."
        )

    dev_map = load_env_map(dev_environment)
    target_map = load_env_map(environment)

    session = session or session_for_service_principal()
    substitutions, excluded, unresolved, target_ws = build_substitution_map(dev_map, target_map, session)

    if unresolved:
        sys.exit(
            f"[error] {len(unresolved)} binding(s) did not resolve:\n  "
            + "\n  ".join(sorted(unresolved))
            + "\n\n[error] Nothing has been changed. Display names are matched literally and "
              "case-sensitively; an item that has not been deployed yet is the other usual cause."
        )

    print(f"[info] {len(substitutions)} binding(s) resolved for {environment}")

    changed, applied = apply_substitutions(root, substitutions, dry_run)
    verb = "would replace" if dry_run else "replaced"
    for dev_guid, (target_guid, label) in sorted(substitutions.items(), key=lambda kv: kv[1][1]):
        count = applied[dev_guid]
        if count:
            print(f"[debug] {verb} {dev_guid} with {target_guid} ({label}, {count}x)")
        else:
            print(f"[debug] {label}: {dev_guid} appears nowhere under {root}")

    overrides = apply_jsonpath_overrides(root, target_map.get("jsonpath_overrides"), target_ws, dry_run)
    for description, before, after, path in overrides:
        print(f"[debug] {verb} {before} with {after} ({description}, {path.name})")

    if not dry_run:
        assert_no_placeholder_guids(root, placeholder_guids(dev_map))

    unknown = report_unknown_guids(root, substitutions, excluded)
    if unknown:
        lines = [f"{guid}  in {', '.join(sorted(files))}" for guid, files in sorted(unknown.items())]
        message = (
            f"[error] {len(unknown)} GUID(s) under {root} are accounted for by nothing in the "
            f"environment maps:\n  " + "\n  ".join(lines) + "\n\n"
            f"[error] Each is either a Dev value that will ship unchanged, or a value that needs "
            f"a logical name in metadata/environments/. Add it, or mark it "
            f"global_substitution: false if it is handled by a jsonpath override."
        )
        if allow_unknown_guids:
            print(message.replace("[error]", "[warn]"))
        else:
            sys.exit(message)

    print(f"[info] {len(changed)} file(s) {'would change' if dry_run else 'changed'}")
    return substitutions


def main():
    parser = argparse.ArgumentParser(description="Resolve Dev GUIDs to a target environment.")
    parser.add_argument("--repository-directory", required=True)
    parser.add_argument(
        "--environment",
        help="Target environment, e.g. TEST. Not needed with --offline.",
    )
    parser.add_argument("--dev-environment", default="DEV", help="Map holding the find-side GUIDs")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print every substitution that would be applied and change nothing.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Validate the maps and Dev GUID presence without credentials. No API calls.",
    )
    parser.add_argument(
        "--allow-unknown-guids",
        action="store_true",
        help="Report unaccounted-for GUIDs without failing. Off by default.",
    )
    args = parser.parse_args()

    if args.offline:
        check_offline(args.repository_directory, dev_environment=args.dev_environment)
        return

    if not args.environment:
        parser.error("--environment is required unless --offline is given")

    resolve(
        args.repository_directory,
        args.environment,
        dev_environment=args.dev_environment,
        dry_run=args.dry_run,
        allow_unknown_guids=args.allow_unknown_guids,
    )


if __name__ == "__main__":
    main()
