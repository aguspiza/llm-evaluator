"""Microsoft Graph API client — thin wrapper over httpx, no external Graph SDK."""
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

_BASE = "https://graph.microsoft.com/v1.0"


class GraphClient:
    """Authenticated Graph API client built from an access token."""

    def __init__(self, access_token: str):
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        r = httpx.get(f"{_BASE}{path}", headers=self._headers, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = httpx.post(f"{_BASE}{path}", headers=self._headers, json=body, timeout=15)
        r.raise_for_status()
        return r.json()

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
    # Polling helpers
    # ---------------------------------------------------------------- #

    def poll_for_reply(
        self,
        *,
        team_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        message_id: Optional[str] = None,  # poll replies to a specific message
        chat_id: Optional[str] = None,
        after_iso: str,
        poll_interval: int = 5,
        timeout: int = 300,
        bot_user_id: Optional[str] = None,  # skip messages from the bot itself
    ) -> Optional[dict]:
        """Block until a human replies after `after_iso` or timeout elapses.

        Returns the first new message dict, or None on timeout.
        """
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

                # Filter out messages from the bot
                human_msgs = [
                    m for m in msgs
                    if bot_user_id is None
                    or m.get("from", {}).get("user", {}).get("id") != bot_user_id
                ]
                if human_msgs:
                    # Return the oldest new message
                    return sorted(human_msgs, key=lambda m: m.get("createdDateTime", ""))[0]

            except httpx.HTTPStatusError:
                pass  # transient error — keep polling

            time.sleep(poll_interval)

        return None


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
    """Minimal markdown → HTML for bold/italic/code so Teams renders them nicely."""
    import re
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = text.replace("\n", "<br>")
    return text
