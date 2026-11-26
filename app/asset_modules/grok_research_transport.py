"""Compact research-only request profile; native execution client is unchanged."""
import json
from urllib.request import Request
from .grok import _post


def compact_transport(request):
    payload=json.loads(request.data)
    payload['max_output_tokens']=256
    # Official Responses API reasoning schema, checked 2026-10-28.
    # Other configured models keep their existing provider defaults.
    if payload.get('model') in {'grok-4.5','grok-4.6','grok-4.7'}:
        payload['reasoning']={'effort':'low'}
    request=Request(request.full_url,data=json.dumps(payload,separators=(',',':')).encode(),
        headers=dict(request.header_items()),method='POST')
    return _post(request,timeout=15)
