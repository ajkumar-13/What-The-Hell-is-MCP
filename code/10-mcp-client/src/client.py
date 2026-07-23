# src/client.py - Multi-Provider MCP Host (CLI)
# Blog 4: Building Your Own MCP Client
# Supports: Anthropic (Claude), OpenAI (GPT), Google (Gemini)

import asyncio
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from dotenv import load_dotenv

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()

SYSTEM_PROMPT = """You are a CLI assistant with access to local tools via MCP.
Rules:
- If the user asks about system/process info, use the available tools. Do not guess.
- If a tool fails, explain the failure and suggest a next step.
- Keep outputs concise unless asked for detail.
"""


# ─── Unified types ────────────────────────────────────────────


@dataclass
class ToolCall:
    """Provider-agnostic representation of a tool call request."""
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    """Unified response from any LLM provider."""
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str | None = None
    raw: object = None  # provider-specific data for message history


# ─── Provider base class ──────────────────────────────────────


class LLMProvider(ABC):
    """
    Abstract base for LLM providers.
    Each subclass translates between its native API format
    and our unified ToolCall / LLMResponse types.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def format_tools(self, mcp_tools: list[dict]) -> list:
        """Convert MCP tool schemas to provider-specific tool format."""

    @abstractmethod
    def send(self, messages: list, tools: list, system: str) -> LLMResponse:
        """Call the LLM and return a unified response."""

    @abstractmethod
    def add_user_message(self, messages: list, text: str): ...

    @abstractmethod
    def add_assistant_response(self, messages: list, response: LLMResponse): ...

    @abstractmethod
    def add_tool_results(self, messages: list, results: list[tuple[ToolCall, str]]): ...


# ─── Anthropic (Claude) ──────────────────────────────────────


class AnthropicProvider(LLMProvider):
    def __init__(self, model: str | None = None):
        from anthropic import Anthropic

        self.client = Anthropic()
        self.model = model or "claude-sonnet-4-20250514"

    @property
    def name(self) -> str:
        return f"Anthropic ({self.model})"

    def format_tools(self, mcp_tools):
        # Anthropic's tool format matches MCP closely
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
            }
            for t in mcp_tools
        ]

    def send(self, messages, tools, system):
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system,
            tools=tools,
            messages=messages,
        )
        if resp.stop_reason == "tool_use":
            calls = [
                ToolCall(id=b.id, name=b.name, arguments=b.input)
                for b in resp.content
                if getattr(b, "type", None) == "tool_use"
            ]
            return LLMResponse(tool_calls=calls, raw=resp.content)
        text = "\n".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "text", None)
        ).strip()
        return LLMResponse(text=text or "(no output)", raw=resp.content)

    def add_user_message(self, messages, text):
        messages.append({"role": "user", "content": text})

    def add_assistant_response(self, messages, response):
        messages.append({"role": "assistant", "content": response.raw})

    def add_tool_results(self, messages, results):
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tc.id, "content": text}
                    for tc, text in results
                ],
            }
        )


# ─── OpenAI (GPT) ────────────────────────────────────────────


class OpenAIProvider(LLMProvider):
    def __init__(self, model: str | None = None):
        from openai import OpenAI

        self.client = OpenAI()
        self.model = model or "gpt-4o"

    @property
    def name(self) -> str:
        return f"OpenAI ({self.model})"

    def format_tools(self, mcp_tools):
        # OpenAI wraps each tool in {"type": "function", "function": {...}}
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in mcp_tools
        ]

    def send(self, messages, tools, system):
        # OpenAI puts the system prompt in the messages array
        full = [{"role": "system", "content": system}] + messages
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=full,
            tools=tools or None,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                )
                for tc in msg.tool_calls
            ]
            return LLMResponse(tool_calls=calls, raw=msg)
        return LLMResponse(text=msg.content or "(no output)", raw=msg)

    def add_user_message(self, messages, text):
        messages.append({"role": "user", "content": text})

    def add_assistant_response(self, messages, response):
        msg = response.raw
        entry = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        messages.append(entry)

    def add_tool_results(self, messages, results):
        # OpenAI: each tool result is a separate message with role="tool"
        for tc, text in results:
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})


# ─── Google Gemini ────────────────────────────────────────────


def _clean_schema_for_gemini(schema: dict) -> dict:
    """Strip JSON Schema keys that Gemini doesn't support."""
    skip = {"additionalProperties", "$schema"}
    out = {}
    for k, v in schema.items():
        if k in skip:
            continue
        if isinstance(v, dict):
            out[k] = _clean_schema_for_gemini(v)
        elif isinstance(v, list):
            out[k] = [
                _clean_schema_for_gemini(i) if isinstance(i, dict) else i for i in v
            ]
        else:
            out[k] = v
    return out


class GeminiProvider(LLMProvider):
    def __init__(self, model: str | None = None):
        from google import genai

        self.client = genai.Client()  # reads GOOGLE_API_KEY from env
        self.model = model or "gemini-2.5-flash"

    @property
    def name(self) -> str:
        return f"Gemini ({self.model})"

    def format_tools(self, mcp_tools):
        from google.genai import types

        declarations = []
        for t in mcp_tools:
            schema = _clean_schema_for_gemini(t["input_schema"])
            declarations.append(
                types.FunctionDeclaration(
                    name=t["name"],
                    description=t["description"],
                    parameters=schema if schema.get("properties") else None,
                )
            )
        return [types.Tool(function_declarations=declarations)]

    def send(self, messages, tools, system):
        from google.genai import types

        resp = self.client.models.generate_content(
            model=self.model,
            contents=messages,
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=tools,
            ),
        )
        parts = resp.candidates[0].content.parts
        calls = [
            ToolCall(
                id=f"call_{p.function_call.name}_{i}",
                name=p.function_call.name,
                arguments=dict(p.function_call.args) if p.function_call.args else {},
            )
            for i, p in enumerate(parts)
            if p.function_call
        ]
        if calls:
            return LLMResponse(tool_calls=calls, raw=resp)
        return LLMResponse(text=resp.text or "(no output)", raw=resp)

    def add_user_message(self, messages, text):
        from google.genai import types

        messages.append(
            types.Content(role="user", parts=[types.Part.from_text(text=text)])
        )

    def add_assistant_response(self, messages, response):
        if hasattr(response.raw, "candidates"):
            messages.append(response.raw.candidates[0].content)

    def add_tool_results(self, messages, results):
        from google.genai import types

        parts = [
            types.Part.from_function_response(
                name=tc.name, response={"result": text}
            )
            for tc, text in results
        ]
        messages.append(types.Content(role="user", parts=parts))


# ─── Provider factory ────────────────────────────────────────


PROVIDERS = {
    "anthropic": ("ANTHROPIC_API_KEY", AnthropicProvider),
    "openai": ("OPENAI_API_KEY", OpenAIProvider),
    "gemini": ("GOOGLE_API_KEY", GeminiProvider),
}


def get_provider(name: str, model: str | None = None) -> LLMProvider:
    """Create an LLM provider by name. Validates the required API key exists."""
    name = name.lower()
    if name not in PROVIDERS:
        available = ", ".join(PROVIDERS.keys())
        raise ValueError(f"Unknown provider '{name}'. Choose from: {available}")
    env_key, cls = PROVIDERS[name]
    if not os.getenv(env_key):
        raise ValueError(f"{env_key} not set. Add it to your .env file.")
    return cls(model=model)


# ─── MCP Host CLI ─────────────────────────────────────────────


def _extract_text_from_mcp_result(call_tool_result) -> str:
    """MCP tool results can contain multiple content blocks."""
    parts = []
    for c in getattr(call_tool_result, "content", []) or []:
        text = getattr(c, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip() or "(no text content returned)"


class MCPHostCLI:
    """
    Host app responsibilities:
    - Maintain MCP connection (ClientSession) to one server
    - Discover tools from that server
    - Run the tool-execution loop with ANY LLM provider
    """

    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self.session: ClientSession | None = None
        self.mcp_tools: list[dict] = []  # raw MCP tool schemas
        self.llm_tools: list = []  # provider-formatted tools

        # Keep refs so we can close them cleanly
        self._transport_cm = None
        self._read = None
        self._write = None

    async def connect(self, command: str, args: list[str], env: dict | None = None):
        """Connect to an MCP server via stdio transport."""
        server_params = StdioServerParameters(command=command, args=args, env=env)

        # Open stdio transport
        self._transport_cm = stdio_client(server_params)
        self._read, self._write = await self._transport_cm.__aenter__()

        # Open MCP session
        self.session = ClientSession(self._read, self._write)
        await self.session.__aenter__()
        await self.session.initialize()

        # Discover tools and convert to provider-specific format
        tool_result = await self.session.list_tools()
        self.mcp_tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.inputSchema,
            }
            for t in tool_result.tools
        ]
        self.llm_tools = self.provider.format_tools(self.mcp_tools)

        print(f"\n✓ Connected ({self.provider.name}). Found {len(self.mcp_tools)} tools:")
        for t in self.mcp_tools:
            print(f"  - {t['name']}: {t['description']}")

    async def close(self):
        """Clean up session and transport (prevents leaked subprocesses)."""
        if self.session is not None:
            await self.session.__aexit__(None, None, None)
            self.session = None

        if self._transport_cm is not None:
            await self._transport_cm.__aexit__(None, None, None)
            self._transport_cm = None

    async def chat(self, user_message: str, messages: list) -> str:
        """Process one user message through the tool execution loop."""
        if not self.session:
            raise RuntimeError("Not connected to an MCP server.")

        self.provider.add_user_message(messages, user_message)

        while True:
            response = self.provider.send(messages, self.llm_tools, SYSTEM_PROMPT)

            # If the LLM wants to use tools, execute them
            if response.tool_calls:
                self.provider.add_assistant_response(messages, response)

                results: list[tuple[ToolCall, str]] = []
                for tc in response.tool_calls:
                    print(f"\n  → Using tool: {tc.name}")

                    try:
                        mcp_result = await self.session.call_tool(tc.name, tc.arguments)
                        text = _extract_text_from_mcp_result(mcp_result)
                    except Exception as e:
                        # Don't crash - return error as tool result
                        text = f"TOOL_ERROR: {type(e).__name__}: {e}"

                    results.append((tc, text))

                self.provider.add_tool_results(messages, results)
                continue

            # Otherwise, LLM is done - return final text
            self.provider.add_assistant_response(messages, response)
            return response.text or "(no output)"

    async def run_cli(self):
        """Run the interactive CLI loop."""
        print("\n" + "=" * 50)
        print("Chat started! Type 'quit' to exit.")
        print("=" * 50 + "\n")
        messages: list = []

        while True:
            try:
                user_input = input("You: ").strip()
                if user_input.lower() in {"quit", "exit", "q"}:
                    break
                if not user_input:
                    continue

                response = await self.chat(user_input, messages)
                print(f"\nAssistant:\n{response}\n")
            except KeyboardInterrupt:
                break


async def main():
    # Read provider config from .env
    provider_name = os.getenv("LLM_PROVIDER", "anthropic")
    model = os.getenv("LLM_MODEL")  # None = use provider default

    try:
        provider = get_provider(provider_name, model)
    except ValueError as e:
        print(f"Error: {e}")
        return

    host = MCPHostCLI(provider)

    try:
        # ============================================
        # UPDATE THIS PATH TO YOUR SERVER LOCATION!
        # ============================================
        await host.connect(
            command="uv",
            args=[
                "run",
                "--directory",
                r"C:\Users\YourName\mcp-system-info",
                "python",
                "src/server.py",
            ],
        )
        await host.run_cli()
    finally:
        await host.close()
        print("\nDisconnected. Goodbye!")


if __name__ == "__main__":
    asyncio.run(main())
