from fastapi import FastAPI
import asyncio, threading

app = FastAPI(title="mempressure")

_hold = []                 # fixed-blob store (vertical right-sizing scenario)
_lock = threading.Lock()

@app.get("/healthz")
def healthz():
    return {"ok": True}

# Per-request hold: memory scales with CONCURRENCY.
# This is the one shape where adding a memory target to the HPA is the CORRECT fix,
# because more replicas means fewer concurrent requests per pod means less memory per pod.
@app.get("/heavy")
async def heavy(hold_mb: int = 18, secs: float = 2.0):
    buf = bytearray(hold_mb * 1024 * 1024)   # bytearray zero-fills, so pages commit and RSS actually moves
    await asyncio.sleep(secs)                 # request stays in-flight; concurrent holds accumulate
    return {"held_mb": hold_mb, "len": len(buf)}

# Fixed blob: constant-high regardless of replica count. Correct fix is vertical, not an HPA target.
@app.post("/allocate")
def allocate(mb: int = 100):
    with _lock:
        _hold.append(bytearray(mb * 1024 * 1024))
    return {"held_mb": sum(len(b) for b in _hold) // (1024 * 1024)}

@app.post("/release")
def release():
    with _lock:
        _hold.clear()
    return {"held_mb": 0}
