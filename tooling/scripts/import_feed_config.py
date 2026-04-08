#!/usr/bin/env python3
"""Import legacy feed_config YouTube channels into tracked_sources."""

from __future__ import annotations

import argparse
import json

from pathlib import Path

from skim_core.db import DB_PATH, get_connection, init_db
from skim_core.feed_config import YOUTUBE_CHANNELS


def load_feed_config() -> dict[str, str]:
    """Load the workspace-managed YOUTUBE_CHANNELS mapping."""
    return YOUTUBE_CHANNELS


def preview_import(db_path: Path) -> dict:
    """Return the import preview without mutating the database."""
    init_db(db_path)
    channels = load_feed_config()
    conn = get_connection(db_path)

    existing = {
        row["canonical_id"]
        for row in conn.execute(
            "SELECT canonical_id FROM tracked_sources WHERE platform = 'youtube'"
        ).fetchall()
    }
    conn.close()

    incoming = [
        {
            "display_name": display_name,
            "canonical_id": channel_id,
            "handle_or_url": f"https://www.youtube.com/channel/{channel_id}",
        }
        for display_name, channel_id in channels.items()
    ]

    new_items = [item for item in incoming if item["canonical_id"] not in existing]
    skipped_items = [item for item in incoming if item["canonical_id"] in existing]

    return {
        "platform": "youtube",
        "total": len(incoming),
        "new_count": len(new_items),
        "skipped_count": len(skipped_items),
        "items": incoming,
    }


def run_import(db_path: Path) -> dict:
    """Insert missing YouTube channels into tracked_sources."""
    preview = preview_import(db_path)
    conn = get_connection(db_path)

    inserted = 0
    for item in preview["items"]:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO tracked_sources (
                platform,
                source_type,
                display_name,
                canonical_id,
                handle_or_url,
                is_enabled,
                focus_level,
                notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "youtube",
                "channel",
                item["display_name"],
                item["canonical_id"],
                item["handle_or_url"],
                1,
                0,
                "Imported from feed_config.py",
            ),
        )
        if cursor.rowcount > 0:
            inserted += 1

    conn.commit()
    conn.close()

    return {
        **preview,
        "inserted_count": inserted,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import legacy feed_config YouTube channels into tracked_sources."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help="SQLite database path. Defaults to data/skim.db.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Preview the import without writing to the database.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    result = (
        preview_import(args.db)
        if args.preview
        else run_import(args.db)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
