"""Write the shared metadata-reader cell into every consuming notebook.

    python scripts/render_metadata_reader.py            # rewrite the notebooks in place
    python scripts/render_metadata_reader.py --check     # fail if any copy is stale (CI)

The reader body lives in `metadata_reader_source.py`. Each consuming notebook carries a pair of
generated markers; everything between them is replaced. The markers must already be present —
this script will not guess where in a notebook a cell belongs, because the reader has to be
defined before the cell that uses it and only the notebook's author knows that order.

Consuming notebooks are listed in CONSUMERS rather than discovered, so adding a consumer is a
deliberate edit that shows up in review.
"""

import argparse
import pathlib

from metadata_reader_source import BEGIN_MARKER, END_MARKER, READER_SOURCE

PLATFORM_CORE = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = PLATFORM_CORE.parent

# Paths are relative to REPO_ROOT — the directory holding all five repos side by side.
CONSUMERS = [
    "ms-fabric-dd-trip-data/silver/nb_silver_yellow_cab_transform.Notebook/notebook-content.py",
    "ms-fabric-dd-trip-data/gold/nb_gold_yellow_cab_aggregate.Notebook/notebook-content.py",
]


def replace_block(text, path):
    """Swap everything between the two markers for the current READER_SOURCE.

    Refuses to guess. A notebook without both markers is an error rather than an append,
    because the reader must be defined before the cell that calls it and this script has no
    way to know where that is.
    """
    start = text.find(BEGIN_MARKER)
    end = text.find(END_MARKER)
    if start == -1 or end == -1:
        raise SystemExit(
            f"[error] {path} has no generated-reader markers.\n"
            f"[error] Add these two lines around the cell that should hold the reader, then "
            f"re-run:\n  {BEGIN_MARKER} ... ---\n  {END_MARKER}"
        )
    if end < start:
        raise SystemExit(f"[error] {path}: END marker appears before BEGIN marker.")
    return text[:start] + READER_SOURCE + text[end + len(END_MARKER):]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if any embedded copy differs from the source, changing nothing.",
    )
    args = parser.parse_args()

    stale = []
    for relative in CONSUMERS:
        path = REPO_ROOT / relative
        if not path.exists():
            raise SystemExit(
                f"[error] consumer not found: {path}\n"
                f"[error] The item repos must be cloned alongside {PLATFORM_CORE.name}."
            )

        current = path.read_text(encoding="utf-8")
        rendered = replace_block(current, relative)

        if current == rendered:
            print(f"  up to date  {relative}")
            continue

        stale.append(relative)
        if args.check:
            print(f"  STALE       {relative}")
        else:
            path.write_text(rendered, encoding="utf-8")
            print(f"  rewrote     {relative}")

    if args.check and stale:
        raise SystemExit(
            f"\n[error] {len(stale)} embedded reader copy/copies differ from "
            f"scripts/metadata_reader_source.py.\n"
            f"[error] Run `python scripts/render_metadata_reader.py` and commit the result. "
            f"Never hand-edit the generated block inside a notebook — a copy edited in the "
            f"Fabric UI and synced back through git integration fails at run time in that one "
            f"notebook only."
        )
    print(f"\n{len(CONSUMERS)} consumer(s) checked, {len(stale)} stale.")


if __name__ == "__main__":
    main()
