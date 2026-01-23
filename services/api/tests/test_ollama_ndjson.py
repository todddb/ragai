import json
from app.utils import ollama


class DummyResp:
    def __init__(self, text, headers=None):
        self.text = text
        self._headers = headers or {}

    @property
    def headers(self):
        return self._headers

    def json(self):
        # try to parse the whole text as JSON (simulate httpx.Response.json)
        return json.loads(self.text)


def test_parse_ndjson_stream_joining():
    # NDJSON example: model streams response fragments in "response" field
    nd_lines = [
        json.dumps({"model": "qwen2.5:latest", "response": "{", "done": False}),
        json.dumps({"model": "qwen2.5:latest", "response": "\"intent_label\"", "done": False}),
        json.dumps({"model": "qwen2.5:latest", "response": ": ", "done": False}),
        json.dumps({"model": "qwen2.5:latest", "response": "\"policy_question\"", "done": False}),
        json.dumps({"model": "qwen2.5:latest", "response": "}", "done": True}),
    ]
    resp_text = "\n".join(nd_lines)
    dummy = DummyResp(resp_text, headers={"content-type": "application/x-ndjson"})
    # call the helper logic via internal path
    parsed = ollama._parse_resp_text_and_join(dummy)
    assert parsed == '{"intent_label": "policy_question"}'


def test_parse_ndjson_heuristic_detection():
    # Test heuristic detection without explicit content-type header
    nd_lines = [
        json.dumps({"response": "Hello", "done": False}),
        json.dumps({"response": " World", "done": False}),
        json.dumps({"response": "!", "done": True}),
    ]
    resp_text = "\n".join(nd_lines)
    dummy = DummyResp(resp_text, headers={})
    parsed = ollama._parse_resp_text_and_join(dummy)
    assert parsed == "Hello World!"


def test_parse_non_ndjson_fallback():
    # Test non-NDJSON response with "text" field
    resp_text = '{"text": "This is a regular response"}'
    dummy = DummyResp(resp_text, headers={"content-type": "application/json"})
    parsed = ollama._parse_resp_text_and_join(dummy)
    assert parsed == "This is a regular response"
