"""Shared test fixtures: a scripted fake OpenAI-compatible client."""

from __future__ import annotations


class FakeMessage:
    def __init__(self, content: str):
        self.content = content


class FakeChoice:
    def __init__(self, content: str):
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content: str):
        self.choices = [FakeChoice(content)]


class FakeCompletions:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.replies:
            raise AssertionError("FakeClient ran out of scripted replies")
        return FakeResponse(self.replies.pop(0))


class FakeChat:
    def __init__(self, replies: list[str]):
        self.completions = FakeCompletions(replies)


class FakeClient:
    """Stands in for openai.OpenAI; returns scripted replies in order."""

    def __init__(self, replies: list[str]):
        self.chat = FakeChat(replies)

    @property
    def calls(self) -> list[dict]:
        return self.chat.completions.calls
