"""Exercise the live service, save real audio, and verify disconnect recovery."""
import argparse
import base64
import http.client
import json
import math
import pathlib
import struct
import time
from urllib.parse import urlsplit

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://10.4.0.10:30824")
parser.add_argument("--key-file", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--text", default="Hello. I am here with you. Take a slow breath, and tell me what is on your mind.")
args = parser.parse_args()
url = urlsplit(args.url)
key = pathlib.Path(args.key_file).read_text().strip()
headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
output = pathlib.Path(args.output)
output.mkdir(parents=True, exist_ok=True)


def request(path, body=None, authenticated=True):
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=180)
    connection.request("GET" if body is None else "POST", path, json.dumps(body) if body else None,
                       headers if authenticated else {"Content-Type": "application/json"})
    return connection, connection.getresponse()


connection, response = request("/v1/audio/speech", {"input": "Unauthorized."}, False)
assert response.status == 401, response.read()
connection.close()
connection, response = request("/v1/audio/speech", {"input": " "})
assert response.status == 422, response.read()
connection.close()

start = time.monotonic()
connection, response = request("/v1/audio/speech", {"input": args.text})
assert response.status == 200, response.read()
assert response.getheader("Content-Type").startswith("application/x-ndjson")
chunks = []
first = None
done = None
cancel_connection = None
for line in response:
    event = json.loads(line)
    assert event["type"] != "error", event
    if event["type"] == "audio":
        first = first if first is not None else time.monotonic() - start
        assert event["sample_rate"] == 24000
        pcm = base64.b64decode(event["pcm"], validate=True)
        assert len(pcm) % 4 == 0 and len(pcm) > 0
        assert all(math.isfinite(x[0]) for x in struct.iter_unpack("<f", pcm))
        chunks.append(pcm)
    elif event["type"] == "done":
        done = event
        completed_at = time.monotonic()
        # The terminal event must mean the model is already available again.
        cancel_connection, cancel_response = request("/v1/audio/speech", {"input": "This is a cancellation test. " * 20})
        assert cancel_response.status == 200, cancel_response.read()
connection.close()
assert chunks and done, "Stream did not complete with audio"
waveform = b"".join(chunks)
(output / "speech.f32le").write_bytes(waveform)
result = {"input": args.text, "client_first_audio_seconds": first,
          "client_wall_seconds": completed_at - start, "audio_seconds": len(waveform) / 4 / 24000,
          "chunks": len(chunks), "completion": done}
connection, response = request("/metrics")
result["server_metrics"] = json.load(response)
connection.close()
print(json.dumps(result, indent=2), flush=True)
(output / "generation.json").write_text(json.dumps(result, indent=2) + "\n")

# Keep one stream active, reject concurrent work, then explicitly close TCP.
assert json.loads(cancel_response.readline())["type"] == "audio"
other, rejected = request("/v1/audio/speech", {"input": "Must not run concurrently."})
assert rejected.status == 429, rejected.read()
other.close()
cancel_response.close()
cancel_connection.close()
start = time.monotonic()
retries = 0
while time.monotonic() - start < 20:
    connection, response = request("/v1/audio/speech", {"input": "Thank you.", "max_audio_seconds": 0.08})
    if response.status == 200:
        break
    assert response.status == 429, response.read()
    response.read()
    connection.close()
    retries += 1
    time.sleep(0.05)
else:
    raise AssertionError("Disconnected generation did not stop within 20s")
result["disconnect_release_seconds"] = time.monotonic() - start
other, metrics_response = request("/metrics")
metrics = json.load(metrics_response)
other.close()
assert metrics["last_generation"]["status"] == "cancelled", metrics
result["cancellation_metrics"] = metrics
events = [json.loads(line) for line in response]
connection.close()
assert any(event["type"] == "audio" for event in events), events
assert events[-1] == {"type": "done", "finish_reason": "length_limit"}, events[-1]
result["cancel_next_request_429_retries"] = retries
(output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
print("Auth, validation, serialized access, finite PCM, completion, and disconnect cancellation passed.")
print(json.dumps({"disconnect_release_seconds": result["disconnect_release_seconds"], "metrics": metrics}, indent=2))
