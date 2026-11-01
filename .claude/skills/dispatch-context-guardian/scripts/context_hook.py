#!/usr/bin/env python3
"""Portable non-blocking lifecycle adapter; never reads a transcript or key."""
import argparse,json,sys
from guardian_core import save,restore,render


def handle(payload,client,action=None,root=None):
    if not isinstance(payload,dict) or payload.get("stop_hook_active") is True:return {}
    event=payload.get("hook_event_name")
    session=payload.get("session_id")
    if not isinstance(session,str) or not session:return {"systemMessage":"Guardian: session identity unavailable; no checkpoint read or written."}
    try:
        if action=="restore" or event=="SessionStart":
            text=render(restore(client,session,root))
            return {"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":text}}
        if action=="save" or event in ("PreCompact","Stop"):
            state=save(client,session,root)
            available=sum(v.get("available") is True for v in state["summary"].values())
            return {"systemMessage":f"Guardian: summary checkpoint saved ({available}/3 sources available); cached state is not live evidence."}
        return {}
    except Exception:
        return {"systemMessage":"Guardian: checkpoint unavailable; recheck permitted live state. No recovery action was executed."}


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--client",choices=["codex","claude","chatgpt","cowork","generic"],default="generic")
    parser.add_argument("--action",choices=["save","restore"]);args=parser.parse_args()
    try:
        raw=sys.stdin.buffer.read(65537)
        if len(raw)>65536:raise ValueError("oversized hook payload")
        payload=json.loads(raw)
        print(json.dumps(handle(payload,args.client,args.action)))
    except Exception:print(json.dumps({"systemMessage":"Guardian: invalid event; no checkpoint action."}))
    return 0

if __name__=="__main__":raise SystemExit(main())
