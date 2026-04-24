"""Tests for llm_teams.teams_link — Teams URL parsing."""
import pytest
from llm_teams.teams_link import parse, TeamsLink


CHANNEL_URL = (
    "https://teams.microsoft.com/l/channel/"
    "19%3Aabc123%40thread.skype"
    "/General?groupId=team-guid-456&tenantId=tenant-789"
)
CHANNEL_URL_ENCODED_SLASH = (
    "https://teams.microsoft.com/l/channel/"
    "19%3Aabc%2F123%40thread.skype"
    "/My%20Channel?groupId=team-guid&tenantId=t"
)
TEAM_URL = "https://teams.microsoft.com/l/team/team-guid-xyz/join?foo=bar"
CHAT_URL = "https://teams.microsoft.com/l/chat/19%3Achat-id%40thread.v2/0"
MSTEAMS_URL = (
    "msteams://teams.microsoft.com/l/channel/"
    "19%3Aabc%40thread.skype/Gen?groupId=gid&tenantId=tid"
)


class TestChannelLink:
    def test_parses_team_id(self):
        link = parse(CHANNEL_URL)
        assert link.team_id == "team-guid-456"

    def test_parses_channel_id_url_decoded(self):
        link = parse(CHANNEL_URL)
        assert link.channel_id == "19:abc123@thread.skype"

    def test_is_channel(self):
        assert parse(CHANNEL_URL).is_channel is True

    def test_is_not_chat(self):
        assert parse(CHANNEL_URL).is_chat is False

    def test_channel_id_with_slash_decoded(self):
        link = parse(CHANNEL_URL_ENCODED_SLASH)
        assert "/" in link.channel_id

    def test_missing_group_id_raises(self):
        url = "https://teams.microsoft.com/l/channel/19%3Axxx%40thread.skype/General"
        with pytest.raises(ValueError, match="groupId"):
            parse(url)


class TestTeamLink:
    def test_parses_team_id(self):
        link = parse(TEAM_URL)
        assert link.team_id == "team-guid-xyz"

    def test_channel_id_is_none(self):
        assert parse(TEAM_URL).channel_id is None

    def test_is_not_channel(self):
        assert parse(TEAM_URL).is_channel is False


class TestChatLink:
    def test_parses_chat_id(self):
        link = parse(CHAT_URL)
        assert link.chat_id == "19:chat-id@thread.v2"

    def test_is_chat(self):
        assert parse(CHAT_URL).is_chat is True

    def test_team_id_is_none(self):
        assert parse(CHAT_URL).team_id is None


class TestMsteamsScheme:
    def test_msteams_scheme_normalised(self):
        link = parse(MSTEAMS_URL)
        assert link.team_id == "gid"
        assert link.channel_id == "19:abc@thread.skype"


class TestInvalidLinks:
    def test_empty_raises(self):
        with pytest.raises(ValueError):
            parse("")

    def test_non_teams_url_raises(self):
        with pytest.raises(ValueError):
            parse("https://example.com/foo/bar")

    def test_unknown_link_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported"):
            parse("https://teams.microsoft.com/l/unknowntype/some-id/")

    def test_whitespace_stripped(self):
        link = parse("  " + CHANNEL_URL + "  ")
        assert link.team_id == "team-guid-456"
