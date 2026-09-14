# Recovered from bytecode — 2026-09-14

Six files existed only as compiled bytecode in `scripts/__pycache__/` and `tests/__pycache__/`.
They were never committed to any branch: `git log --all -- '**/<name>.py'` returns nothing for
each. The `.py` sources are gone.

`scripts/metadata_reader_source.py` was recovered **verbatim** — its entire body is a module-level
string constant, which `marshal` returns intact. The other five are reconstructions.

Extraction, for anything still to be recovered:

```python
import marshal, pathlib
code = marshal.loads(pathlib.Path("tests/__pycache__/<name>.cpython-311-pytest-8.4.2.pyc").read_bytes()[16:])
code.co_consts[0]                                    # module docstring
[c.co_name for c in code.co_consts if hasattr(c, "co_name")]   # function names
```

Bytecode preserves docstrings, names and constants. It does **not** preserve function bodies in
readable form, so every reconstruction below is new code written to the recovered intent and
must be reviewed as new code, not as a restoration.

## Recovered and committed

| File | Fidelity |
|---|---|
| `scripts/metadata_reader_source.py` | Verbatim. Round-trip asserted against the bytecode constant. |
| `scripts/render_metadata_reader.py` | Reconstructed from docstring + symbol list. |
| `scripts/run_seed_metadata.py` | Reconstructed from docstring + symbol list. |

## Still to port — with the original docstrings

Each of these guards code that does not exist yet, so porting them now would make the suite red.
Each is ported in the step that makes it pass. **The docstrings below are the original authors'
reasoning and are the reason these checks exist — they are not reconstructable from the code.**


### `tests/test_metadata_guards.py`

Test functions recovered: `test_accepts_workspace_and_config_names_not_just_items`, `test_catches_every_reader_entry_point`, `test_ignores_files_that_are_not_notebooks`, `test_passes_when_every_logical_name_is_in_the_environment_map`, `test_placeholder_guid_stops_the_deploy`, `test_placeholder_is_found_in_any_file_type`, `test_real_guids_pass`, `test_rejects_a_logical_name_the_seeder_will_never_supply`, `test_skips_when_the_environment_has_no_map`

> assert_metadata_coverage and assert_no_placeholder_guids.
> 
> Once a notebook resolves a reference from md_item instead of carrying a substituted GUID,
> `assert_find_values_present` stops covering it — there is no find_value for that reference any
> more. Nothing else checks it, and the failure surfaces only at run time, mid-pipeline, as a
> LookupError from the generated reader. assert_metadata_coverage is the replacement guard.
> 
> assert_no_placeholder_guids covers the other new class: references whose real DEV value does
> not exist yet (the Dev platform workspace), which sit in git as sentinels that read exactly
> like real GUIDs in a diff.


### `tests/test_metadata_reader_sync.py`

Test functions recovered: `test_consumer_notebooks_carry_no_guids_outside_the_metadata_block`, `test_copies_are_byte_identical_to_each_other`, `test_every_embedded_copy_is_current`

> Every embedded copy of the metadata reader must match the single source.
> 
> The reader cannot be imported at run time — Fabric notebooks have no path back to
> platform-core — so it is generated into each consuming notebook. Generated code with no
> compiler drifts the moment someone edits a copy in the Fabric UI and syncs it back through git
> integration, and a drifted copy fails at run time in one notebook only, which is the hardest
> shape of this bug to find.
> 
> `render_metadata_reader.py --check` is the guard; this runs it the way CI does.


### `tests/test_lakehouse_shortcuts.py`

Test functions recovered: `test_every_shortcut_guid_is_rewritten_for_the_target_environment`, `test_every_table_a_notebook_reads_is_provided`, `test_notebooks_hold_no_cross_workspace_guids`, `test_shortcut_name_matches_the_table_it_targets`, `test_shortcut_targets_are_onelake`, `test_shortcuts_mount_under_the_lakehouse_default_schema`

> Checks over the real OneLake shortcuts that carry cross-workspace reads.
> 
> Silver and gold no longer name an upstream workspace in their notebooks. They read a table
> that reaches them through a shortcut declared in their own lakehouse, and `parameter.yml`
> rewrites that shortcut's target per environment — see docs/upstream-shortcuts-design.md.
> 
> That moves the failure mode. Before, a stale cross-workspace GUID sat in a notebook where
> `assert_find_values_present` covered it; the same guard still covers the shortcut file, but a
> shortcut can now be *structurally* wrong in ways a GUID check can't see — the wrong table name,
> a mount point under the wrong schema, a query reading a table nothing provides. None of those
> fail the deploy. They fail the next pipeline run, hours later, as "table not found".
> 
> These are those checks. Skipped when the item repos aren't cloned alongside platform-core.


### `tests/test_shortcut_targets.py`

Test functions recovered: `test_every_shortcut_target_guid_has_a_rule_scoped_to_its_lakehouse`, `test_shortcut_rules_resolve_the_target_environment_live`, `test_shortcut_targets_are_not_the_lakehouse_that_holds_them`

> Every GUID in a shortcuts.metadata.json must be rewritten for the target environment.
> 
> A OneLake shortcut's target is a workspace GUID plus an item GUID, git-tracked in the
> lakehouse's shortcuts.metadata.json. These shortcuts carry the md_* metadata tables into each
> workload lakehouse. If no find_replace rule rewrites them, the deploy is still green and the
> workload shortcuts into *another environment's* metadata lakehouse — which then hands it that
> environment's item GUIDs, so it reads and writes the wrong environment's tables entirely. The
> shortcut looks correct in the portal and every Replacing line in the log looks fine.
> 
> `assert_find_values_present` in the deploy script cannot catch this. It only asks whether a
> find_value appears *somewhere* under the repository_directory, ignoring item_type — so a rule
> scoped to the Notebook satisfies it even when the GUID it needs to cover lives in the
> Lakehouse. These tests close that gap by checking the scoping, not just the presence.
> 
> See docs/metadata-driven-resolution.md.


### `tests/test_shortcut_publish_flag.py`

Test functions recovered: `test_does_not_enable_continue_on_shortcut_failure`, `test_flag_name_matches_the_installed_fabric_cicd`, `test_is_idempotent`, `test_sets_the_shortcut_publish_flag`

> enable_shortcut_publish_flag: the flag without which shortcuts are skipped in silence.
> 
> fabric-cicd only opens a lakehouse's shortcuts.metadata.json when this flag is set
> (`_items/_lakehouse.py`, post_publish_all). Without it there is no shortcut, no parameter
> substitution against that file, no log line, and a green deploy — so silver and gold would
> publish against an upstream table that was never created.
> 
> Unlike enable_items_to_include it does NOT require enable_experimental_features; asserting
> that keeps a future version bump from quietly adding the dependency.
