# Still CUDA speech service

Private endpoint: `http://10.4.0.10:30824`, on Nyx's LAN address. This is a
bearer-authenticated HTTP NodePort, not a public ingress or TLS endpoint. Use it
only over the private LAN or a trusted encrypted tunnel. No DNS is needed.

## Client contract

`GET /health` is unauthenticated and returns `status: "ok"`,
`model: "sesame/csm-1b"`, `device: "cuda"`, `sample_rate: 24000`, and `busy`.
Readiness waits for the weights and reference context to load.

`POST /v1/audio/speech` requires `Authorization: Bearer <CSM_API_KEY>` and
`Content-Type: application/json`. The body is `{"input":"Text to speak."}`.
An optional `max_audio_seconds` limits generation (default 30, maximum 60).
Input is limited to 1,200 characters. Empty/whitespace input returns 422.

The response is `application/x-ndjson`, one JSON object per newline:

```json
{"type":"audio","pcm":"<base64 float32 little-endian mono PCM>","sample_rate":24000}
{"type":"done","finish_reason":"done"}
```

Audio frames normally contain 1,920 samples (80 ms). `finish_reason` is
`length_limit` if the audio limit is reached. A failure after streaming starts
emits `{"type":"error","message":"..."}` instead of a successful completion.
Consume lines incrementally. Closing the HTTP connection cancels at a frame
boundary and releases the model for the next request. One generation runs at a
time; concurrent requests receive 429 rather than an unbounded queue.
The model lock is released before `done` is sent. After a disconnect, a client
may briefly receive 429 until the in-flight frame stops; retry only explicit
429 responses, never replay a partially consumed 200 stream.

`GET /metrics` uses the same bearer token and exposes the latest generation's
first-audio latency, duration, wall time, peak CUDA allocation/reservation, and
cancellation status. These CUDA counters describe CSM, not total board use.

## Credentials and storage

The authoritative credential is Kubernetes Secret `default/csm-api-key`, key
`CSM_API_KEY`. A mode-600 transfer copy is in the ignored workspace path
`.private/csm/api-key`; strip its trailing newline. Never commit or log its value.
No Hugging Face token is required by the pinned public mirrors used here.

Deployment `default/csm` owns one pod on Nyx with the existing `nvidia` runtime
and one of the existing six time-sliced GPU allocations. Nothing changes
Parakeet's deployment or the device-plugin configuration.
CSM requests 4 GiB host RAM, has a 20 GiB host-memory limit, and limits its
PyTorch CUDA allocator to 65% of board memory to leave room for Parakeet.

PVC `default/csm-data` is 40 GiB of `ceph-block` storage mounted at `/data`:

- `/data/huggingface`: downloaded CSM/Mimi weights, tokenizer, reference WAV.
- `/data/venv`: persistent Python dependencies over the image's PyTorch install.
- `/data/source/models.py`: pinned official Sesame CUDA model implementation.
- `/data/torchinductor` and `/data/triton`: persistent compilation caches.

The Kubernetes Deployment restarts the service after process/node failure.
Dependencies and model weights remain on the PVC. The manifest is locally
applied, **not yet pushed or wired into Flux**; cluster recreation requires
restoring the Secret and applying this directory again:

```sh
kubectl --kubeconfig kubeconfig apply -k kubernetes/apps/default/csm
```

The image is digest-pinned. Bootstrap installs GCC/G++ into a fresh container
for Mimi/Triton, so fresh container startup still needs Ubuntu package-mirror
access. Python packages and weights are reused from the PVC. A custom built
image would remove that remaining bootstrap dependency.

## Model provenance

Official `SesameAILabs/csm` model implementation is pinned to revision
`daed31e6d42cf71873999075de204fa37d2acec3`. The FP32 checkpoint comes from
`nielsr/csm-1b`, revision `bf2c4391916748cd08448743e34f04235528c327`, and is loaded
strictly into the official architecture before CUDA BF16 inference. Hugging
Face's LFS metadata identifies this 6,211,186,784-byte `model.safetensors` as the
same object as the official `sesame/csm-1b/model.safetensors`. This is not a
fine-tune, quantized substitute, or different speech model.
The downloaded file was also hashed on Nyx and matched that LFS object's
SHA-256: `2e7721144afe38b906d4f1048671da639fe142423f4a26283606ecebe894f4bf`.

The Llama tokenizer comes from pinned `unsloth/Llama-3.2-1B`. Voice conditioning
uses the official `sesame/csm-1b` Space's `prompts/conversational_a.wav` and its
official transcript, speaker 0. Mimi decodes all 32 codebooks at 24 kHz, with
one continuous streaming decoder state per utterance. The official offline
SilentCipher watermark pass is not applied to live PCM because it requires a
complete waveform.

## Reproducible live verification

```sh
uv run --no-project python kubernetes/apps/default/csm/verify.py \
  --key-file .private/csm/api-key --output .private/csm/verification
ffmpeg -f f32le -ar 24000 -ac 1 -i .private/csm/verification/speech.f32le \
  -ar 16000 -ac 1 -c:a pcm_s16le .private/csm/verification/speech-16k.wav
curl --resolve whisper.hades.casa:443:10.8.0.121 \
  https://whisper.hades.casa/v1/audio/transcriptions \
  -F file=@.private/csm/verification/speech-16k.wav
```

The live verifier asserts unauthorized/invalid-input rejection, finite PCM,
completed streaming, concurrent-request rejection, and disconnect cancellation.
It saves both waveform bytes and measured timing/memory JSON. Parakeet has no
existing NodePort; use its internal HTTPS gateway address with the hostname/SNI
preserved as above.

## Observed live results, September 20, 2026

| Measurement                         | First request | Warm request |
| ----------------------------------- | ------------: | -----------: |
| Client first PCM                    |       1.409 s |      0.269 s |
| Generated audio                     |       12.80 s |       5.28 s |
| End-to-end generation               |       14.30 s |       5.66 s |
| CSM peak allocated CUDA memory      |     3,866 MiB |    3,873 MiB |
| CSM peak reserved CUDA memory       |     4,862 MiB |    4,084 MiB |
| Disconnect to next accepted request |       0.653 s |      0.662 s |

Both runs passed the live verifier, including starting the next request
immediately upon `done`, 429 rejection while active, and disconnect/retry
recovery. Board use observed after the runs was 6,946 MiB of 23,034 MiB,
including Parakeet; that snapshot is not a sampled board-memory peak.

Parakeet transcribed the first generated waveform correctly:
"Hello, I am here with you. Take a slow breath and tell me what is on your mind."
The warm request, "I am listening. What would feel helpful for you right now?",
had audible repeated/hallucinated words in the middle, confirmed both by
Parakeet and audio inspection. It had no audible clipping/dropouts. This
deployment is genuine CSM-1B, but these observations do not establish uniformly
clean voice quality. Warm generation was approximately 1.07 times audio
duration, slightly slower than realtime on this unoptimized implementation.

Saved waveform, transcription, timing, and cancellation evidence is under
ignored workspace directories `.private/csm/cold` and `.private/csm/warm`.
Parakeet remained healthy with zero container restarts throughout these tests.
