

class TestGreetingForCaller:
    def _state(self, number: str) -> dict[str, Any]:
        return {
            "voicemail": {"caller_number": number},
            "caller_snapshot": caller_snapshot.fresh_snapshot(),
        }

    def test_known_caller_returns_name_and_fills_snapshot(self, monkeypatch):
        import persona_tools

        monkeypatch.setattr(
            contacts,
            "lookup",
            lambda n: {"name": "Sam Rivera", "relationship": "Work", "source": "vcard"},
        )
        state = self._state("+14155550142")
        name = persona_tools.greeting_for_caller(state)
        assert name == "Sam Rivera"
        assert state["caller_snapshot"]["known_caller"] is True
        assert state["caller_snapshot"]["caller_name"] == "Sam Rivera"
        assert state["caller_snapshot"]["relationship"] == "Work"

    def test_unknown_caller_returns_none(self, monkeypatch):
        import persona_tools

        monkeypatch.delenv("OWNER_PHONE_NUMBER", raising=False)
        monkeypatch.setattr(contacts, "lookup", lambda n: None)
        state = self._state("+14155550100")
        assert persona_tools.greeting_for_caller(state) is None
        assert state["caller_snapshot"]["known_caller"] is False

    def test_webrtc_no_number_returns_none(self, monkeypatch):
        import persona_tools

        monkeypatch.delenv("OWNER_PHONE_NUMBER", raising=False)
        state = self._state("")
        assert persona_tools.greeting_for_caller(state) is None
