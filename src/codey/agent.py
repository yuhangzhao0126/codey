"""Core agent loop: multi-turn streaming chat over an OpenAI-compatible API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator

from openai import AsyncOpenAI

from .config import Profile


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class Agent:
    profile: Profile
    system_prompt: str
    history: list[Message] = field(default_factory=list)
    _client: AsyncOpenAI = field(init=False)

    def __post_init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=self.profile.api_key,
            base_url=self.profile.base_url,
        )
        if self.system_prompt:
            self.history.append(Message(role="system", content=self.system_prompt))

    async def send(self, user_input: str) -> AsyncIterator[str]:
        """Append user message, stream assistant reply, append it to history."""
        self.history.append(Message(role="user", content=user_input))

        messages = [{"role": m.role, "content": m.content} for m in self.history]

        chunks: list[str] = []
        stream = await self._client.chat.completions.create(
            model=self.profile.model,
            messages=messages,
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                chunks.append(delta)
                yield delta

        self.history.append(Message(role="assistant", content="".join(chunks)))

    def reset(self) -> None:
        """Clear conversation history, keeping the system prompt."""
        self.history = [m for m in self.history if m.role == "system"]

    def swap_profile(self, profile: Profile) -> None:
        """Switch provider/model live. Keeps chat history and system prompt."""
        self.profile = profile
        self._client = AsyncOpenAI(api_key=profile.api_key, base_url=profile.base_url)
