import time, urllib.request, json, os

deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:" + os.environ.get("ADAPTER_PORT", "18800") + "/health", timeout=3
        ) as response:
            if json.load(response).get("ok"):
                raise SystemExit(0)
    except Exception:
        pass
    time.sleep(1)
raise SystemExit("Hub adapter did not become ready within 60 seconds")
