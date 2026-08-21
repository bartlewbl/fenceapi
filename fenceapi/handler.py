from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from fenceapi.client import HttpClient
from fenceapi.scraper import Scraper


def lambda_handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    event = event or {}
    nation = event.get("nation") or os.environ.get("FENCEAPI_NATION")
    include_results = _bool(event.get("include_results") or os.environ.get("FENCEAPI_INCLUDE_RESULTS"))
    interval = float(event.get("interval") or os.environ.get("FENCEAPI_INTERVAL", "1.0"))
    scraper = Scraper(client=HttpClient(min_interval=interval))
    payload = scraper.snapshot(
        nation=nation,
        include_competitions=True,
        include_results=include_results,
    )
    payload["scraped_at"] = datetime.now(timezone.utc).isoformat()

    bucket = event.get("bucket") or os.environ.get("FENCEAPI_S3_BUCKET")
    key = None
    if bucket:
        key = _write_s3(bucket, payload, nation)
        payload["s3"] = {"bucket": bucket, "key": key}

    return {
        "ok": True,
        "nation": nation,
        "current": len(payload.get("current") or []),
        "upcoming": len(payload.get("upcoming") or []),
        "details": len(payload.get("details") or []),
        "s3_key": key,
    }


def _write_s3(bucket: str, payload: dict[str, Any], nation: str | None) -> str:
    import boto3

    stamp = datetime.now(timezone.utc).strftime("%Y/%m/%d/%H%M%S")
    prefix = (nation or "all").lower()
    key = f"fencingworldwide/{prefix}/{stamp}.json"
    boto3.client("s3").put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    return key


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
