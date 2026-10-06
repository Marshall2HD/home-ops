"""Private, serialized CUDA CSM-1B streaming service for Still."""
import asyncio
import base64
import json
import logging
import os
import queue
import secrets
import threading
import time
from contextlib import asynccontextmanager

import torch
import torchaudio
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from huggingface_hub import hf_hub_download
from models import Model, ModelArgs
from moshi.models import loaders
from pydantic import BaseModel, Field
from safetensors.torch import load_model
from tokenizers.processors import TemplateProcessing
from transformers import AutoTokenizer

LOG = logging.getLogger("uvicorn.error")
MODEL = "sesame/csm-1b"
REFERENCE_TEXT = "like revising for an exam I'd have to try and like keep up the momentum because I'd start really early I'd be like okay I'm gonna start revising now and then like you're revising for ages and then I just like start losing steam I didn't do that for the exam we had recently to be fair that was a more of a last minute scenario but like yeah I'm trying to like yeah I noticed this yesterday that like Mondays I sort of start the day with this not like a panic but like a"
busy = threading.Lock()
last_generation = None


class Speech(BaseModel):
    input: str = Field(min_length=1, max_length=1200)
    max_audio_seconds: float = Field(default=30, ge=0.08, le=60)


class Engine:
    @torch.inference_mode()
    def __init__(self):
        torch.set_num_threads(4)
        # Leave board memory for Parakeet and the driver under time slicing.
        torch.cuda.set_per_process_memory_fraction(0.65)
        # This mirror has the same LFS object as sesame/csm-1b/model.safetensors.
        checkpoint = hf_hub_download("nielsr/csm-1b", "model.safetensors", revision="bf2c4391916748cd08448743e34f04235528c327")
        self.model = Model(ModelArgs("llama-1B", "llama-100M", 128256, 2051, 32))
        load_model(self.model, checkpoint, strict=True)
        self.model = self.model.to(device="cuda", dtype=torch.bfloat16).eval()
        self.model.setup_caches(1)
        self.tokenizer = AutoTokenizer.from_pretrained("unsloth/Llama-3.2-1B", revision="9535bd9b1d1dea6acafbdc4813b728796aeb28da")
        tok = self.tokenizer
        tok._tokenizer.post_processor = TemplateProcessing(
            single=f"{tok.bos_token}:0 $A:0 {tok.eos_token}:0",
            pair=f"{tok.bos_token}:0 $A:0 {tok.eos_token}:0 {tok.bos_token}:1 $B:1 {tok.eos_token}:1",
            special_tokens=[(tok.bos_token, tok.bos_token_id), (tok.eos_token, tok.eos_token_id)],
        )
        self.mimi = loaders.get_mimi(hf_hub_download(loaders.DEFAULT_REPO, loaders.MIMI_NAME), device="cuda")
        self.mimi.set_num_codebooks(32)
        audio, sr = torchaudio.load(hf_hub_download("sesame/csm-1b", "prompts/conversational_a.wav", repo_type="space"))
        audio = torchaudio.functional.resample(audio.mean(dim=0), sr, 24000).cuda()
        codes = self.mimi.encode(audio[None, None])[0]
        codes = torch.cat([codes, torch.zeros_like(codes[:, :1])], dim=1)
        frames = torch.zeros(codes.shape[1], 33, dtype=torch.long, device="cuda")
        masks = torch.zeros_like(frames, dtype=torch.bool)
        frames[:, :-1] = codes.T
        masks[:, :-1] = True
        text, text_mask = self.text_frames(REFERENCE_TEXT)
        self.context = torch.cat([text, frames])
        self.context_mask = torch.cat([text_mask, masks])
        LOG.info("Loaded %s on %s, reference frames=%d", MODEL, torch.cuda.get_device_name(), len(self.context))

    def text_frames(self, text):
        ids = self.tokenizer.encode(f"[0]{text}")
        frames = torch.zeros(len(ids), 33, dtype=torch.long, device="cuda")
        masks = torch.zeros_like(frames, dtype=torch.bool)
        frames[:, -1] = torch.tensor(ids, device="cuda")
        masks[:, -1] = True
        return frames, masks

    @torch.inference_mode()
    def generate(self, request, stop, output):
        global last_generation
        start = time.monotonic()
        samples = 0
        first_audio = None
        status = "done"
        terminal = None

        def emit(item):
            while not stop.is_set():
                try:
                    output.put(item, timeout=0.1)
                    return True
                except queue.Full:
                    pass
            return False

        try:
            torch.cuda.reset_peak_memory_stats()
            self.model.reset_caches()
            text, mask = self.text_frames(request.input)
            tokens = torch.cat([self.context, text])[None]
            masks = torch.cat([self.context_mask, mask])[None]
            positions = torch.arange(tokens.shape[1], device="cuda")[None]
            max_frames = int(request.max_audio_seconds * 12.5)
            if tokens.shape[1] + max_frames >= 2048:
                raise ValueError("Input and audio limit exceed CSM's 2048-frame context")
            with self.mimi.streaming(batch_size=1):
                for _ in range(max_frames):
                    if stop.is_set():
                        status = "cancelled"
                        break
                    frame = self.model.generate_frame(tokens, masks, positions, 0.9, 50)
                    if torch.all(frame == 0):
                        break
                    pcm = self.mimi.decode(frame[:, :, None]).flatten().float().cpu().numpy().astype("<f4", copy=False)
                    if pcm.size:
                        if first_audio is None:
                            first_audio = time.monotonic() - start
                        samples += pcm.size
                        if not emit({"type": "audio", "pcm": base64.b64encode(pcm.tobytes()).decode(), "sample_rate": 24000}):
                            status = "cancelled"
                            break
                    tokens = torch.cat([frame, torch.zeros(1, 1, device="cuda", dtype=torch.long)], dim=1)[:, None]
                    masks = torch.cat([torch.ones_like(frame, dtype=torch.bool), torch.zeros(1, 1, device="cuda", dtype=torch.bool)], dim=1)[:, None]
                    positions = positions[:, -1:] + 1
                else:
                    status = "length_limit"
            if not stop.is_set():
                terminal = {"type": "done", "finish_reason": status}
        except Exception:
            status = "error"
            LOG.exception("CSM generation failed")
            terminal = {"type": "error", "message": "CSM generation failed; check service logs"}
        finally:
            last_generation = {
                "status": status,
                "wall_seconds": time.monotonic() - start,
                "first_audio_seconds": first_audio,
                "audio_seconds": samples / 24000,
                "peak_allocated_mib": torch.cuda.max_memory_allocated() / 2**20,
                "peak_reserved_mib": torch.cuda.max_memory_reserved() / 2**20,
            }
            LOG.info("Generation metrics: %s", json.dumps(last_generation))
            busy.release()
            # A client may submit its next request as soon as it sees done.
            if terminal is not None:
                emit(terminal)
            emit(None)


@asynccontextmanager
async def lifespan(app):
    app.state.api_key = os.environ["CSM_API_KEY"].strip()
    app.state.engine = await asyncio.to_thread(Engine)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL, "device": "cuda", "sample_rate": 24000, "busy": busy.locked()}


def authenticate(authorization):
    if not secrets.compare_digest(authorization or "", f"Bearer {app.state.api_key}"):
        raise HTTPException(401, "Invalid bearer token")


@app.get("/metrics")
def metrics(authorization: str | None = Header(default=None)):
    authenticate(authorization)
    return {"last_generation": last_generation, "busy": busy.locked()}


@app.post("/v1/audio/speech")
async def speech(body: Speech, authorization: str | None = Header(default=None)):
    authenticate(authorization)
    if not body.input.strip():
        raise HTTPException(422, "input must contain text")
    if not busy.acquire(blocking=False):
        raise HTTPException(429, "CSM is busy; retry after the current stream closes")
    stop = threading.Event()
    output = queue.Queue(maxsize=4)

    async def stream():
        try:
            while True:
                try:
                    item = output.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.01)
                    continue
                if item is None:
                    break
                yield json.dumps(item, separators=(",", ":")) + "\n"
        finally:
            stop.set()

    threading.Thread(target=app.state.engine.generate, args=(body, stop, output), daemon=True).start()
    return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
