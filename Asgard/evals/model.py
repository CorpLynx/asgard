"""Where a reply comes from when an eval runs. Standard library only.

1. A recorded reply: evals/replies/<case id>.txt, or the folder in ASGARD_EVAL_REPLIES. That is the
   clipboard tier: paste a case's model_input() into the chat you are allowed to use, save the answer
   as the file. It needs no key and no network, and it's how to evaluate the on-prem chat.
2. A live call to any OpenAI-compatible chat endpoint: ASGARD_EVAL_ENDPOINT (the full
   .../v1/chat/completions URL), ASGARD_EVAL_KEY and ASGARD_EVAL_MODEL. Only ever send the synthetic
   cases in cases/; never a real pack.

Returns None when neither is set, and the tests skip.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

REPLIES = Path(os.environ.get("ASGARD_EVAL_REPLIES") or Path(__file__).resolve().parent / "replies")


def reply_for(case: Dict[str, Any], prompt: str) -> Optional[str]:
    recorded = REPLIES / f"{case['id']}.txt"
    if recorded.is_file():
        return recorded.read_text(encoding="utf-8")
    url = os.environ.get("ASGARD_EVAL_ENDPOINT")
    if not url:
        return None
    body = json.dumps({"model": os.environ.get("ASGARD_EVAL_MODEL", ""), "temperature": 0,
                       "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    request = urllib.request.Request(url, body, {"Content-Type": "application/json",
                                                 "Authorization": "Bearer " + os.environ.get("ASGARD_EVAL_KEY", "")})
    with urllib.request.urlopen(request, timeout=120, context=ssl.create_default_context()) as response:
        return json.load(response)["choices"][0]["message"]["content"]
