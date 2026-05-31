"""Hermetic tests for cloud-backed persona storage."""

from __future__ import annotations

import json

import persona_cloud


class _FakeTable:
    def __init__(self, item=None):
        self.item = item
        self.written = None

    def put_item(self, *, Item):
        self.written = Item

    def get_item(self, *, Key):
        return {"Item": self.item} if self.item else {}


class _FakeDynamo:
    def __init__(self, table):
        self.table = table

    def Table(self, _name):
        return self.table


class _FakeBody:
    def __init__(self, value):
        self.value = value

    def read(self):
        return self.value


class _FakeS3:
    def __init__(self, item=None):
        self.item = item
        self.written = None

    def put_object(self, **kwargs):
        self.written = kwargs

    def get_object(self, **_kwargs):
        return {"Body": _FakeBody(json.dumps(self.item).encode("utf-8"))}


class _FakeSession:
    def __init__(self, table, s3=None):
        self.table = table
        self.s3 = s3

    def resource(self, name):
        assert name == "dynamodb"
        return _FakeDynamo(self.table)

    def client(self, name):
        assert name == "s3"
        return self.s3


def test_publish_persona_overwrites_fixed_dynamo_record(monkeypatch):
    table = _FakeTable()
    monkeypatch.setattr(persona_cloud, "_session", lambda: _FakeSession(table))
    monkeypatch.setenv("PERSONA_CLOUD_RECORD_ID", "persona-context:test")

    where = persona_cloud.publish_persona("# Persona\n\n- concise")

    assert where == "dynamodb:ff-voicemails/persona-context:test"
    assert table.written is not None
    assert table.written["record_id"] == "persona-context:test"
    assert table.written["type"] == "persona_context"
    assert table.written["content"] == "# Persona\n\n- concise"


def test_fetch_persona_reads_dynamo(monkeypatch):
    table = _FakeTable(
        {"record_id": "persona-context:test", "type": "persona_context", "content": "remote"}
    )
    monkeypatch.setattr(persona_cloud, "_session", lambda: _FakeSession(table))
    monkeypatch.setattr(persona_cloud, "cloud_reads_enabled", lambda: True)

    assert persona_cloud.fetch_persona() == "remote"


def test_fetch_persona_scans_when_get_item_is_denied(monkeypatch):
    class _ScanFallbackTable:
        def get_item(self, **_kwargs):
            raise RuntimeError("GetItem denied")

        def scan(self, **_kwargs):
            return {"Items": [{"type": "persona_context", "content": "remote via scan"}]}

    monkeypatch.setattr(
        persona_cloud,
        "_session",
        lambda: _FakeSession(_ScanFallbackTable()),
    )
    monkeypatch.setattr(persona_cloud, "cloud_reads_enabled", lambda: True)

    assert persona_cloud.fetch_persona() == "remote via scan"


def test_fetch_persona_can_be_disabled(monkeypatch):
    monkeypatch.setattr(persona_cloud, "cloud_reads_enabled", lambda: False)
    monkeypatch.setattr(persona_cloud, "_session", lambda: (_ for _ in ()).throw(AssertionError))

    assert persona_cloud.fetch_persona() is None


def test_publish_persona_falls_back_to_s3(monkeypatch):
    class _BrokenTable:
        def put_item(self, **_kwargs):
            raise RuntimeError("ddb unavailable")

    s3 = _FakeS3()
    monkeypatch.setattr(persona_cloud, "_session", lambda: _FakeSession(_BrokenTable(), s3))
    monkeypatch.setenv("PERSONA_CLOUD_S3_BUCKET", "persona-bucket")

    where = persona_cloud.publish_persona("remote")

    assert where is not None
    assert where.startswith("s3:persona-bucket/personas/")
    assert s3.written is not None
    assert json.loads(s3.written["Body"])["content"] == "remote"
