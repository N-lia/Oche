#!/usr/bin/env python3
"""Copy Mem0's local Qdrant collections into a shared Qdrant server.

Local Qdrant files take an exclusive lock, so the Telegram bot and WebApp
cannot share one directory.  Run this once after starting the Qdrant service,
with both application containers stopped, to preserve existing memories.

Usage:
    python tools/migrate_memory_to_qdrant.py \
        --source /data/oyi-memory/qdrant \
        --source /data/webapp-memory/qdrant \
        --url http://qdrant:6333

The script is idempotent: point ids are preserved, so re-running it updates
existing points instead of duplicating them.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable

from qdrant_client import QdrantClient, models


def _vector_size(vector) -> int:  # noqa: ANN001
    if isinstance(vector, dict):
        if not vector:
            raise ValueError("empty named-vector dict")
        return len(next(iter(vector.values())))
    return len(vector)


def _vectors_config(sample_vector):  # noqa: ANN001, ANN202
    if isinstance(sample_vector, dict):
        return {
            vector_name: models.VectorParams(
                size=_vector_size(vector),
                distance=models.Distance.COSINE,
            )
            for vector_name, vector in sample_vector.items()
        }
    return models.VectorParams(
        size=_vector_size(sample_vector),
        distance=models.Distance.COSINE,
    )


def _ensure_collection(
    target: QdrantClient,
    name: str,
    sample_vector,  # noqa: ANN001
) -> None:
    existing = {collection.name for collection in target.get_collections().collections}
    if name in existing:
        return
    target.create_collection(
        collection_name=name,
        vectors_config=_vectors_config(sample_vector),
    )


def _scroll_all(
    source: QdrantClient,
    collection: str,
) -> Iterable[models.Record]:
    offset = None
    while True:
        points, offset = source.scroll(
            collection_name=collection,
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        yield from points
        if offset is None:
            return


def migrate(source_path: str, target_url: str, *, api_key: str | None = None) -> int:
    source = QdrantClient(path=source_path)
    target = QdrantClient(url=target_url, api_key=api_key)
    moved = 0
    try:
        for collection in source.get_collections().collections:
            name = collection.name
            points = list(_scroll_all(source, name))
            if not points:
                print(f"{source_path}: {name}: no points, skipped")
                continue

            _ensure_collection(target, name, points[0].vector)
            target.upsert(
                collection_name=name,
                points=[
                    models.PointStruct(id=point.id, vector=point.vector, payload=point.payload)
                    for point in points
                ],
                wait=True,
            )
            moved += len(points)
            print(f"{source_path}: {name}: migrated {len(points)} points")
    finally:
        source.close()
        target.close()
    return moved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", required=True, help="Local Qdrant directory (repeatable)")
    parser.add_argument("--url", required=True, help="Qdrant server URL, e.g. http://qdrant:6333")
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args(argv)

    total = 0
    for source in args.source:
        total += migrate(source, args.url, api_key=args.api_key)
    print(f"Migrated {total} points total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
