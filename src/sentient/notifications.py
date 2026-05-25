from __future__ import annotations

import json
import smtplib
import urllib.request
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol


class Notifier(Protocol):
    def notify(self, event_type: str, payload: dict[str, Any]) -> None:
        raise NotImplementedError


@dataclass
class InMemoryNotifier:
    events: list[dict[str, Any]] = field(default_factory=list)

    def notify(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"event_type": event_type, "payload": payload})


@dataclass(frozen=True)
class FileNotifier:
    path: Path

    def notify(self, event_type: str, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event_type": event_type, "payload": payload}))
            handle.write("\n")


@dataclass(frozen=True)
class WebhookNotifier:
    url: str

    def notify(self, event_type: str, payload: dict[str, Any]) -> None:
        _post_json(self.url, {"event_type": event_type, "payload": payload})


@dataclass(frozen=True)
class SlackWebhookNotifier:
    url: str
    username: str = "Sentient"

    def notify(self, event_type: str, payload: dict[str, Any]) -> None:
        _post_json(
            self.url,
            {
                "username": self.username,
                "text": _format_slack_message(event_type, payload),
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": _format_slack_message(event_type, payload),
                        },
                    }
                ],
            },
        )


@dataclass(frozen=True)
class EmailNotifier:
    host: str
    port: int
    sender: str
    recipients: tuple[str, ...]
    username: str | None = None
    password: str | None = None
    use_tls: bool = True

    def notify(self, event_type: str, payload: dict[str, Any]) -> None:
        message = EmailMessage()
        message["Subject"] = f"Sentient alert: {event_type}"
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message.set_content(json.dumps({"event_type": event_type, "payload": payload}, indent=2))
        with smtplib.SMTP(self.host, self.port, timeout=10) as smtp:
            if self.use_tls:
                smtp.starttls()
            if self.username and self.password:
                smtp.login(self.username, self.password)
            smtp.send_message(message)


def _post_json(url: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5):
        return


def _format_slack_message(event_type: str, payload: dict[str, Any]) -> str:
    summary = payload.get("reason") or payload.get("decision_summary") or payload.get("tool_name") or ""
    subject = payload.get("agent_id") or payload.get("request_id") or "system"
    if summary:
        return f"*{event_type}* for `{subject}`: {summary}"
    return f"*{event_type}* for `{subject}`"
