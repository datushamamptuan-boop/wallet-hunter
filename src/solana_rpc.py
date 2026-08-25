import requests, time, os

class SolanaRPC:
    def __init__(self, url):
        self.url = url
        self.session = requests.Session()

    def call(self, method, params):
        for attempt in range(5):
            try:
                r = self.session.post(
                    self.url,
                    json={"jsonrpc":"2.0","id":1,"method":method,"params":params},
                    timeout=35
                )
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                data = r.json()
                if "error" in data:
                    raise RuntimeError(data["error"])
                return data.get("result")
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(1.5 * (attempt + 1))
        return None

    def batch(self, requests_list):
        if not requests_list:
            return []
        payload = []
        for i, item in enumerate(requests_list):
            payload.append({
                "jsonrpc":"2.0","id":i+1,
                "method":item["method"],"params":item["params"]
            })
        for attempt in range(5):
            try:
                r = self.session.post(self.url, json=payload, timeout=60)
                if r.status_code in (429,500,502,503,504):
                    time.sleep(2 * (attempt+1))
                    continue
                r.raise_for_status()
                out = r.json()
                return sorted(out, key=lambda x:x.get("id",0))
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2 * (attempt+1))
        return []
