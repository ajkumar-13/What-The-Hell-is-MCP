"""Send the requests a hand-written client gets wrong, and print what comes back.

Starts the post 05 server (code/05-first-server) on 127.0.0.1 and a free port,
sends one broken request per case, prints the HTTP status and the JSON-RPC
error or tool result, and stops the server. From the series root:

    uv run python posts/03-wire-protocol/snippets/what_can_go_wrong.py

Every request is built exactly as raw_discover.py builds it and then broken in
one way only, so each case differs from a working request by one change.
"""

from __future__ import annotations

import json
import sys

import httpx

sys.dont_write_bytecode = True  # importing the sibling script leaves no __pycache__ beside it
from raw_discover import PROTOCOL_VERSION, build, headers, local_server, read_result


def show(label: str, response: httpx.Response) -> None:
    message = read_result(response)
    print(label)
    if "error" in message:
        error = message["error"]
        line = f"  HTTP {response.status_code}  error {error['code']}  {error['message']}"
        if "data" in error:
            line += f"  data={json.dumps(error['data'], separators=(',', ':'))}"
        print(line + f"  id={json.dumps(message.get('id'))}")
    else:
        result = message["result"]
        text = result.get("content", [{}])[0].get("text", "").splitlines()[0]
        print(f"  HTTP {response.status_code}  result  isError={json.dumps(result.get('isError', False))}  {text}")


def main() -> None:
    with local_server() as url, httpx.Client(timeout=30.0) as client:

        def send(label, body, hdrs):
            show(label, client.post(url, content=json.dumps(body), headers=hdrs))

        # 1. The specification's feature pages abbreviate _meta away; sent as printed.
        body = build(1, "tools/list")
        del body["params"]["_meta"]
        send("_meta left out, as the feature pages print requests", body, headers("tools/list"))

        # 2. clientInfo is a SHOULD in the specification; this SDK build requires it.
        body = build(2, "tools/list")
        del body["params"]["_meta"]["io.modelcontextprotocol/clientInfo"]
        send("clientInfo left out", body, headers("tools/list"))

        # 3. A revision the server does not implement, in both the body and the header.
        body = build(3, "tools/list")
        body["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = "1900-01-01"
        hdrs = headers("tools/list")
        hdrs["MCP-Protocol-Version"] = "1900-01-01"
        send("unsupported revision 1900-01-01", body, hdrs)

        # 4. The routing header names a different method from the body.
        hdrs = headers("tools/list")
        hdrs["Mcp-Method"] = "prompts/list"
        send("Mcp-Method header disagrees with the body", build(4, "tools/list"), hdrs)

        # 5. A tools/call without the Mcp-Name header that mirrors the tool name.
        body = build(5, "tools/call", {"name": "get_system_info", "arguments": {}})
        send("Mcp-Name left out of a tools/call", body, headers("tools/call"))

        # 6. A method nobody implements.
        send("unknown method", build(6, "tools/lsit"), headers("tools/lsit"))

        # 7. A tool name the server does not have.
        body = build(7, "tools/call", {"name": "get_wether", "arguments": {}})
        send("unknown tool name", body, headers("tools/call", "get_wether"))

        # 8. A well-formed tools/call whose argument has the wrong type for the tool.
        body = build(8, "tools/call", {"name": "find_process", "arguments": {"name": 42}})
        send("argument of the wrong type", body, headers("tools/call", "find_process"))

        # 9. A tools/call that breaks the CallToolRequest shape: arguments is not an object.
        body = build(9, "tools/call", {"name": "find_process", "arguments": "python"})
        send("arguments that are not an object", body, headers("tools/call", "find_process"))

        # 10. A tool that needs elicitation, called by a client that declared none.
        body = build(10, "tools/call", {"name": "terminate_process", "arguments": {"pid": 999999}})
        send("elicitation needed but not declared", body, headers("tools/call", "terminate_process"))

        # 11. Not JSON at all.
        response = client.post(url, content=b'{"jsonrpc": "2.0", "id": 11,', headers=headers("tools/list"))
        show("unparseable JSON", response)

        # 12. The same request, correct, for comparison.
        response = client.post(url, content=json.dumps(build(12, "tools/list")), headers=headers("tools/list"))
        tools = read_result(response)["result"]["tools"]
        print("nothing missing")
        print(f"  HTTP {response.status_code}  result  {len(tools)} tools at revision {PROTOCOL_VERSION}")


if __name__ == "__main__":
    main()
