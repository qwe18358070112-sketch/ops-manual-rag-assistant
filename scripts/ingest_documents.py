from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ingestion.pipeline import ingest_documents


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest ops manuals and generate structured chunks.")
    parser.add_argument(
        "--manifest",
        default="data/manifests/seed_documents.json",
        help="Path to the seed document manifest.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional document limit for faster local smoke runs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = (PROJECT_ROOT / args.manifest).resolve()
    summary = ingest_documents(project_root=PROJECT_ROOT, manifest_path=manifest_path, limit=args.limit)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
