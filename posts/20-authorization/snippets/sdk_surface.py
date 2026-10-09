"""Post 20, section 9: the SDK's server-side authorization surface, read from the installed package.

Run from the series root:
    uv run python posts/20-authorization/snippets/sdk_surface.py

Prints the SDK version, the one method of the `TokenVerifier` protocol, the
fields of `AccessToken`, the required fields of `AuthSettings`, the three
constructor refusals of `MCPServer`, and the defaults that keep Dynamic Client
Registration off, together with whether the registration handler's source
mentions a deprecation at all. Nothing is served.
"""

from __future__ import annotations

import inspect
import typing
import warnings
from importlib.metadata import version

from mcp.server.auth import provider
from mcp.server.auth.handlers import register
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer


class Verifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        return None


class Provider:
    """Stands in for an authorization server provider; never called."""


def refusal(**kwargs) -> str:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            MCPServer("probe", **kwargs)
    except ValueError as exc:
        return f"ValueError: {exc}"
    return "accepted"


def main() -> None:
    print(f"mcp {version('mcp')}")
    print()

    methods = [n for n, v in vars(TokenVerifier).items() if inspect.iscoroutinefunction(v)]
    print(f"TokenVerifier is a Protocol: {typing.Protocol in TokenVerifier.__mro__}; methods: {methods}")
    hints = typing.get_type_hints(TokenVerifier.verify_token, vars(provider))
    print(f"  verify_token(token: {hints['token'].__name__}) -> {hints['return']}".replace(
        "mcp.server.auth.provider.", ""))
    print()

    print("AccessToken fields:")
    for name, field in AccessToken.model_fields.items():
        print(f"  {name:<11} {'required' if field.is_required() else 'default ' + repr(field.default)}")
    print()

    required = [n for n, f in AuthSettings.model_fields.items() if f.is_required()]
    print(f"AuthSettings required fields: {required}")
    print(f"AuthSettings.validate_token_resource default: "
          f"{AuthSettings.model_fields['validate_token_resource'].default!r}")
    print()

    auth = AuthSettings(issuer_url="https://auth.example.com",
                        resource_server_url="https://mcp.example.com/mcp",
                        validate_token_resource=True)
    print("MCPServer constructor:")
    print(f"  provider and verifier, with auth: {refusal(auth_server_provider=Provider(), token_verifier=Verifier(), auth=auth)}")
    print(f"  auth with neither:                {refusal(auth=auth)}")
    print(f"  verifier without auth:            {refusal(token_verifier=Verifier())}")
    print(f"  provider without auth:            {refusal(auth_server_provider=Provider())}")
    print(f"  verifier with auth:               {refusal(token_verifier=Verifier(), auth=auth)}")
    print()

    print(f"ClientRegistrationOptions().enabled = {ClientRegistrationOptions().enabled}")
    print(f"RevocationOptions().enabled = {RevocationOptions().enabled}")
    print(f"'deprecat' in the registration handler's source: {'deprecat' in inspect.getsource(register).lower()}")


if __name__ == "__main__":
    main()
