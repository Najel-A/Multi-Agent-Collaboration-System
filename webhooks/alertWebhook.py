import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import FastAPI, Request
from kafka import KafkaProducer

app = FastAPI()

KAFKA_BOOTSTRAP = os.getenv(
    "KAFKA_BOOTSTRAP",
    "kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092",
)
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "k8s-alerts")

producer = KafkaProducer(
    bootstrap_servers=KAFKA_BOOTSTRAP,
    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    key_serializer=lambda v: v.encode("utf-8") if v else None,
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def send_to_kafka(message: Dict[str, Any]) -> None:
    key = f"{message.get('source')}:{message.get('namespace')}:{message.get('alertname')}:{message.get('pod')}"
    producer.send(KAFKA_TOPIC, key=key, value=message)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/alert/prometheus")
async def prometheus_alert(request: Request):
    payload = await request.json()
    alerts: List[Dict[str, Any]] = payload.get("alerts", [])

    for alert in alerts:
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})

        message = {
            "source": "prometheus",
            "received_at": now_utc(),
            "status": alert.get("status"),
            "alertname": labels.get("alertname"),
            "severity": labels.get("severity"),
            "namespace": labels.get("namespace"),
            "pod": labels.get("pod"),
            "container": labels.get("container"),
            "reason": labels.get("reason"),
            "summary": annotations.get("summary"),
            "description": annotations.get("description"),
            "startsAt": alert.get("startsAt"),
            "endsAt": alert.get("endsAt"),
            "raw": payload,
        }

        send_to_kafka(message)

    producer.flush()

    return {
        "status": "sent_to_kafka",
        "source": "prometheus",
        "topic": KAFKA_TOPIC,
        "alerts_sent": len(alerts),
    }


@app.post("/alert/elastic")
async def elastic_alert(request: Request):
    payload = await request.json()

    rule = payload.get("rule", {})
    alert = payload.get("alert", {})
    context = payload.get("context", {})
    labels = payload.get("labels", {})

    message = {
        "source": "elastic",
        "received_at": now_utc(),
        "status": alert.get("status") or payload.get("status"),
        "alertname": rule.get("name") or payload.get("rule_name"),
        "severity": labels.get("severity") or payload.get("severity"),
        "namespace": labels.get("namespace") or context.get("namespace"),
        "pod": labels.get("pod") or context.get("pod"),
        "container": labels.get("container") or context.get("container"),
        "reason": context.get("reason"),
        "summary": context.get("title") or payload.get("summary"),
        "description": context.get("message") or context.get("reason") or payload.get("message"),
        "startsAt": alert.get("start") or payload.get("date"),
        "endsAt": alert.get("end"),
        "raw": payload,
    }

    send_to_kafka(message)
    producer.flush()

    return {
        "status": "sent_to_kafka",
        "source": "elastic",
        "topic": KAFKA_TOPIC,
        "alerts_sent": 1,
    }