from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any
import os
import json
import math

app = FastAPI()

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


def mean(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def p95(vals: List[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    # nearest-rank percentile
    rank = math.ceil(0.95 * n)
    idx = max(0, min(n - 1, rank - 1))
    return float(s[idx])


def load_records() -> List[Dict[str, Any]]:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "data", "q-vercel-latency.json")

    if not os.path.exists(path):
        # fallback names
        for alt in ["telemetry.json", "telemetry.csv"]:
            p = os.path.join(base, "data", alt)
            if os.path.exists(p):
                path = p
                break

    if not os.path.exists(path):
        raise HTTPException(status_code=500, detail="Telemetry file not found in /data")

    # JSON only for your file q-vercel-latency.json
    if path.endswith(".json"):
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        # supports either [{"...": ...}] or {"records":[...]}
        rows = payload.get("records", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise HTTPException(status_code=500, detail="Unsupported telemetry JSON format")

        records = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            region = r.get("region") or r.get("Region")
            latency = r.get("latency_ms") or r.get("latency") or r.get("latencyMs")
            uptime = r.get("uptime") or r.get("uptime_pct") or r.get("uptimePercent")

            if region is None or latency is None or uptime is None:
                continue

            try:
                records.append({
                    "region": str(region).strip().lower(),
                    "latency_ms": float(latency),
                    "uptime": float(uptime),
                })
            except Exception:
                continue
        return records

    # If CSV fallback is used
    import csv
    records = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            region = r.get("region") or r.get("Region")
            latency = r.get("latency_ms") or r.get("latency") or r.get("latencyMs")
            uptime = r.get("uptime") or r.get("uptime_pct") or r.get("uptimePercent")
            if region is None or latency is None or uptime is None:
                continue
            try:
                records.append({
                    "region": str(region).strip().lower(),
                    "latency_ms": float(latency),
                    "uptime": float(uptime),
                })
            except Exception:
                continue
    return records


@app.get("/")
def root():
    return {"ok": True}


@app.post("/api/telemetry")
def telemetry(req: TelemetryRequest):
    records = load_records()

    requested = {str(x).strip().lower() for x in req.regions if str(x).strip()}
    if not requested:
        raise HTTPException(status_code=400, detail="regions cannot be empty")

    out: Dict[str, Any] = {}
    for region in requested:
        rows = [r for r in records if r["region"] == region]
        if not rows:
            out[region] = {
                "avg_latency": 0.0,
                "p95_latency": 0.0,
                "avg_uptime": 0.0,
                "breaches": 0,
            }
            continue

        lats = [r["latency_ms"] for r in rows]
        ups = [r["uptime"] for r in rows]
        breaches = sum(1 for r in rows if r["latency_ms"] > req.threshold_ms)

        out[region] = {
            "avg_latency": round(mean(lats), 3),
            "p95_latency": round(p95(lats), 3),
            "avg_uptime": round(mean(ups), 6),
            "breaches": breaches,
        }

    return out
