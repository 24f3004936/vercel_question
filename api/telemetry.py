from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any
import os
import math
import csv
import json

app = FastAPI(title="eShopCo Telemetry API")

# CORS for browser dashboards
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
)

class TelemetryRequest(BaseModel):
    regions: List[str]
    threshold_ms: float


def _mean(values: List[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _percentile_95(values: List[float]) -> float:
    """Nearest-rank p95."""
    if not values:
        return 0.0
    vals = sorted(values)
    n = len(vals)
    rank = math.ceil(0.95 * n)  # 1-based nearest-rank
    idx = max(0, min(n - 1, rank - 1))
    return float(vals[idx])


def _load_records() -> List[Dict[str, Any]]:
    """
    Loads telemetry from:
      - data/telemetry.csv  OR
      - data/telemetry.json

    Expected logical fields:
      region, latency_ms, uptime
    (with a few alias names supported)
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_path = os.path.join(base_dir, "data", "telemetry.csv")
    json_path = os.path.join(base_dir, "data", "telemetry.json")

    records: List[Dict[str, Any]] = []

    if os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                region = row.get("region") or row.get("Region")
                latency = (
                    row.get("latency_ms")
                    or row.get("latency")
                    or row.get("latencyMs")
                    or row.get("Latency")
                )
                uptime = (
                    row.get("uptime")
                    or row.get("uptime_pct")
                    or row.get("uptimePercent")
                    or row.get("Uptime")
                )

                if region is None or latency is None or uptime is None:
                    continue

                try:
                    records.append(
                        {
                            "region": str(region).strip().lower(),
                            "latency_ms": float(latency),
                            "uptime": float(uptime),
                        }
                    )
                except ValueError:
                    continue

        return records

    if os.path.exists(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        rows = payload.get("records", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise HTTPException(status_code=500, detail="Unsupported telemetry JSON format")

        for row in rows:
            region = row.get("region") or row.get("Region")
            latency = row.get("latency_ms") or row.get("latency") or row.get("latencyMs")
            uptime = row.get("uptime") or row.get("uptime_pct") or row.get("uptimePercent")

            if region is None or latency is None or uptime is None:
                continue

            try:
                records.append(
                    {
                        "region": str(region).strip().lower(),
                        "latency_ms": float(latency),
                        "uptime": float(uptime),
                    }
                )
            except (ValueError, TypeError):
                continue

        return records

    raise HTTPException(
        status_code=500,
        detail="No telemetry file found. Place telemetry.csv or telemetry.json in /data",
    )


@app.get("/")
def root():
    return {"ok": True, "message": "Use POST /api/telemetry"}


@app.post("/api/telemetry")
def telemetry_metrics(req: TelemetryRequest):
    records = _load_records()

    requested_regions = [r.strip().lower() for r in req.regions if str(r).strip()]
    if not requested_regions:
        raise HTTPException(status_code=400, detail="regions cannot be empty")

    requested_set = set(requested_regions)

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        if rec["region"] in requested_set:
            grouped.setdefault(rec["region"], []).append(rec)

    response: Dict[str, Any] = {}

    for region, rows in grouped.items():
        latencies = [r["latency_ms"] for r in rows]
        uptimes = [r["uptime"] for r in rows]
        breaches = sum(1 for r in rows if r["latency_ms"] > req.threshold_ms)

        response[region] = {
            "avg_latency": round(_mean(latencies), 3),
            "p95_latency": round(_percentile_95(latencies), 3),
            "avg_uptime": round(_mean(uptimes), 6),
            "breaches": breaches,
        }

    # Add missing requested regions with zero metrics (predictable response)
    for region in requested_set:
        if region not in response:
            response[region] = {
                "avg_latency": 0.0,
                "p95_latency": 0.0,
                "avg_uptime": 0.0,
                "breaches": 0,
            }

    return response
