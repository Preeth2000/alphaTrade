import sys
sys.path.insert(0, '/home/preeth/projects/alphaTrade')

import pytest
import respx
import httpx
from alphaTrade.notify import webhook

def test_simple():
    print(f"\n_webhook_url = {repr(webhook._webhook_url)}")
    print(f"_rate_limits = {webhook._rate_limits}")
    webhook.configure("https://discord.com/api/webhooks/123/abc")
    print(f"After configure: _webhook_url = {repr(webhook._webhook_url)}")
    
    with respx.mock() as mock:
        mock.post("https://discord.com/api/webhooks/123/abc").mock(return_value=httpx.Response(204))
        print(f"Before notify: _webhook_url = {repr(webhook._webhook_url)}")
        webhook.notify("WARNING", "test", "cat")
        webhook._drain()
        print(f"After notify: mock.calls = {len(mock.calls)}")
        assert len(mock.calls) == 1

if __name__ == "__main__":
    test_simple()
