#!/usr/bin/env python3
"""
Helper: init a chrome-mcp session and call one tool, return result text.
Usage: python3 mcp_call.py <tool_name> '<json_args>'
"""
import sys, json, subprocess, re

MCP_URL = "http://127.0.0.1:12306/mcp"
HEADERS_INIT = [
    "-H", "Content-Type: application/json",
    "-H", "Accept: application/json, text/event-stream",
]

def curl(extra_headers, data):
    cmd = ["curl", "-si", MCP_URL, "-X", "POST"] + HEADERS_INIT + extra_headers + ["-d", json.dumps(data)]
    out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode()
    return out

def get_session_id(raw):
    for line in raw.splitlines():
        if "mcp-session-id" in line.lower():
            return line.split(":", 1)[1].strip()
    return None

def parse_event(raw):
    m = re.search(r"data: (.+)", raw)
    if m:
        return json.loads(m.group(1))
    return None

def main():
    if len(sys.argv) < 3:
        print("Usage: mcp_call.py <tool_name> '<json_args>'", file=sys.stderr)
        sys.exit(1)

    tool_name = sys.argv[1]
    tool_args = json.loads(sys.argv[2])

    # Init session
    init_payload = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "internship-scout", "version": "1.0"}}
    }
    raw = curl([], init_payload)
    session_id = get_session_id(raw)
    if not session_id:
        print("ERROR: could not get session id", file=sys.stderr)
        sys.exit(1)

    # Call tool
    call_payload = {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": tool_name, "arguments": tool_args}
    }
    raw2 = curl(["-H", f"mcp-session-id: {session_id}"], call_payload)
    result = parse_event(raw2)
    if result:
        content = result.get("result", {}).get("content", [{}])
        print(content[0].get("text", ""))
    else:
        print(raw2)

if __name__ == "__main__":
    main()
