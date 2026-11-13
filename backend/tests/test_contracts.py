import asyncio
import json
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.Agent.SummarizerAgent import SummarizerAgent
from src.Infrastructure.Citations import build_citation_index, renumber_markers
from src.Infrastructure.Config import Settings
from src.Infrastructure.LLM import LLMClient, detect_capabilities
from src.Infrastructure.NoteStore import NoteStore
from src.Schema.Claim import Claim
from src.Schema.Source import Source
from src.Schema.Summary import Summary
from src.Schema.Task import Task
from src.WorkFlow.ResearchWorkFlow import ResearchWorkFlow


def setUpModule() -> None:
    logging.disable(logging.CRITICAL)


def tearDownModule() -> None:
    logging.disable(logging.NOTSET)


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, llm_api_key="test", **overrides)


class ModelCapabilityTest(unittest.TestCase):
    def test_reasoning_models_omit_temperature(self):
        client = LLMClient(make_settings(llm_model_id="deepseek-reasoner"))

        self.assertTrue(client.capabilities.is_reasoning)
        self.assertFalse(client.capabilities.supports_temperature)
        payload = client._build_payload("system", "user", 0.2, None)
        self.assertNotIn("temperature", payload)

    def test_ordinary_models_still_receive_temperature(self):
        client = LLMClient(make_settings(llm_model_id="gpt-4o-mini"))

        self.assertFalse(client.capabilities.is_reasoning)
        payload = client._build_payload("system", "user", 0.2, None)
        self.assertEqual(payload["temperature"], 0.2)

    def test_overrides_pin_capabilities_instead_of_guessing(self):
        capabilities = detect_capabilities(
            make_settings(llm_model_id="gpt-4o-mini", llm_is_reasoning="true", llm_supports_temperature="true")
        )

        self.assertTrue(capabilities.is_reasoning)
        self.assertTrue(capabilities.supports_temperature)

    def test_capabilities_are_downgraded_immutably(self):
        capabilities = detect_capabilities(make_settings())
        downgraded = capabilities.without("supports_json_schema")

        self.assertTrue(capabilities.supports_json_schema)
        self.assertFalse(downgraded.supports_json_schema)
        with self.assertRaises(KeyError):
            capabilities.without("not_a_capability")

    def test_client_downgrades_structured_output_when_provider_rejects_it(self):
        client = LLMClient(make_settings(llm_model_id="gpt-4o-mini"))
        rejection = RuntimeError("Unsupported parameter: 'response_format' is not supported with this model.")
        rejection.status_code = 400
        recovered = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"summary": "ok"}'))])
        create = AsyncMock(side_effect=[rejection, recovered])
        client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

        result = asyncio.run(
            client.invoke("system", "user", json_schema={"name": "x", "schema": {"type": "object", "properties": {}}})
        )

        self.assertEqual(result, '{"summary": "ok"}')
        self.assertFalse(client.capabilities.supports_json_schema)
        first_call, retry_call = create.await_args_list[0].kwargs, create.await_args_list[1].kwargs
        self.assertIn("response_format", first_call)
        self.assertNotIn("response_format", retry_call)

    def test_transient_failure_is_not_mistaken_for_missing_capability(self):
        client = LLMClient(make_settings())
        failure = RuntimeError("unavailable")
        failure.status_code = 503
        create = AsyncMock(side_effect=failure)
        client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

        with self.assertRaises(RuntimeError):
            asyncio.run(client.invoke("system", "user"))

        self.assertTrue(client.capabilities.supports_json_schema)
        self.assertIn("暂时不可用", client.last_error)


class ClaimTest(unittest.TestCase):
    def test_declared_and_inline_citations_are_merged_into_source_ids(self):
        sources = [Source(title="A", url="https://a.example"), Source(title="B", url="https://b.example")]

        claims = SummarizerAgent.build_claims(
            {"summary": "x", "claims": [{"text": "结论 [2]", "cites": [1]}]}, sources
        )

        self.assertEqual(claims[0].cited_source_ids, [sources[0].id, sources[1].id])

    def test_unknown_citation_numbers_are_dropped_not_fabricated(self):
        sources = [Source(title="A", url="https://a.example")]

        claims = SummarizerAgent.build_claims({"claims": [{"text": "结论 [9]", "cites": [1]}]}, sources)

        self.assertEqual(claims[0].cited_source_ids, [sources[0].id])

    def test_legacy_key_points_still_produce_traceable_claims(self):
        sources = [Source(title="A", url="https://a.example")]

        claims = SummarizerAgent.build_claims({"summary": "x", "key_points": ["结论 [1]", "没有引用的句子"]}, sources)

        self.assertEqual(claims[0].cited_source_ids, [sources[0].id])
        self.assertEqual(claims[1].cited_source_ids, [])

    def test_fallback_summary_claims_point_at_their_own_source(self):
        task = Task(title="T", intent="I", query="Q")
        source = Source(title="A", url="https://a.example", snippet="一段可读的证据文本。")

        summary = ResearchWorkFlow._fallback_summary(task, [source])

        self.assertEqual(summary.claims[0].cited_source_ids, [source.id])


class CitationIndexTest(unittest.TestCase):
    def test_each_source_is_numbered_once_across_tasks(self):
        shared = Source(title="Same", url="https://same.example")
        other = Source(title="Other", url="https://other.example")
        first_claim = Claim(text="a", cited_source_ids=[shared.id])
        second_claim = Claim(text="b", cited_source_ids=[shared.id])

        citations = build_citation_index([
            Summary(task=Task(title="t1", intent="i", query="q"), summary="", claims=[first_claim], sources=[shared]),
            Summary(task=Task(title="t2", intent="i", query="q"), summary="", claims=[second_claim], sources=[shared, other]),
        ])

        self.assertEqual([citation.index for citation in citations], [1, 2])
        self.assertEqual(citations[0].source_id, shared.id)
        self.assertEqual(citations[0].claim_ids, [first_claim.id, second_claim.id])
        self.assertEqual(citations[1].claim_ids, [])

    def test_sources_without_url_never_get_a_number(self):
        empty = Source(title="placeholder", url="")

        citations = build_citation_index([
            Summary(task=Task(title="t", intent="i", query="q"), summary="", sources=[empty]),
        ])

        self.assertEqual(citations, [])


class MarkerRenumberingTest(unittest.TestCase):
    def test_markers_are_mapped_to_global_numbers(self):
        self.assertEqual(renumber_markers("结论 [1] 与 [2]", {1: 10, 2: 3}), "结论 [10] 与 [3]")

    def test_unmappable_markers_are_dropped_rather_than_left_dangling(self):
        self.assertEqual(renumber_markers("结论 [1] 与 [9]", {1: 4}), "结论 [4] 与 ")

    def test_empty_mapping_strips_every_marker(self):
        self.assertEqual(renumber_markers("来源[2]报告，见[3]", {}), "来源报告，见")


class SourceIdentityTest(unittest.TestCase):
    def test_identity_ignores_case_and_trailing_slash(self):
        first = Source(title="A", url="https://Example.com/Path/")
        second = Source(title="B", url="https://example.com/Path")

        self.assertEqual(first.id, second.id)

    def test_domain_is_derived_from_the_url(self):
        self.assertEqual(Source(title="A", url="https://docs.example.com/x").domain, "docs.example.com")


class EvidenceMigrationTest(unittest.TestCase):
    def test_v1_evidence_files_are_migrated_on_read(self):
        with tempfile.TemporaryDirectory() as directory:
            store = NoteStore(Path(directory))
            legacy = {
                "topic": "t",
                "summaries": [
                    {
                        "task": {"id": "a", "title": "T", "intent": "I", "query": "Q"},
                        "summary": "s",
                        "key_points": ["要点 [1]"],
                        "sources": [{"title": "A", "url": "https://a.example"}],
                    }
                ],
            }
            (Path(directory) / "run1-evidence.json").write_text(json.dumps(legacy), encoding="utf-8")

            topic, summaries = store.load_evidence("run1")

        self.assertEqual(topic, "t")
        self.assertEqual(summaries[0].claims[0].text, "要点 [1]")
        self.assertEqual(summaries[0].claims[0].cited_source_ids, [summaries[0].sources[0].id])

    def test_round_trip_keeps_claims_and_declares_schema_version(self):
        with tempfile.TemporaryDirectory() as directory:
            store = NoteStore(Path(directory))
            source = Source(title="A", url="https://a.example")
            summary = Summary(
                task=Task(title="T", intent="I", query="Q"),
                summary="s",
                claims=[Claim(text="c", cited_source_ids=[source.id])],
                sources=[source],
            )

            store.save_evidence("run2", "topic", [summary])
            payload = json.loads((Path(directory) / "run2-evidence.json").read_text(encoding="utf-8"))
            topic, loaded = store.load_evidence("run2")

        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["documents"], [])
        self.assertEqual(payload["chunks"], [])
        self.assertEqual(topic, "topic")
        self.assertEqual(loaded[0].claims[0].cited_source_ids, [source.id])


if __name__ == "__main__":
    unittest.main()
