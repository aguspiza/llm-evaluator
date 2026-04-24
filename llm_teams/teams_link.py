"""Parse Microsoft Teams deep links into (team_id, channel_id, chat_id)."""
from urllib.parse import unquote, urlparse, parse_qs
from typing import Optional
from dataclasses import dataclass


@dataclass
class TeamsLink:
    team_id: Optional[str] = None
    channel_id: Optional[str] = None
    chat_id: Optional[str] = None

    @property
    def is_channel(self) -> bool:
        return bool(self.team_id and self.channel_id)

    @property
    def is_chat(self) -> bool:
        return bool(self.chat_id)


def parse(url: str) -> TeamsLink:
    """Parse a Teams deep link URL and return a TeamsLink.

    Supported formats
    -----------------
    Channel link (from "Get link to channel"):
      https://teams.microsoft.com/l/channel/<channel_id>/...?groupId=<team_id>&...

    Team link:
      https://teams.microsoft.com/l/team/<team_id>/...

    Chat link:
      https://teams.microsoft.com/l/chat/<chat_id>/...

    Also handles the msteams:// scheme and short teams.live.com URLs.
    """
    url = url.strip()

    # Normalise msteams:// → https://teams.microsoft.com/
    if url.startswith("msteams://"):
        url = "https://teams.microsoft.com/" + url[len("msteams://"):]

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    # Path looks like /l/<type>/<id>/...
    parts = [p for p in parsed.path.split("/") if p]
    # parts: ['l', 'channel', '<id>', ...]  or ['l', 'team', '<id>'] etc.

    if len(parts) < 3 or parts[0] != "l":
        raise ValueError(
            f"Unrecognised Teams link format.\n"
            f"Expected: https://teams.microsoft.com/l/channel/<id>/...?groupId=<team-id>\n"
            f"Got: {url}"
        )

    link_type = parts[1].lower()
    raw_id = unquote(parts[2])

    if link_type == "channel":
        team_id = (qs.get("groupId") or qs.get("groupid") or [None])[0]
        if not team_id:
            raise ValueError(
                "Channel link is missing the `groupId` query parameter.\n"
                "Copy the link again from Teams → right-click channel → Get link to channel."
            )
        return TeamsLink(team_id=team_id, channel_id=raw_id)

    if link_type == "team":
        return TeamsLink(team_id=raw_id)

    if link_type == "chat":
        return TeamsLink(chat_id=raw_id)

    raise ValueError(
        f"Unsupported Teams link type '{link_type}'. "
        "Supported: channel, team, chat."
    )
