"""A scripted stand-in for a native agent provider, shared by every suite that runs a real worker.

The worker speaks Claude Code's stream-json over stdin/stdout, so the fake is a
tiny Python program in that dialect. It records what reached it in a JSON file
(``FAKE_PROVIDER_LOG``), because the provider runs under tmux in another process
and a file is the only thing a test can read back. Two texts script it:
``oversized`` emits a 70 KB reply first, ``hold replay`` withholds its reply
until the next frame arrives, and ``slow:<seconds>`` answers after that delay
and records it under ``completed``, which is how a test keeps a turn IN FLIGHT
across an event and then sees it finish.
"""

from __future__ import annotations

from grove.core.native_worker import _BOOT_PROMPT, _PEER_PREAMBLE


def fake_provider_source() -> str:
    """The provider program's source, for ``python -u -c``."""
    return (
        "import json,os,sys\n"
        "log=os.environ['FAKE_PROVIDER_LOG']\n"
        "data={'pid':os.getpid(),'session':'6c44c19e-8f97-47fb-86e1-e82b6bc00cd7','initial':0,'followups':[]}\n"
        "pending=None\n"
        "def save():\n"
        " with open(log+'.tmp','w') as out: json.dump(data,out)\n"
        " os.replace(log+'.tmp',log)\n"
        "save()\n"
        "print(json.dumps({'type':'system','subtype':'init','session_id':data['session']}),flush=True)\n"
        "for raw in sys.stdin:\n"
        " frame=json.loads(raw)\n"
        " if frame.get('type') == 'control_request':\n"
        "  if frame['request']['subtype'] == 'interrupt':\n"
        "   data['interrupted']=True\n"
        "   save()\n"
        "   response={'subtype':'success','request_id':frame['request_id'],'response':{}}\n"
        "   print(json.dumps({'type':'control_response','response':response}),flush=True)\n"
        "   if pending: print(json.dumps(pending),flush=True)\n"
        "   pending=None\n"
        "  continue\n"
        " if frame.get('type') != 'user': continue\n"
        " text=frame['message']['content']\n"
        # Both markers are INJECTED from the worker's own constants rather than
        # retyped: this fake distinguishes the boot turn from the initial task
        # by their text, so a reworded prompt would otherwise desynchronise it
        # silently and every assertion here would time out naming no cause.
        f" if {_BOOT_PROMPT[:40]!r} in text: continue\n"
        f" if {_PEER_PREAMBLE[:40]!r} in text: data['initial']+=1\n"
        " else: data['followups'].append(text)\n"
        " save()\n"
        " reply={'type':'user','uuid':frame.get('uuid'),'message':frame['message']}\n"
        " if text == 'oversized':\n"
        "  large={'type':'assistant','message':{'content':[{'type':'text','text':'x'*70000}]}}\n"
        "  print(json.dumps(large),flush=True)\n"
        " if text == 'hold replay':\n"
        "  pending=reply\n"
        "  continue\n"
        " if text.startswith('slow:'):\n"
        "  import time; time.sleep(float(text[5:]))\n"
        "  data.setdefault('completed',[]).append(text)\n"
        "  save()\n"
        " print(json.dumps(reply),flush=True)\n"
    )


__all__ = ["fake_provider_source"]
