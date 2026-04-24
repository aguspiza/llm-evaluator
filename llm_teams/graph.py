"""Microsoft Graph API client — thin wrapper over httpx, no external Graph SDK."""
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Optional

import httpx

_BASE = "https://graph.microsoft.com/v1.0"


class GraphClient:
    """Authenticated Graph API client built from an access token."""

    def __init__(self, access_token: str):
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: Optional[dict] = None, full_url: bool = False) -> dict:
        url = path if full_url else f"{_BASE}{path}"
        r = httpx.get(url, headers=self._headers, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = httpx.post(f"{_BASE}{path}", headers=self._headers, json=body, timeout=15)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str) -> None:
        r = httpx.delete(f"{_BASE}{path}", headers=self._headers, timeout=15)
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
        """Get the initial deltaLink for a channel's message feed.

        Calling this fetches all existing messages and returns a deltaLink.
        Subsequent calls to _get(deltaLink, full_url=True) return only new ones.
        """
        path = f"/teams/{team_id}/channels/{channel_id}/messages/delta"
        delta_link = None
        next_url: Optional[str] = f"{_BASE}{path}"

        # Drain all pages to reach the deltaLink (marks "start from now")
        while next_url:
            resp = self._get(next_url, full_url=True)
            delta_link = resp.get("@odata.deltaLink")
            next_url = resp.get("@odata.nextLink")

        return delta_link  # type: ignore[return-value]

    def chat_messages_delta_url(self, chat_id: str) -> str:
        """Get the initial deltaLink for a chat's message feed."""
        path = f"/chats/{chat_id}/messages/delta"
        delta_link = None
        next_url: Optional[str] = f"{_BASE}{path}"

        while next_url:
            resp = self._get(next_url, full_url=True)
            delta_link = resp.get("@odata.deltaLink")
            next_url = resp.get("@odata.nextLink")

        return delta_link  # type: ignore[return-value]

    def poll_delta(self, delta_url: str) -> tuple[list[dict], str]:
        """Poll a deltaLink. Returns (new_messages, updated_delta_url)."""
        all_msgs: list[dict] = []
        next_url: Optional[str] = delta_url
        new_delta: str = delta_url

        while next_url:
            resp = self._get(next_url, full_url=True)
            all_msgs.extend(resp.get("value", []))
            new_delta = resp.get("@odata.deltaLink", new_delta)
            next_url = resp.get("@odata.nextLink")

        # Filter out tombstone (deleted) messages
        active = [m for m in all_msgs if m.get("messageType") != "unknownFutureValue"]
        return active, new_delta

    # ---------------------------------------------------------------- #
    # Change notifications (webhook subscriptions)
    # ---------------------------------------------------------------- #

    def create_subscription(
        self,
        resource: str,
        notification_url: str,
        change_types: list[str] = None,
        expiration_minutes: int = 60,
    ) -> dict:
        """Register a Graph change notification subscription.

        resource: e.g. "/teams/{id}/channels/{id}/messages"
        notification_url: publicly reachable HTTPS endpoint (or localtunnel URL)
        """
        if change_types is None:
            change_types = ["created", "updated"]

        expiry = datetime.now(tz=timezone.utc)
        from datetime import timedelta
        expiry += timedelta(minutes=expiration_minutes)

        body = {
            "changeType": ",".join(change_types),
            "notificationUrl": notification_url,
            "resource": resource,
            "expirationDateTime": expiry.isoformat(),
            "clientState": "llm-teams",
        }
        return self._post("/subscriptions", body)

    def delete_subscription(self, subscription_id: str) -> None:
        self._delete(f"/subscriptions/{subscription_id}")

    def renew_subscription(self, subscription_id: str, expiration_minutes: int = 60) -> dict:
        from datetime import timedelta
        expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=expiration_minutes)
        r = httpx.patch(
            f"{_BASE}/subscriptions/{subscription_id}",
            headers=self._headers,
            json={"expirationDateTime": expiry.isoformat()},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

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
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                if chat_id:
                    msgs = self.list_chat_messages(chat_id, after_iso=after_iso)
                elif team_id and channel_id and message_id:
                    msgs = self.list_replies(team_id, channel_id, message_id, after_iso=after_iso)
                elif team_id and channel_id:
                    msgs = self.list_channel_messages(team_id, channel_id, after_iso=after_iso)
                else:
                    raise ValueError("Need either chat_id or (team_id + channel_id)")

                human_msgs = [
                    m for m in msgs
                    if bot_user_id is None
                    or m.get("from", {}).get("user", {}).get("id") != bot_user_id
                ]
                if human_msgs:
                    return sorted(human_msgs, key=lambda m: m.get("createdDateTime", ""))[0]

            except httpx.HTTPStatusError:
                pass

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

    Initialises the delta cursor at "now" (skips historical messages),
    then yields each new message dict as it appears.
    This is a blocking generator — run in a thread or async context if needed.
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
        except httpx.HTTPStatusError:
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
    import re
    content = message.get("body", {}).get("content", "")
    return re.sub(r"<[^>]+>", "", content).strip()


def _md_to_html(text: str) -> str:
    import re
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = text.replace("\n", "<br>")
    return text
