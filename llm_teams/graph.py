"""Microsoft Graph API client — thin wrapper over httpx, no external Graph SDK."""
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

import httpx

_BASE = "https://graph.microsoft.com/v1.0"


class GraphClient:
    """Authenticated Graph API client built from an access token.

    Uses a persistent httpx.Client for connection reuse across requests.
    """

    def __init__(self, access_token: str):
        self._http = httpx.Client(
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ---------------------------------------------------------------- #
    # Private HTTP helpers
    # ---------------------------------------------------------------- #

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        r = self._http.get(f"{_BASE}{path}", params=params)
        r.raise_for_status()
        return r.json()

    def _get_url(self, url: str) -> dict:
        """GET an absolute URL (used for nextLink / deltaLink pagination)."""
        r = self._http.get(url)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = self._http.post(f"{_BASE}{path}", json=body)
        r.raise_for_status()
        return r.json()

    def _patch(self, path: str, body: dict) -> dict:
        r = self._http.patch(f"{_BASE}{path}", json=body)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> None:
        r = self._http.delete(f"{_BASE}{path}")
        r.raise_for_status()

    # ---------------------------------------------------------------- #
    # Identity
    # ---------------------------------------------------------------- #

    def me(self) -> dict:
        return self._get("/me")

    # ---------------------------------------------------------------- #
    # Teams
    # ---------------------------------------------------------------- #

    def list_joined_teams(self) -> list[dict]:
        return self._get("/me/joinedTeams").get("value", [])

    def list_channels(self, team_id: str) -> list[dict]:
        return self._get(f"/teams/{team_id}/channels").get("value", [])

    # ---------------------------------------------------------------- #
    # Channel messages
    # ---------------------------------------------------------------- #

    def send_channel_message(self, team_id: str, channel_id: str, text: str,
                             subject: Optional[str] = None) -> dict:
        body: dict[str, Any] = {
            "body": {"contentType": "html", "content": _md_to_html(text)},
        }
        if subject:
            body["subject"] = subject
        return self._post(f"/teams/{team_id}/channels/{channel_id}/messages", body)

    def list_channel_messages(self, team_id: str, channel_id: str,
                              after_iso: Optional[str] = None) -> list[dict]:
        params = {"$top": 20, "$orderby": "createdDateTime desc"}
        msgs = self._get(
            f"/teams/{team_id}/channels/{channel_id}/messages", params=params
        ).get("value", [])
        if after_iso:
            msgs = [m for m in msgs if m.get("createdDateTime", "") > after_iso]
        return msgs

    def get_channel_message(self, team_id: str, channel_id: str, message_id: str) -> dict:
        return self._get(f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}")

    def get_chat_message(self, chat_id: str, message_id: str) -> dict:
        return self._get(f"/chats/{chat_id}/messages/{message_id}")

    def list_replies(self, team_id: str, channel_id: str, message_id: str,
                     after_iso: Optional[str] = None) -> list[dict]:
        msgs = self._get(
            f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}/replies"
        ).get("value", [])
        if after_iso:
            msgs = [m for m in msgs if m.get("createdDateTime", "") > after_iso]
        return msgs

    # ---------------------------------------------------------------- #
    # Chat messages (1:1 or group chat)
    # ---------------------------------------------------------------- #

    def send_chat_message(self, chat_id: str, text: str) -> dict:
        body = {"body": {"contentType": "html", "content": _md_to_html(text)}}
        return self._post(f"/chats/{chat_id}/messages", body)

    def list_chat_messages(self, chat_id: str, after_iso: Optional[str] = None) -> list[dict]:
        params = {"$top": 20}
        msgs = self._get(f"/chats/{chat_id}/messages", params=params).get("value", [])
        if after_iso:
            msgs = [m for m in msgs if m.get("createdDateTime", "") > after_iso]
        return msgs

    # ---------------------------------------------------------------- #
    # Delta queries — incremental change feed (efficient polling)
    # ---------------------------------------------------------------- #

    def channel_messages_delta_url(self, team_id: str, channel_id: str) -> str:
        """Drain the channel message history and return the current deltaLink.

        Subsequent calls to poll_delta() return only messages newer than this point.
        """
        return self._drain_to_delta(f"{_BASE}/teams/{team_id}/channels/{channel_id}/messages/delta")

    def chat_messages_delta_url(self, chat_id: str) -> str:
        """Drain the chat message history and return the current deltaLink."""
        return self._drain_to_delta(f"{_BASE}/chats/{chat_id}/messages/delta")

    def _drain_to_delta(self, initial_url: str) -> str:
        """Page through all existing results until we get a deltaLink."""
        delta_link: Optional[str] = None
        next_url: Optional[str] = initial_url
        while next_url:
            resp = self._get_url(next_url)
            delta_link = resp.get("@odata.deltaLink") or delta_link
            next_url = resp.get("@odata.nextLink")
        assert delta_link, "Graph did not return a deltaLink"
        return delta_link

    def poll_delta(self, delta_url: str) -> tuple[list[dict], str]:
        """Poll a deltaLink. Returns (new_messages, updated_delta_url).

        Follows nextLinks automatically and filters out deleted-message tombstones.
        """
        all_msgs: list[dict] = []
        new_delta = delta_url
        next_url: Optional[str] = delta_url

        while next_url:
            resp = self._get_url(next_url)
            all_msgs.extend(resp.get("value", []))
            new_delta = resp.get("@odata.deltaLink", new_delta)
            next_url = resp.get("@odata.nextLink")

        active = [m for m in all_msgs if m.get("messageType") != "unknownFutureValue"]
        return active, new_delta

    # ---------------------------------------------------------------- #
    # Change notification subscriptions
    # ---------------------------------------------------------------- #

    def create_subscription(
        self,
        resource: str,
        notification_url: str,
        change_types: Optional[list[str]] = None,
        expiration_minutes: int = 60,
    ) -> dict:
        """Register a Graph change notification subscription.

        resource: e.g. "/teams/{id}/channels/{id}/messages"
        notification_url: publicly reachable HTTPS endpoint (or ngrok URL)
        """
        expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=expiration_minutes)
        body = {
            "changeType": ",".join(change_types or ["created", "updated"]),
            "notificationUrl": notification_url,
            "resource": resource,
            "expirationDateTime": expiry.isoformat(),
            "clientState": "llm-teams",
        }
        return self._post("/subscriptions", body)

    def delete_subscription(self, subscription_id: str) -> None:
        self._delete(f"/subscriptions/{subscription_id}")

    def renew_subscription(self, subscription_id: str, expiration_minutes: int = 60) -> dict:
        expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=expiration_minutes)
        return self._patch(
            f"/subscriptions/{subscription_id}",
            {"expirationDateTime": expiry.isoformat()},
        )

    # ---------------------------------------------------------------- #
    # One-shot reply polling (for ask-human / confirm)
    # ---------------------------------------------------------------- #

    def poll_for_reply(
        self,
        *,
        team_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        message_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        after_iso: str,
        poll_interval: int = 5,
        timeout: int = 300,
        bot_user_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Block until a human replies after `after_iso` or timeout elapses."""
        if not chat_id and not (team_id and channel_id):
            raise ValueError("Need either chat_id or (team_id + channel_id)")

        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                if chat_id:
                    msgs = self.list_chat_messages(chat_id, after_iso=after_iso)
                elif message_id:
                    msgs = self.list_replies(team_id, channel_id, message_id, after_iso=after_iso)
                else:
                    msgs = self.list_channel_messages(team_id, channel_id, after_iso=after_iso)

                human_msgs = [
                    m for m in msgs
                    if bot_user_id is None
                    or m.get("from", {}).get("user", {}).get("id") != bot_user_id
                ]
                if human_msgs:
                    return sorted(human_msgs, key=lambda m: m.get("createdDateTime", ""))[0]

            except (httpx.HTTPStatusError, httpx.RequestError):
                pass  # transient network error — keep polling

            time.sleep(poll_interval)

        return None


# ---------------------------------------------------------------- #
# Delta event stream generator
# ---------------------------------------------------------------- #

def delta_event_stream(
    graph: GraphClient,
    *,
    team_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    poll_interval: int = 5,
    skip_user_id: Optional[str] = None,
) -> Iterator[dict]:
    """Yield new Graph messages as they arrive, using delta queries.

    Initialises the cursor at "now" (skips historical messages), then yields
    each new message as it appears. Blocking generator — run in a thread if needed.
    """
    if chat_id:
        delta_url = graph.chat_messages_delta_url(chat_id)
    elif team_id and channel_id:
        delta_url = graph.channel_messages_delta_url(team_id, channel_id)
    else:
        raise ValueError("Need chat_id or (team_id + channel_id)")

    while True:
        time.sleep(poll_interval)
        try:
            msgs, delta_url = graph.poll_delta(delta_url)
        except (httpx.HTTPStatusError, httpx.RequestError):
            continue  # transient — retry next interval

        for msg in sorted(msgs, key=lambda m: m.get("createdDateTime", "")):
            if skip_user_id and msg.get("from", {}).get("user", {}).get("id") == skip_user_id:
                continue
            yield msg


# ---------------------------------------------------------------- #
# Utility
# ---------------------------------------------------------------- #

def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def extract_text(message: dict) -> str:
    """Extract plain text from a Graph message body (strips HTML tags)."""
    content = message.get("body", {}).get("content", "")
    return re.sub(r"<[^>]+>", "", content).strip()


def _md_to_html(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text.replace("\n", "<br>")
