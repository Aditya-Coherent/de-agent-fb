"""Optional durable copy of run exports to Google Cloud Storage.

Cloud Run's container filesystem is ephemeral — files written to it are lost
on restart, redeploy, or when a request lands on a different instance. This
module gives each run's Excel/Word/CSV a permanent, signed download URL
instead, so results survive all of that.

No-op if GCS_BUCKET is unset (e.g. local dev) — nothing else changes.
"""
from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path


def gcs_enabled() -> bool:
    return bool((os.getenv("GCS_BUCKET") or "").strip())


def _bucket_name() -> str:
    return (os.getenv("GCS_BUCKET") or "").strip()


def _ttl_days() -> int:
    # V4 signed URLs allow at most 7 days.
    ttl_days = int(os.getenv("GCS_URL_TTL_DAYS") or "7")
    return max(1, min(ttl_days, 7))


def _client_bucket():
    from google.cloud import storage

    client = storage.Client()
    return client.bucket(_bucket_name())


def _service_account_email(credentials) -> str:
    email = getattr(credentials, "service_account_email", None) or ""
    if email and email != "default":
        return email
    # Cloud Run / GCE metadata fallback
    try:
        from urllib.request import Request, urlopen

        req = Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email",
            headers={"Metadata-Flavor": "Google"},
        )
        return urlopen(req, timeout=2).read().decode("utf-8").strip()
    except Exception:
        return email if email != "default" else ""


def _iam_sign_kwargs() -> dict:
    """Cloud Run ADC has no private key — sign via IAM with an access token."""
    try:
        import google.auth
        from google.auth.transport import requests as google_requests

        credentials, _ = google.auth.default()
        # Local SA JSON keys can sign directly; skip IAM path.
        if getattr(credentials, "signer", None) is not None:
            return {}
        auth_request = google_requests.Request()
        credentials.refresh(auth_request)
        email = _service_account_email(credentials)
        token = getattr(credentials, "token", None) or ""
        if email and token:
            return {
                "service_account_email": email,
                "access_token": token,
            }
    except Exception as exc:  # pragma: no cover
        print(f"  [gcs] iam sign setup failed: {exc}", flush=True)
    return {}


def signed_url_for_blob(blob_path: str) -> str | None:
    """Fresh signed GET URL for an existing object, or None."""
    if not gcs_enabled() or not blob_path:
        return None
    try:
        bucket = _client_bucket()
        blob = bucket.blob(blob_path)
        if not blob.exists():
            return None
        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(days=_ttl_days()),
            method="GET",
            **_iam_sign_kwargs(),
        )
    except Exception as exc:  # pragma: no cover
        print(f"  [gcs] signed url failed for {blob_path}: {exc}", flush=True)
        return None


def signed_urls_for_slug(slug: str) -> dict[str, str]:
    """Re-mint download links for a finished run's xlsx/docx/csv in GCS."""
    if not gcs_enabled() or not slug:
        return {}
    urls: dict[str, str] = {}
    for key, name in (
        ("xlsx", f"{slug}.xlsx"),
        ("docx", f"{slug}.docx"),
        ("csv", f"{slug}.csv"),
    ):
        url = signed_url_for_blob(f"runs/{slug}/{name}")
        if url:
            urls[key] = url
    return urls


def download_run_file_bytes(slug: str, filename: str) -> bytes | None:
    """Fetch runs/{slug}/{filename} bytes from GCS (works on Cloud Run without signing)."""
    if not gcs_enabled() or not slug or not filename:
        return None
    try:
        bucket = _client_bucket()
        blob = bucket.blob(f"runs/{slug}/{filename}")
        if not blob.exists():
            return None
        return blob.download_as_bytes()
    except Exception as exc:  # pragma: no cover
        print(f"  [gcs] download failed for {slug}/{filename}: {exc}", flush=True)
        return None


def materialize_run_files(slug: str, dest_dir: Path) -> Path | None:
    """Download csv/xlsx/docx for slug into dest_dir. Returns local CSV path if available."""
    if not slug:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    csv_path = dest_dir / f"{slug}.csv"
    for name in (f"{slug}.csv", f"{slug}.xlsx", f"{slug}.docx"):
        target = dest_dir / name
        if target.exists() and target.stat().st_size > 0:
            continue
        raw = download_run_file_bytes(slug, name)
        if raw:
            target.write_bytes(raw)
    return csv_path if csv_path.exists() else None


def upload_run_file(local_path: Path, *, slug: str) -> str | None:
    """Upload one file under runs/{slug}/{filename} and return a signed URL.

    Returns None (never raises) if GCS isn't configured or the upload fails —
    callers should keep serving the local file as the primary path either way.
    """
    if not gcs_enabled() or not local_path.exists():
        return None
    try:
        bucket = _client_bucket()
        blob = bucket.blob(f"runs/{slug}/{local_path.name}")
        blob.upload_from_filename(str(local_path))
        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(days=_ttl_days()),
            method="GET",
            **_iam_sign_kwargs(),
        )
    except Exception as exc:  # pragma: no cover - best-effort durability layer
        print(f"  [gcs] upload failed for {local_path.name}: {exc}", flush=True)
        return None


def upload_json_blob(blob_path: str, payload: object) -> bool:
    if not gcs_enabled():
        return False
    try:
        bucket = _client_bucket()
        blob = bucket.blob(blob_path)
        blob.upload_from_string(
            json.dumps(payload, indent=2, default=str),
            content_type="application/json",
        )
        return True
    except Exception as exc:  # pragma: no cover
        print(f"  [gcs] json upload failed for {blob_path}: {exc}", flush=True)
        return False


def download_json_blob(blob_path: str) -> object | None:
    if not gcs_enabled():
        return None
    try:
        bucket = _client_bucket()
        blob = bucket.blob(blob_path)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover
        print(f"  [gcs] json download failed for {blob_path}: {exc}", flush=True)
        return None


SESSION_LOG_BLOB = "meta/session_log.json"
JOB_STATUS_PREFIX = "meta/jobs/"


def pull_session_log() -> list[dict]:
    raw = download_json_blob(SESSION_LOG_BLOB)
    return raw if isinstance(raw, list) else []


def push_session_log(entries: list[dict]) -> None:
    upload_json_blob(SESSION_LOG_BLOB, entries)


def _local_job_status_dir() -> Path:
    base = Path(os.getenv("MARKET_QUERY_OUTPUT_DIR") or "output/demo")
    path = base / "job_status"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_status_blob(run_id: str) -> str:
    return f"{JOB_STATUS_PREFIX}{run_id}.json"


def _phase_from_log(log: str) -> int:
    low = (log or "").lower()
    phase = 0
    if "[pipeline] phase 1" in low or "=== pipeline start ===" in low:
        phase = max(phase, 1)
    if "[pipeline] phase 2" in low:
        phase = max(phase, 2)
    if "[pipeline] phase 3" in low:
        phase = max(phase, 3)
    if "[pipeline] phase 4" in low or "[pipeline] classif" in low:
        phase = max(phase, 4)
    if (
        "[pipeline] csv saved" in low
        or "[pipeline] xlsx saved" in low
        or "[pipeline] docx saved" in low
        or "[pipeline] total time" in low
    ):
        phase = max(phase, 5)
    return phase


def serialize_job_status(job: dict) -> dict:
    """Compact durable snapshot (no huge result payloads)."""
    import time as _time

    log = str(job.get("log") or "")
    result = job.get("result") if isinstance(job.get("result"), dict) else None
    slug = ""
    if result:
        csv_path = str(result.get("_csv_path") or "")
        if csv_path:
            slug = Path(csv_path).stem
    if job.get("cancelled"):
        status = "cancelled"
    elif job.get("error"):
        status = "error"
    elif result is not None or job.get("has_result"):
        status = "ok"
    elif job.get("running"):
        status = "running"
    else:
        status = str(job.get("status") or "unknown")

    return {
        "run_id": str(job.get("run_id") or ""),
        "owner_email": str(job.get("owner_email") or "").strip().lower(),
        "job_type": str(job.get("job_type") or "pipeline"),
        "query": str(job.get("query") or ""),
        "country": str(job.get("country") or "global"),
        "profile": str(job.get("profile") or ""),
        "cap": job.get("cap"),
        "running": bool(job.get("running")),
        "cancelled": bool(job.get("cancelled")),
        "cancel_requested": bool(job.get("cancel_requested")),
        "error": job.get("error"),
        "status": status,
        "started_at": float(job.get("started_at") or _time.time()),
        "updated_at": _time.time(),
        "phase": max(int(job.get("phase") or 0), _phase_from_log(log)),
        "log_tail": log[-8000:],
        "has_result": bool(result is not None or job.get("has_result")),
        "slug": slug or str(job.get("slug") or ""),
        "source": "gcs_job_status",
    }


def push_job_status(job: dict) -> bool:
    """Persist job status to local disk and GCS (when configured)."""
    run_id = str(job.get("run_id") or "").strip()
    if not run_id:
        return False
    payload = serialize_job_status(job)
    ok = False
    # Local always — enables localhost testing without Cloud Run
    try:
        path = _local_job_status_dir() / f"{run_id}.json"
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        ok = True
    except Exception as exc:  # pragma: no cover
        print(f"  [job-status] local write failed: {exc}", flush=True)
    if gcs_enabled():
        if upload_json_blob(_job_status_blob(run_id), payload):
            ok = True
        else:
            print(f"  [job-status] gcs write failed for {run_id}", flush=True)
    return ok


def pull_job_status(run_id: str) -> dict | None:
    """Load one job status (GCS first, then local)."""
    run_id = (run_id or "").strip()
    if not run_id:
        return None
    if gcs_enabled():
        raw = download_json_blob(_job_status_blob(run_id))
        if isinstance(raw, dict) and raw.get("run_id"):
            return raw
    try:
        path = _local_job_status_dir() / f"{run_id}.json"
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("run_id"):
                return raw
    except Exception as exc:  # pragma: no cover
        print(f"  [job-status] local read failed: {exc}", flush=True)
    return None


def list_job_statuses_for_owner(
    owner_email: str,
    *,
    max_age_hours: float = 8.0,
    stale_running_minutes: float = 5.0,
) -> list[dict]:
    """Newest-first durable jobs for one user (local + GCS).

    Marks long-stale ``running`` rows as ``interrupted`` (worker likely dead).
    """
    import time as _time

    email = (owner_email or "").strip().lower()
    if not email:
        return []
    by_id: dict[str, dict] = {}
    now = _time.time()
    max_age_s = max_age_hours * 3600.0
    stale_s = stale_running_minutes * 60.0

    def _ingest(raw: dict) -> None:
        if not isinstance(raw, dict):
            return
        rid = str(raw.get("run_id") or "").strip()
        if not rid:
            return
        if str(raw.get("owner_email") or "").strip().lower() != email:
            return
        updated = float(raw.get("updated_at") or raw.get("started_at") or 0)
        if updated and (now - updated) > max_age_s:
            return
        entry = dict(raw)
        if entry.get("running") and updated and (now - updated) > stale_s:
            entry["running"] = False
            entry["status"] = "interrupted"
            entry["error"] = entry.get("error") or (
                "Run looks interrupted (no progress update). "
                "Check Cloud storage files if it finished on another instance."
            )
        prev = by_id.get(rid)
        if not prev or float(entry.get("updated_at") or 0) >= float(prev.get("updated_at") or 0):
            by_id[rid] = entry

    # Local files
    try:
        for path in _local_job_status_dir().glob("*.json"):
            try:
                _ingest(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
    except Exception:
        pass

    # GCS
    if gcs_enabled():
        try:
            bucket = _client_bucket()
            for blob in bucket.list_blobs(prefix=JOB_STATUS_PREFIX):
                if not (blob.name or "").endswith(".json"):
                    continue
                try:
                    raw = json.loads(blob.download_as_text(encoding="utf-8"))
                    _ingest(raw if isinstance(raw, dict) else {})
                except Exception:
                    continue
        except Exception as exc:  # pragma: no cover
            print(f"  [job-status] list failed: {exc}", flush=True)

    return sorted(
        by_id.values(),
        key=lambda e: float(e.get("updated_at") or e.get("started_at") or 0),
        reverse=True,
    )


def status_to_registry_job(status: dict) -> dict:
    """Convert a durable status snapshot into an in-memory job dict for the UI."""
    import time as _time

    log = str(status.get("log_tail") or "")
    running = bool(status.get("running"))
    return {
        "run_id": str(status.get("run_id") or ""),
        "owner_email": str(status.get("owner_email") or ""),
        "running": running,
        "job_type": str(status.get("job_type") or "pipeline"),
        "query": str(status.get("query") or ""),
        "country": str(status.get("country") or "global"),
        "profile": str(status.get("profile") or ""),
        "cap": status.get("cap"),
        "log": log,
        "result": None,  # full result stays on disk/GCS exports
        "error": status.get("error"),
        "cancelled": bool(status.get("cancelled")),
        "cancel_requested": bool(status.get("cancel_requested")),
        "started_at": float(status.get("started_at") or _time.time()),
        "has_result": bool(status.get("has_result")),
        "slug": str(status.get("slug") or ""),
        "status": str(status.get("status") or ""),
        "phase": int(status.get("phase") or 0),
        "_from_durable_status": True,
        "updated_at": float(status.get("updated_at") or 0),
    }


def list_gcs_run_entries() -> list[dict]:
    """Discover finished runs from objects under runs/{slug}/ in the bucket.

    Older Cloud Run instances wrote Excel/Word/CSV to GCS but never wrote
    session_log rows — so Search history must read the bucket itself.
    """
    if not gcs_enabled():
        return []
    try:
        bucket = _client_bucket()
        # Collect per-slug file presence + newest update time
        by_slug: dict[str, dict] = {}
        for blob in bucket.list_blobs(prefix="runs/"):
            # runs/<slug>/<file>
            parts = (blob.name or "").split("/")
            if len(parts) < 3:
                continue
            slug = parts[1]
            if not slug:
                continue
            rec = by_slug.setdefault(
                slug,
                {
                    "slug": slug,
                    "has_xlsx": False,
                    "has_docx": False,
                    "has_csv": False,
                    "updated": None,
                },
            )
            fname = parts[-1].lower()
            if fname.endswith(".xlsx"):
                rec["has_xlsx"] = True
            elif fname.endswith(".docx"):
                rec["has_docx"] = True
            elif fname.endswith(".csv"):
                rec["has_csv"] = True
            updated = blob.updated
            if updated is not None:
                cur = rec["updated"]
                if cur is None or updated > cur:
                    rec["updated"] = updated

        out: list[dict] = []
        for slug, rec in by_slug.items():
            if not (rec["has_xlsx"] or rec["has_docx"] or rec["has_csv"]):
                continue
            # bamboo_toothbrushes__market_global -> query/country guess
            query = slug.replace("__", " ").replace("_", " ").strip()
            country = "global"
            low = slug.lower()
            for geo in (
                "united_states",
                "new_zealand",
                "azerbaijan",
                "uzbekistan",
                "europe",
                "india",
                "germany",
                "global",
            ):
                if low.endswith("_" + geo) or low.endswith("__" + geo):
                    country = geo.replace("_", " ")
                    query = slug[: -(len(geo) + 1)].replace("__", " ").replace("_", " ").strip()
                    break
            ran_at = ""
            if rec["updated"] is not None:
                ran_at = rec["updated"].isoformat()
            out.append(
                {
                    "run_id": f"gcs:{slug}",
                    "owner_email": "",  # pre-owner bucket artifacts — shared team history
                    "query": query or slug,
                    "country": country,
                    "status": "ok",
                    "slug": slug,
                    "csv_file": f"{slug}.csv" if rec["has_csv"] else "",
                    "xlsx_file": f"{slug}.xlsx" if rec["has_xlsx"] else "",
                    "docx_file": f"{slug}.docx" if rec["has_docx"] else "",
                    "source": "gcs",
                    "ran_at": ran_at,
                }
            )
        out.sort(key=lambda e: str(e.get("ran_at") or ""), reverse=True)
        return out
    except Exception as exc:  # pragma: no cover
        print(f"  [gcs] list runs failed: {exc}", flush=True)
        return []
