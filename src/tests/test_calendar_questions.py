"""A question about the calendar ("what meetings do I have today?") is answered from the calendar. It used to be
classed as "create an event", where there was nothing to extract and the model's refusal was shown as "the AI model is
not available"."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import routing
from controllers.classifier import ClassifierController, is_calendar_question
from helpers.exceptions import LLMUnavailable
from helpers.prompts import render


class FixedLLM:
    """A classifier model that always answers `reply` (here: the wrong answer the real model gave)."""
    enums = SimpleNamespace(SYSTEM=SimpleNamespace(value="system"), USER=SimpleNamespace(value="user"),
                            ASSISTANT=SimpleNamespace(value="assistant"))

    def __init__(self, reply):
        self.reply = reply

    def construct_prompt(self, prompt, role):
        return {"role": role, "content": prompt}

    def generate_text(self, prompt, chat_history=None, **kwargs):
        return self.reply


CALENDAR_ACTION = '{"request_type": "action", "action_type": "calendar", "priority": "medium"}'

QUESTIONS = [
    "what meetings do I have today?",
    "What's on my calendar tomorrow",
    "when is my next meeting?",
    "am I free at 3pm on Thursday?",
    "do I have any meetings this week",
    "show me my meetings",
    "how many meetings do I have on Monday?",
    "عندي اجتماعات ايه بكرة؟",
    "ايه مواعيدي النهاردة",
    "امتى الاجتماع الجاي؟",
]

REQUESTS_TO_CHANGE_THE_CALENDAR = [
    "book a meeting for me tomorrow at 5pm",
    "schedule a call with the team next Thursday",
    "create an event called Review on Friday at 2",
    "cancel my meeting with Sara",
    "reschedule the 3pm meeting to 4pm",
    "schedule a meeting with Sara, what time works?",       # a question, but also a request to schedule
    "احجز اجتماع بكرة الساعة 5",
    "الغي الاجتماع بتاع الخميس",
]


@pytest.mark.parametrize("query", QUESTIONS)
def test_a_question_about_the_calendar_is_recognised(query):
    assert is_calendar_question(query) is True


@pytest.mark.parametrize("query", REQUESTS_TO_CHANGE_THE_CALENDAR)
def test_a_request_to_change_the_calendar_is_not_a_question(query):
    assert is_calendar_question(query) is False


@pytest.mark.parametrize("query", QUESTIONS)
def test_even_when_the_model_calls_a_question_a_calendar_action_it_is_answered_by_retrieval(query):
    """The real model answered action/calendar for "what meetings do I have today?"."""
    result = ClassifierController(FixedLLM(CALENDAR_ACTION)).classify_query(query)
    assert (result["request_type"], result["action_type"]) == ("retrieval", "")
    state = {"request_type": result["request_type"], "action_type": result["action_type"]}
    assert routing.route_request_type(state) == "search_knowledge_base"      # the node that reads the calendar


@pytest.mark.parametrize("query", REQUESTS_TO_CHANGE_THE_CALENDAR)
def test_requests_to_change_the_calendar_still_become_calendar_actions(query):
    result = ClassifierController(FixedLLM(CALENDAR_ACTION)).classify_query(query)
    assert (result["request_type"], result["action_type"]) == ("action", "calendar")


def test_other_classifications_are_left_alone():
    controller = ClassifierController(FixedLLM('{"request_type": "action", "action_type": "gmail", "priority": "low"}'))
    assert controller.classify_query("what emails did I get today?")["action_type"] == "gmail"


def test_the_prompt_tells_the_model_that_asking_about_the_calendar_is_not_an_action():
    prompt = render("classifier", "classify_system")
    assert "what meetings do I have today?" in prompt and "NOT \"calendar\"" in prompt
    assert "Keywords: book, schedule, appointment, reserve" in prompt            # "meeting" is no longer a keyword for creating


def test_a_model_that_cannot_produce_the_requested_json_is_not_reported_as_an_outage():
    class BadRequest(Exception):
        status_code = 400

    error = LLMUnavailable.from_exception(BadRequest("Failed to generate JSON ... code': 'json_validate_failed'"), provider="Groq")
    assert error.reason == "unusable_output"
    assert "not available" not in error.public_message and "differently" in error.public_message
    # a genuine outage keeps its own message
    class Down(Exception):
        status_code = 503

    assert "not available" in LLMUnavailable.from_exception(Down("boom"), provider="Groq").public_message


def test_a_calendar_question_without_google_says_the_calendar_is_not_connected(monkeypatch):
    """Not the vague "no information found": the user is told what to do."""
    import asyncio

    from agent import AgentDeps
    from agent.nodes import retrieval as retrieval_node
    from helpers.exceptions import GoogleAuthRequiredException

    def not_connected():
        raise GoogleAuthRequiredException("No Google account connected.")

    monkeypatch.setattr(retrieval_node, "calendar_for_request", not_connected)

    class EmptyIndex:
        def search_by_vector(self, *a, **k):
            return []

        def is_collection_existed(self, name):
            return False

    class Embedder:
        def embed_text(self, *a, **k):
            return [0.0] * 4

        async def aembed_text(self, *a, **k):
            return [0.0] * 4

    state = {"query": "what meetings do I have today?", "user_id": "u1", "project_id": "default", "metadata": {},
             "chat_history": [], "request_type": "retrieval", "action_type": ""}
    deps = AgentDeps(vectordb_client=EmptyIndex(), embedding_client=Embedder(), generation_client=None, db_client=None)
    result = asyncio.run(retrieval_node.search_knowledge_base_node(state, deps))
    assert result["status"] == "error" and "calendar" in result["result"] and "Google" in result["result"]
    assert "No information found" not in result["result"]
