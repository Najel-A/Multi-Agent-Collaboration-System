import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import FastAPI, Request
from kafka import KafkaProducer

app = FastAPI()

KAFKA_BOOTSTRAP = os.getenv(
    "KAFKA_BOOTSTRAP",
    "mas-kafka-kafka-bootstrap.kafka.svc.cluster.local:9092",
)
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "alert-topic")

producer = KafkaProducer(
    bootstrap_servers=KAFKA_BOOTSTRAP,
    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    key_serializer=lambda v: v.encode("utf-8") if v else None,
)

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def detect_source(payload: Dict[str, Any]) -> str:
    if "alerts" in payload and "groupLabels" in payload:
        return "prometheus"
    if "rule" in payload or "context" in payload:
        return "elastic"
    return "unknown"

def build_key(payload: Dict[str, Any], source: str) -> str:
    if source == "prometheus" and payload.get("alerts"):
        labels = payload["alerts"][0].get("labels", {})
        return f"{source}:{labels.get('namespace')}:{labels.get('alertname')}:{labels.get('pod')}"
    return f"{source}:{now_utc()}"

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/alert")
async def receive_alert(request: Request):
    payload = await request.json()
    source = detect_source(payload)

    message = {
        "source": source,
        "received_at": now_utc(),
        "raw": payload,
    }

    key = build_key(payload, source)

    producer.send(KAFKA_TOPIC, key=key, value=message)
    producer.flush()

    return {
        "status": "sent_to_kafka",
        "source": source,
        "topic": KAFKA_TOPIC,
    }