"""Tests for llm_teams.graph — Graph API client and utilities."""
import json

import httpx
import pytest
from pytest_httpx import HTTPXMock

from llm_teams.graph import GraphClient, extract_text, _md_to_html, delta_event_stream


TOKEN = "test-access-token"
BASE = "https://graph.microsoft.com/v1.0"


@pytest.fixture
def graph():
    return GraphClient(TOKEN)


# ------------------------------------------------------------------ #
# Utility functions
# ------------------------------------------------------------------ #

class TestExtractText:
    def test_plain_text(self):
        msg = {"body": {"content": "hello world"}}
        assert extract_text(msg) == "hello world"

    def test_strips_html_tags(self):
        msg = {"body": {"content": "<p>hello <b>world</b></p>"}}
        assert extract_text(msg) == "hello world"

    def test_empty_body(self):
        assert extract_text({}) == ""

    def test_strips_br(self):
        msg = {"body": {"content": "line1<br>line2"}}
        assert "line1" in extract_text(msg)
        assert "line2" in extract_text(msg)


class TestMdToHtml:
    def test_bold(self):
        assert "<strong>hi</strong>" in _md_to_html("**hi**")

    def test_italic(self):
        assert "<em>hi</em>" in _md_to_html("*hi*")

    def test_code(self):
        assert "<code>x</code>" in _md_to_html("`x`")

    def test_newline_to_br(self):
        assert "<br>" in _md_to_html("a\nb")

    def test_escapes_html_entities(self):
        result = _md_to_html("<script>alert('xss')</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_ampersand_escaped(self):
        assert "&amp;" in _md_to_html("a & b")


# ------------------------------------------------------------------ #
# GraphClient HTTP calls (mocked)
# ------------------------------------------------------------------ #

class TestGraphClientHeaders:
    def test_sends_auth_header(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(json={"id": "me-id"})
        graph.me()
        request = httpx_mock.get_requests()[0]
        assert request.headers["authorization"] == f"Bearer {TOKEN}"

    def test_sends_content_type(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(json={"id": "me-id"})
        graph.me()
        request = httpx_mock.get_requests()[0]
        assert "application/json" in request.headers["content-type"]


class TestMe:
    def test_returns_user_dict(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(json={"id": "u1", "displayName": "Alice"})
        result = graph.me()
        assert result["id"] == "u1"


class TestListJoinedTeams:
    def test_returns_list(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(json={"value": [{"id": "t1"}, {"id": "t2"}]})
        teams = graph.list_joined_teams()
        assert len(teams) == 2
        assert teams[0]["id"] == "t1"

    def test_empty_value(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(json={"value": []})
        assert graph.list_joined_teams() == []


class TestListChannels:
    def test_returns_channels(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(json={"value": [{"id": "c1", "displayName": "General"}]})
        chans = graph.list_channels("team-1")
        assert chans[0]["id"] == "c1"

    def test_requests_correct_path(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(json={"value": []})
        graph.list_channels("team-abc")
        assert "/teams/team-abc/channels" in str(httpx_mock.get_requests()[0].url)


class TestSendChannelMessage:
    def test_posts_to_correct_endpoint(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(json={"id": "msg-1"})
        graph.send_channel_message("t1", "c1", "Hello")
        req = httpx_mock.get_requests()[0]
        assert "/teams/t1/channels/c1/messages" in str(req.url)
        assert req.method == "POST"

    def test_body_contains_text(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(json={"id": "msg-1"})
        graph.send_channel_message("t1", "c1", "**bold** text")
        req = httpx_mock.get_requests()[0]
        body = json.loads(req.content)
        assert "<strong>bold</strong>" in body["body"]["content"]

    def test_subject_included_when_provided(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(json={"id": "msg-1"})
        graph.send_channel_message("t1", "c1", "hi", subject="My subject")
        body = json.loads(httpx_mock.get_requests()[0].content)
        assert body["subject"] == "My subject"

    def test_returns_sent_message(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(json={"id": "msg-99", "createdDateTime": "2024-01-01"})
        result = graph.send_channel_message("t1", "c1", "hi")
        assert result["id"] == "msg-99"


class TestSendChatMessage:
    def test_posts_to_chat_endpoint(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(json={"id": "chat-msg-1"})
        graph.send_chat_message("chat-123", "hello")
        req = httpx_mock.get_requests()[0]
        assert "/chats/chat-123/messages" in str(req.url)


class TestListChannelMessages:
    def test_returns_messages(self, httpx_mock: HTTPXMock, graph):
        msgs = [
            {"id": "1", "createdDateTime": "2024-01-02T00:00:00Z", "body": {"content": "a"}},
            {"id": "2", "createdDateTime": "2024-01-01T00:00:00Z", "body": {"content": "b"}},
        ]
        httpx_mock.add_response(json={"value": msgs})
        result = graph.list_channel_messages("t", "c")
        assert len(result) == 2

    def test_filters_after_iso(self, httpx_mock: HTTPXMock, graph):
        msgs = [
            {"id": "1", "createdDateTime": "2024-01-03T00:00:00Z"},
            {"id": "2", "createdDateTime": "2024-01-01T00:00:00Z"},
        ]
        httpx_mock.add_response(json={"value": msgs})
        result = graph.list_channel_messages("t", "c", after_iso="2024-01-02T00:00:00Z")
        assert len(result) == 1
        assert result[0]["id"] == "1"


class TestPollDelta:
    def test_returns_messages_and_new_delta(self, httpx_mock: HTTPXMock, graph):
        delta_url = f"{BASE}/teams/t/channels/c/messages/delta?$deltaToken=abc"
        httpx_mock.add_response(
            url=delta_url,
            json={
                "value": [{"id": "m1", "messageType": "message"}],
                "@odata.deltaLink": delta_url + "2",
            },
        )
        msgs, new_url = graph.poll_delta(delta_url)
        assert len(msgs) == 1
        assert new_url == delta_url + "2"

    def test_filters_tombstone_messages(self, httpx_mock: HTTPXMock, graph):
        delta_url = f"{BASE}/teams/t/channels/c/messages/delta?$deltaToken=x"
        httpx_mock.add_response(
            url=delta_url,
            json={
                "value": [
                    {"id": "m1", "messageType": "message"},
                    {"id": "m2", "messageType": "unknownFutureValue"},
                ],
                "@odata.deltaLink": delta_url,
            },
        )
        msgs, _ = graph.poll_delta(delta_url)
        assert len(msgs) == 1
        assert msgs[0]["id"] == "m1"

    def test_follows_next_link(self, httpx_mock: HTTPXMock, graph):
        page1_url = f"{BASE}/delta?token=p1"
        page2_url = f"{BASE}/delta?token=p2"
        httpx_mock.add_response(
            url=page1_url,
            json={"value": [{"id": "a", "messageType": "message"}], "@odata.nextLink": page2_url},
        )
        httpx_mock.add_response(
            url=page2_url,
            json={"value": [{"id": "b", "messageType": "message"}], "@odata.deltaLink": page2_url + "fin"},
        )
        msgs, final = graph.poll_delta(page1_url)
        assert {m["id"] for m in msgs} == {"a", "b"}
        assert final == page2_url + "fin"


class TestGetChannelMessage:
    def test_calls_correct_endpoint(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(json={"id": "msg-1"})
        graph.get_channel_message("t1", "c1", "msg-1")
        assert "/teams/t1/channels/c1/messages/msg-1" in str(httpx_mock.get_requests()[0].url)

    def test_returns_message(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(json={"id": "msg-1", "body": {"content": "hi"}})
        msg = graph.get_channel_message("t1", "c1", "msg-1")
        assert msg["id"] == "msg-1"


class TestGetChatMessage:
    def test_calls_correct_endpoint(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(json={"id": "msg-2"})
        graph.get_chat_message("chat-1", "msg-2")
        assert "/chats/chat-1/messages/msg-2" in str(httpx_mock.get_requests()[0].url)


class TestRenewSubscription:
    def test_patches_correct_endpoint(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(json={"id": "sub-1"})
        graph.renew_subscription("sub-1")
        req = httpx_mock.get_requests()[0]
        assert req.method == "PATCH"
        assert "/subscriptions/sub-1" in str(req.url)

    def test_sends_expiration_in_body(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(json={"id": "sub-1"})
        graph.renew_subscription("sub-1", expiration_minutes=30)
        body = json.loads(httpx_mock.get_requests()[0].content)
        assert "expirationDateTime" in body


class TestConnectionReuse:
    def test_client_is_persistent(self):
        g = GraphClient("tok")
        assert g._http is not None

    def test_context_manager_closes(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"id": "u1"})
        with GraphClient("tok") as g:
            g.me()
        assert g._http.is_closed


class TestHTTPErrors:
    def test_raises_on_4xx(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(status_code=401, json={"error": "Unauthorized"})
        with pytest.raises(httpx.HTTPStatusError):
            graph.me()

    def test_raises_on_5xx(self, httpx_mock: HTTPXMock, graph):
        httpx_mock.add_response(status_code=500)
        with pytest.raises(httpx.HTTPStatusError):
            graph.list_joined_teams()


class TestPollForReplyValidation:
    def test_raises_without_destination(self, graph):
        with pytest.raises(ValueError, match="chat_id"):
            graph.poll_for_reply(after_iso="2024-01-01T00:00:00Z")
