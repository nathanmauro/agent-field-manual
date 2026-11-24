"""Regenerate the mcp-result-guard fixtures: placeholder data in the shape of real MCP results.

Run: python3 -I tests/fixtures/mcp-result-guard/generate.py tests/fixtures/mcp-result-guard
Shapes were read from real transcripts; every value here is a placeholder.
"""

import base64
import json
import sys

out = sys.argv[1]
CGJ = "͏"


def tracking_url(path, n):
    q = "&".join(f"param{i}=" + ("AbCdEf0123456789" * 2) for i in range(n))
    return f"https://www.example.com/{path}?{q}"


def message(i):
    urls = [
        "https://www.example.com/messaging/thread/2-PLACEHOLDERTHREADID000000000000000000000000000000000000000000",
        tracking_url("help/answer/4788", 12),
        tracking_url("comm/mypreferences/u/emailunsub", 16),
        tracking_url("help/answer/67", 11),
    ]
    plain = (
        "Placeholder Sender sent you a new message.\n\n"
        "Hi Placeholder, this is a one-sentence placeholder InMail body.\n\n"
        f"Reply: {urls[0]}\n\n\n\n"
        + ("This is placeholder footer copy about why you are receiving this email. " * 18)
        + f"\nLearn more: {urls[1]}\n\nUnsubscribe: {urls[2]}\n\nHelp: {urls[3]}\n"
        + "© 2026 Example Corporation, Placeholder Address.\n"
    )
    snippet = "Placeholder Sender sent you a new message " + (CGJ + " ") * 74 + "Hi Placeholder"
    row = (
        '<tr><td style="padding:0 24px;font-family:Helvetica,Arial,sans-serif;font-size:14px;">'
        '<span style="color:#333333">Placeholder cell text</span></td></tr>\n'
    )
    html = (
        "<html><head><style>.x{color:red}</style></head><body><table>"
        + row * 120
        + f'<a href="{urls[2]}">Unsubscribe</a></table></body></html>'
    )
    return {
        "addressDisplayNames": [
            {"displayName": "Placeholder Sender", "emailAddress": "sender@example.com"},
            {"displayName": "Placeholder Recipient", "emailAddress": "recipient@example.com"},
        ],
        "date": "2026-11-20T12:00:00Z",
        "historyId": "12345678",
        "htmlBody": html,
        "id": f"msg{i:013d}",
        "internalDate": "1795000000000",
        "labelIds": ["INBOX", "UNREAD", "CATEGORY_UPDATES", "IMPORTANT"],
        "plaintextBody": plain,
        "sender": "sender@example.com",
        "sizeEstimate": 54321,
        "snippet": snippet,
        "subject": "Placeholder Sender sent you a new message about a placeholder role",
        "threadId": "thread0000000001",
        "toRecipients": ["recipient@example.com"],
        "viewUrl": "https://mail.google.com/mail/u/0/#all/thread0000000001?placeholder=" + "x" * 60,
    }


thread = {
    "id": "thread0000000001",
    "messageCount": 1,
    "messages": [message(1)],
    "viewUrl": "https://mail.google.com/mail/u/0/#all/thread0000000001",
}
text = json.dumps(thread, ensure_ascii=False, separators=(",", ":"))

envelope = {
    "session_id": "11111111-2222-3333-4444-555555555555",
    "transcript_path": "/tmp/example-home/.claude/projects/-example/11111111-2222-3333-4444-555555555555.jsonl",
    "cwd": "/tmp/example-project",
    "permission_mode": "default",
    "hook_event_name": "PostToolUse",
}

get_thread = dict(envelope)
get_thread.update(
    tool_name="mcp__claude_ai_Gmail__get_thread",
    tool_input={"threadId": "thread0000000001"},
    tool_response=text,
    tool_use_id="toolu_01PLACEHOLDERGETTHREAD00",
    duration_ms=812,
)
json.dump(get_thread, open(f"{out}/post-get-thread.json", "w"), indent=2)

# Local MCP servers (a memory server, claude-in-chrome) answer with content-block arrays.
png = base64.b64encode(bytes(range(256)) * 40).decode()
record = {
    "sessions": [
        {
            "id": f"sess-{i:04d}",
            "summary": "Placeholder session summary text. " * 40,
            "attachment": "data:image/png;base64," + png[:3000],
            "blob": png[:1500],
        }
        for i in range(8)
    ]
}
blocks = dict(envelope)
blocks.update(
    tool_name="mcp__memory__recentSessions",
    tool_input={"limit": 8},
    tool_response=[
        {"type": "text", "text": json.dumps(record, separators=(",", ":"))},
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": png[:4000]},
        },
    ],
    tool_use_id="toolu_01PLACEHOLDERBLOCKS0000",
    duration_ms=95,
)
json.dump(blocks, open(f"{out}/post-blocks-base64.json", "w"), indent=2)

pre = dict(envelope)
pre.update(
    hook_event_name="PreToolUse",
    tool_name="mcp__claude_ai_Gmail__get_thread",
    tool_input={"threadId": "thread0000000001"},
    tool_use_id="toolu_01PLACEHOLDERPRE00000000",
)
json.dump(pre, open(f"{out}/pre-get-thread.json", "w"), indent=2)
print(len(text), len(blocks["tool_response"][0]["text"]))
