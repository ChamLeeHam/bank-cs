import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from src.nodes.jev_intent import JevError, _endpoint, classify
from src.scripts.compare_jev_intent import sample_rows
from src.scripts.compare_jev_shared_rules import apply_rules


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class JevTests(unittest.TestCase):
    def test_endpoint_accepts_provider_base_forms(self):
        for value in ("https://api.zhizengzeng.com", "https://api.zhizengzeng.com/v1",
                      "https://api.zhizengzeng.com/v1/systemone"):
            self.assertEqual(_endpoint(value), "https://api.zhizengzeng.com/v1/systemone")
        with self.assertRaises(JevError):
            _endpoint("http://example.com")

    @patch("src.nodes.jev_intent.request.urlopen")
    def test_choice_request_and_response(self, open_url):
        body = {"model": "jev-1.14", "answers": {"intent": {"type": "choice", "choice": "fees_rates",
                "confidence": 0.8, "probabilities": {"fees_rates": 0.8}}}, "usage": {"input_tokens": 42}}
        open_url.return_value = Response(json.dumps(body).encode())
        result = classify("PayNow fee?", api_key="test-secret", base_url="https://api.zhizengzeng.com", model="jev-latest")
        self.assertEqual(result["intent"], "fees_rates")
        self.assertEqual(result["model"], "jev-1.14")
        req = open_url.call_args.args[0]
        payload = json.loads(req.data)
        self.assertEqual(payload["state"]["customer_message"], "PayNow fee?")
        self.assertEqual(payload["questions"]["intent"]["type"], "choice")
        self.assertEqual(req.full_url, "https://api.zhizengzeng.com/v1/systemone")

    @patch("src.nodes.jev_intent.request.urlopen")
    def test_invalid_response_and_http_failure_do_not_fabricate_label(self, open_url):
        open_url.return_value = Response(b'{"answers":{"intent":{"choice":"made_up"}}}')
        with self.assertRaises(JevError):
            classify("hello", api_key="test", base_url="https://example.com")
        open_url.side_effect = HTTPError("https://example.com", 401, "bad", {}, None)
        with self.assertRaisesRegex(JevError, "HTTP 401"):
            classify("hello", api_key="test", base_url="https://example.com")

    def test_sample_is_deterministic_and_has_no_duplicates(self):
        rows = [{"id": str(i), "intent": "a" if i < 3 else "b", "lang": "zh"} for i in range(6)]
        picked = sample_rows(rows, 3)
        self.assertEqual(picked, sample_rows(rows, 3))
        self.assertEqual(len({r["id"] for r in picked}), 3)
        self.assertEqual(sample_rows(rows, 0), rows)

    def test_shared_rules_override_cached_jev_and_leave_others_unchanged(self):
        fraud = {"text": "I found an unauthorized card payment", "predicted_intent": "dispute_refund"}
        result = apply_rules(fraud)
        self.assertEqual((result["jev_rules_intent"], result["jev_rules_backend"]), ("fraud_report", "keyword"))
        ordinary = {"text": "What are the fees?", "predicted_intent": "fees_rates"}
        result = apply_rules(ordinary)
        self.assertEqual((result["jev_rules_intent"], result["jev_rules_backend"]), ("fees_rates", "jev_cached"))


if __name__ == "__main__":
    unittest.main()
