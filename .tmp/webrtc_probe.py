"""Reproduce the browser's SmallWebRTC handshake against the running bot.

Adds a sendrecv audio transceiver (like the browser does with a mic track),
POSTs the offer to /api/offer, applies the answer, then waits to see the
connection reach 'connected' and counts inbound audio frames (the bot greeting).
"""
import asyncio
import fractions
import time

import aiohttp
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack
from av import AudioFrame
import numpy as np

OFFER_URL = "http://localhost:7860/api/offer"


class SilenceTrack(MediaStreamTrack):
    """Emit silent 20ms 48kHz mono frames so the m-line is genuinely sendrecv."""

    kind = "audio"

    def __init__(self):
        super().__init__()
        self._samples = 960  # 20ms @ 48k
        self._sr = 48000
        self._pts = 0

    async def recv(self):
        await asyncio.sleep(0.02)
        arr = np.zeros((1, self._samples), dtype=np.int16)
        frame = AudioFrame.from_ndarray(arr, format="s16", layout="mono")
        frame.sample_rate = self._sr
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, self._sr)
        self._pts += self._samples
        return frame


async def main():
    pc = RTCPeerConnection()
    pc.addTrack(SilenceTrack())  # sendrecv audio, mirrors the browser mic

    inbound = {"frames": 0, "kind": None}
    connected = asyncio.Event()

    @pc.on("connectionstatechange")
    async def on_state():
        print(f"[state] connectionState = {pc.connectionState}")
        if pc.connectionState == "connected":
            connected.set()

    @pc.on("track")
    def on_track(track):
        print(f"[track] received {track.kind} track from bot")
        inbound["kind"] = track.kind

        async def drain():
            while True:
                try:
                    await track.recv()
                    inbound["frames"] += 1
                except Exception:
                    break

        asyncio.ensure_future(drain())

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.05)

    async with aiohttp.ClientSession() as s:
        async with s.post(
            OFFER_URL,
            json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type, "pc_id": None},
        ) as r:
            print(f"[http] POST /api/offer -> {r.status}")
            ans = await r.json()
    print(f"[http] answer keys: {list(ans.keys())}  type={ans.get('type')}  pc_id={ans.get('pc_id')}")

    await pc.setRemoteDescription(RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))

    try:
        await asyncio.wait_for(connected.wait(), timeout=15)
        print("[ok] peer connection CONNECTED")
    except asyncio.TimeoutError:
        print(f"[FAIL] never connected; final state={pc.connectionState}")
        await pc.close()
        return

    # Let the greeting play; count inbound audio frames.
    t0 = time.time()
    await asyncio.sleep(6)
    print(f"[result] inbound {inbound['kind']} frames in 6s: {inbound['frames']} "
          f"({'BOT IS SPEAKING ✓' if inbound['frames'] > 30 else 'NO/low audio ✗'})")
    await pc.close()


asyncio.run(main())
