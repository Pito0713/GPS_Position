#!/usr/bin/env python3
"""
GPS Spoofer Backend v2  —  iOS 26 / pymobiledevice3 Python API
Usage:  sudo python3 gps_spoofer.py

=== 架構說明 ===
用 pymobiledevice3 Python API 維持單一持久連線：
  - 單一 async session 持續存活
  - set_location() 直接呼叫 API，無 process 生死
  - 無 pool、無 reinforce、無 absorb wait
  - 更新速度幾乎即時

=== 啟動方式 ===
sudo python3 gps_spoofer.py
瀏覽器開啟 http://localhost:7788/ui
"""

import asyncio, threading, subprocess, re, os, sys, time, math, random
from typing import Optional
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ── Python 路徑 ───────────────────────────────────────────────────────────────
def _find_python() -> str:
    for p in [
        "/Applications/Xcode.app/Contents/Developer/usr/bin/python3",
        sys.executable,
        "/usr/local/bin/python3",
        "/opt/homebrew/bin/python3",
    ]:
        try:
            r = subprocess.run([p, "-m", "pymobiledevice3", "--help"],
                               capture_output=True, timeout=5)
            if r.returncode == 0:
                print(f"[Init] Python: {p}")
                return p
        except Exception:
            continue
    return sys.executable

PYTHON: str = _find_python()

# ── Tunnel 狀態 ───────────────────────────────────────────────────────────────
tunnel_proc: Optional[subprocess.Popen] = None
tunnel_addr: Optional[str]              = None
tunnel_port: Optional[str]              = None
tunnel_log:  list[str]                  = []

# ── LocationController — 核心 ─────────────────────────────────────────────────
class LocationController:
    """
    持久連線定位控制器。

    架構：
      - 背景執行緒跑 asyncio event loop
      - set_location() 從任意執行緒呼叫，非阻塞
      - 主循環純事件驅動（await event.wait()），無輪詢 wakeup
      - keep-alive 為獨立 asyncio Task，閒置 KEEPALIVE_INTERVAL 秒後才送
      - 每次真實座標送出後重置 keep-alive 計時，路線播放中幾乎不觸發
      - 連線斷開自動重試（指數退避，最長 16s）
    """

    KEEPALIVE_INTERVAL = 5.0  # 閒置超過此秒數才送 keep-alive，降低 iPhone DVT 負載

    def __init__(self):
        self.connected   = False
        self.status      = '未連線'
        self._latest: Optional[tuple[float, float]] = None
        self._last_sent: Optional[tuple[float, float]] = None
        self._lock       = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._event: Optional[asyncio.Event] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, rsd_host: str, rsd_port: int):
        """啟動背景連線執行緒。"""
        self._rsd_host = rsd_host
        self._rsd_port = rsd_port
        self._thread = threading.Thread(
            target=self._run, daemon=True)
        self._thread.start()

    def set_location(self, lat: float, lng: float):
        """從任意執行緒呼叫，非阻塞，立刻傳給 async loop。"""
        with self._lock:
            self._latest = (lat, lng)
        if self._loop and self._event:
            self._loop.call_soon_threadsafe(self._event.set)

    def stop(self):
        """停止並清除位置。"""
        self.connected = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run(self):
        asyncio.run(self._async_main())

    async def _async_main(self):
        from pymobiledevice3.remote.remote_service_discovery import (
            RemoteServiceDiscoveryService)
        from pymobiledevice3.services.dvt.instruments.dvt_provider import (
            DvtProvider)
        from pymobiledevice3.services.dvt.instruments.location_simulation import (
            LocationSimulation)

        self._loop  = asyncio.get_running_loop()
        self._event = asyncio.Event()
        retry_delay = 1.0

        while True:
            try:
                self.status = '連線中...'
                print(f"[Ctrl] Connecting to {self._rsd_host}:{self._rsd_port}")

                async with RemoteServiceDiscoveryService(
                        (self._rsd_host, int(self._rsd_port))) as rsd:
                    async with DvtProvider(rsd) as dvt:
                        async with LocationSimulation(dvt) as loc:
                            self.connected = True
                            self.status    = '裝置已連線'
                            retry_delay    = 1.0
                            print("[Ctrl] Device connected. Ready.")

                            async def _keepalive():
                                try:
                                    while True:
                                        await asyncio.sleep(self.KEEPALIVE_INTERVAL)
                                        if self._last_sent:
                                            await loc.set(
                                                self._last_sent[0],
                                                self._last_sent[1])
                                except asyncio.CancelledError:
                                    pass

                            ka = asyncio.create_task(_keepalive())
                            try:
                                while True:
                                    await self._event.wait()
                                    self._event.clear()

                                    with self._lock:
                                        coord = self._latest
                                        self._latest = None

                                    if coord:
                                        await loc.set(coord[0], coord[1])
                                        self._last_sent = coord
                                        print(f"[Ctrl] set {coord[0]:.5f}, {coord[1]:.5f}")
                                        # 重置 keep-alive 計時（從本次送出起算 KEEPALIVE_INTERVAL）
                                        ka.cancel()
                                        ka = asyncio.create_task(_keepalive())
                            finally:
                                ka.cancel()

            except Exception as e:
                self.connected = False
                self.status    = f'連線中斷：{e}'
                print(f"[Ctrl] Disconnected: {e}")
                print(f"[Ctrl] Retry in {retry_delay:.1f}s...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 16.0)   # 指數退避，最長 16s

    async def clear(self):
        """發送 clear（還原真實 GPS）。"""
        try:
            from pymobiledevice3.remote.remote_service_discovery import (
                RemoteServiceDiscoveryService)
            from pymobiledevice3.services.dvt.instruments.dvt_provider import (
                DvtProvider)
            from pymobiledevice3.services.dvt.instruments.location_simulation import (
                LocationSimulation)
            async with RemoteServiceDiscoveryService(
                    (self._rsd_host, int(self._rsd_port))) as rsd:
                async with DvtProvider(rsd) as dvt:
                    async with LocationSimulation(dvt) as loc:
                        await loc.clear()
        except Exception as e:
            print(f"[Ctrl] clear error: {e}")


controller: Optional[LocationController] = None

# ── 全域狀態 ──────────────────────────────────────────────────────────────────
route_thread: Optional[threading.Thread] = None
route_stop    = threading.Event()
route_pause   = threading.Event()
route_speed   = 5.0

route_state: dict = {
    "active": False, "paused": False,
    "step": 0, "total": 0,
    "loop": 0, "total_loops": 0,
    "lat": None, "lng": None,
}

# ── Tunnel helpers ────────────────────────────────────────────────────────────
def _parse_rsd(line: str) -> None:
    global tunnel_addr, tunnel_port
    for pat in [r'RSD Address:\s*(\S+)', r'address:\s*(fd[0-9a-f:]+)']:
        m = re.search(pat, line, re.I)
        if m:
            tunnel_addr = m.group(1).rstrip(':')
            break
    for pat in [r'RSD Port:\s*(\d{4,5})', r'port:\s*(\d{4,5})']:
        m = re.search(pat, line, re.I)
        if m:
            tunnel_port = m.group(1)
            break
    if tunnel_addr and tunnel_port:
        print(f"[RSD] {tunnel_addr}:{tunnel_port}")

def _read_tunnel(proc: subprocess.Popen) -> None:
    global tunnel_log
    for raw in proc.stdout:
        line = raw.strip()
        tunnel_log.append(line)
        if len(tunnel_log) > 200:
            tunnel_log = tunnel_log[-200:]
        _parse_rsd(line)

# ── 數學工具 ──────────────────────────────────────────────────────────────────
def haversine_km(p1: dict, p2: dict) -> float:
    R = 6371
    la1, la2 = math.radians(p1["lat"]), math.radians(p2["lat"])
    dlng = math.radians(p2["lng"] - p1["lng"])
    a = (math.sin((la2-la1)/2)**2
         + math.cos(la1)*math.cos(la2)*math.sin(dlng/2)**2)
    return R * 2 * math.asin(math.sqrt(min(1.0, a)))

def step_metres(spd: float) -> int:
    """步長（公尺）— 前後端必須一致。"""
    if spd <= 10: return 6
    if spd <= 20: return 8
    if spd <= 40: return 14
    if spd <= 70: return 28
    return 50

def step_interval(spd: float) -> float:
    """
    每步間隔（秒）。
    v2 不需要等待 iOS 確認（API 直接呼叫），最小間隔大幅縮短。
    """
    return max(0.3, (step_metres(spd) / 1000.0 / spd) * 3600)

def interpolate(points: list[dict], speed_kmh: float) -> tuple:
    sm   = step_metres(speed_kmh)
    sk   = sm / 1000.0
    ivl  = step_interval(speed_kmh)
    dense = [{"lat": points[0]["lat"], "lng": points[0]["lng"], "dwell": 0}]
    for i in range(len(points) - 1):
        p1, p2 = points[i], points[i+1]
        n = max(1, int(haversine_km(p1, p2) / sk))
        for j in range(1, n+1):
            t = j / n
            dense.append({
                "lat":   p1["lat"] + (p2["lat"]-p1["lat"]) * t,
                "lng":   p1["lng"] + (p2["lng"]-p1["lng"]) * t,
                "dwell": int(p2.get("dwell", 0)) if j == n else 0,
            })
    return dense, ivl, sm

def rw_next(cur_lat, cur_lng, c_lat, c_lng, radius_m, spd, heading):
    sm   = step_metres(spd)
    deg  = (sm / 1000.0) / 111.0
    dm   = haversine_km({"lat": cur_lat, "lng": cur_lng},
                        {"lat": c_lat,   "lng": c_lng}) * 1000
    to_c = math.degrees(math.atan2(c_lng-cur_lng, c_lat-cur_lat)) % 360
    pull = min(1.0, dm / radius_m) ** 2
    free = (heading + random.uniform(-60, 60)) % 360
    h    = (free*(1-pull) + to_c*pull) % 360
    rad  = math.radians(h)
    clat = max(0.001, math.cos(math.radians(cur_lat)))
    return cur_lat + deg*math.cos(rad), cur_lng + deg*math.sin(rad)/clat, h

# ── 路線工具 ──────────────────────────────────────────────────────────────────
def _wait_if_paused() -> bool:
    while route_pause.is_set():
        route_state["paused"] = True
        time.sleep(0.1)
        if route_stop.is_set():
            return False
    route_state["paused"] = False
    return True

def _do_step(lat: float, lng: float, ivl: float) -> None:
    """
    v2：直接呼叫 controller.set_location()，無需等待。
    API 呼叫幾乎即時，整個 interval 都可用來等待。
    """
    if controller:
        controller.set_location(lat, lng)
    route_stop.wait(timeout=ivl)

# ── API ───────────────────────────────────────────────────────────────────────
@app.route("/status")
def status():
    return jsonify({
        "tunnel_running": tunnel_proc is not None and tunnel_proc.poll() is None,
        "rsd_addr":   tunnel_addr,
        "rsd_port":   tunnel_port,
        "ctrl_connected": controller.connected if controller else False,
        "ctrl_status":    controller.status    if controller else "未初始化",
    })

@app.route("/route/status")
def route_status():
    return jsonify(route_state)

# Tunnel
@app.route("/tunnel/start", methods=["POST"])
def tunnel_start():
    global tunnel_proc, tunnel_addr, tunnel_port, tunnel_log, controller
    if tunnel_proc and tunnel_proc.poll() is None:
        return jsonify({"ok": True, "addr": tunnel_addr, "port": tunnel_port})
    tunnel_addr = tunnel_port = None
    tunnel_log  = []
    try:
        tunnel_proc = subprocess.Popen(
            ["sudo", PYTHON, "-m", "pymobiledevice3", "lockdown", "start-tunnel"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)
        threading.Thread(
            target=_read_tunnel, args=(tunnel_proc,), daemon=True).start()
        deadline = time.time() + 14
        while time.time() < deadline:
            if tunnel_addr and tunnel_port:
                # 啟動 LocationController
                controller = LocationController()
                controller.start(tunnel_addr, tunnel_port)
                return jsonify({
                    "ok": True, "addr": tunnel_addr, "port": tunnel_port})
            time.sleep(0.2)
        return jsonify({
            "ok": False, "msg": "RSD not detected — unlock iPhone and retry"})
    except FileNotFoundError:
        return jsonify({"ok": False, "msg": "pymobiledevice3 not found"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route("/tunnel/set-rsd", methods=["POST"])
def tunnel_set_rsd():
    global tunnel_addr, tunnel_port, controller
    d = request.json or {}
    tunnel_addr = d.get("addr") or tunnel_addr
    tunnel_port = str(d.get("port") or tunnel_port or "")
    print(f"[RSD] set via UI: {tunnel_addr}:{tunnel_port}")
    # 如果 controller 還沒啟動，現在啟動
    if tunnel_addr and tunnel_port and (controller is None or not controller.connected):
        controller = LocationController()
        controller.start(tunnel_addr, tunnel_port)
    return jsonify({"ok": True, "addr": tunnel_addr, "port": tunnel_port})

@app.route("/tunnel/stop", methods=["POST"])
def tunnel_stop():
    global tunnel_proc, tunnel_addr, tunnel_port, controller
    if tunnel_proc:
        tunnel_proc.terminate()
        tunnel_proc = None
    if controller:
        controller.stop()
        controller = None
    tunnel_addr = tunnel_port = None
    return jsonify({"ok": True})

@app.route("/tunnel/logs")
def tunnel_logs():
    return jsonify({"logs": tunnel_log[-50:]})

# Location
@app.route("/location/set", methods=["POST"])
def location_set():
    d = request.json or {}
    lat, lng = d.get("lat"), d.get("lng")
    if lat is None or lng is None:
        return jsonify({"ok": False, "msg": "lat/lng required"})
    if not controller or not controller.connected:
        return jsonify({"ok": False, "msg": "裝置未連線"})
    controller.set_location(float(lat), float(lng))
    return jsonify({"ok": True})

@app.route("/location/clear", methods=["POST"])
def location_clear_ep():
    global controller
    route_stop.set()
    route_state["active"] = False
    if controller:
        # 在背景執行 clear（async）
        def _do_clear():
            try:
                asyncio.run(controller.clear())
            except Exception as e:
                print(f"[clear] {e}")
        threading.Thread(target=_do_clear, daemon=True).start()
    return jsonify({"ok": True})

# Route
@app.route("/route/play", methods=["POST"])
def route_play():
    global route_thread, route_state, route_speed
    d         = request.json or {}
    points    = d.get("points", [])
    speed_kmh = float(d.get("speed", 5))
    loops     = int(d.get("loops", 1))

    if len(points) < 2:
        return jsonify({"ok": False, "msg": "Need at least 2 points"})
    if not controller or not controller.connected:
        return jsonify({"ok": False, "msg": "裝置未連線"})

    route_stop.set()
    if route_thread and route_thread.is_alive():
        route_thread.join(timeout=3)

    route_stop.clear()
    route_pause.clear()
    route_speed = speed_kmh

    dense, ivl, step_m = interpolate(points, speed_kmh)
    route_state.update({
        "active": True, "paused": False,
        "step": 0, "total": len(dense),
        "loop": 1, "total_loops": loops,
        "lat": None, "lng": None,
    })

    def _play():
        nonlocal ivl
        loop_n = 0
        while True:
            loop_n += 1
            route_state["loop"] = loop_n
            print(f"[Route] Loop {loop_n}/{loops or '∞'} — {len(dense)} steps")
            for i, pt in enumerate(dense):
                if route_stop.is_set():
                    route_state["active"] = False; return
                if not _wait_if_paused():
                    route_state["active"] = False; return
                live_spd = route_speed
                ivl = step_interval(live_spd)
                route_state.update({
                    "step": i+1, "lat": pt["lat"], "lng": pt["lng"]})
                print(f"  [{i+1:04d}/{len(dense)}] "
                      f"{pt['lat']:.6f}, {pt['lng']:.6f}  {live_spd}km/h")
                _do_step(pt["lat"], pt["lng"], ivl)
                dwell = int(pt.get("dwell", 0))
                if dwell > 0 and not route_stop.is_set():
                    route_state["paused"] = True
                    route_stop.wait(timeout=dwell)
                    route_state["paused"] = False
            if route_stop.is_set(): break
            if loops != 0 and loop_n >= loops: break
        route_state["active"] = False
        print("[Route] Done.")

    route_thread = threading.Thread(target=_play, daemon=True)
    route_thread.start()
    return jsonify({
        "ok": True, "steps": len(dense), "step_m": step_m,
        "interval": round(ivl, 2), "loops": loops,
        "msg": f"{len(dense)} steps @ {step_m}m/{ivl:.2f}s — {loops or '∞'} loop(s)",
    })

@app.route("/route/pause", methods=["POST"])
def route_pause_ep():
    route_pause.set(); route_state["paused"] = True
    return jsonify({"ok": True})

@app.route("/route/resume", methods=["POST"])
def route_resume_ep():
    route_pause.clear(); route_state["paused"] = False
    return jsonify({"ok": True})

@app.route("/route/speed", methods=["POST"])
def route_speed_ep():
    global route_speed
    route_speed = max(1.0, min(120.0,
                     float((request.json or {}).get("speed", 5))))
    return jsonify({"ok": True, "speed": route_speed})

@app.route("/route/stop", methods=["POST"])
def route_stop_ep():
    route_stop.set()
    route_pause.clear()
    route_state["active"] = False
    return jsonify({"ok": True})

@app.route("/route/random-walk", methods=["POST"])
def route_random_walk():
    global route_thread, route_state, route_speed
    d        = request.json or {}
    c_lat    = float(d.get("center_lat", 0))
    c_lng    = float(d.get("center_lng", 0))
    radius_m = float(d.get("radius_m", 300))
    spd_init = float(d.get("speed", 5))
    loops    = int(d.get("loops", 0))

    if not controller or not controller.connected:
        return jsonify({"ok": False, "msg": "裝置未連線"})

    route_stop.set()
    if route_thread and route_thread.is_alive():
        route_thread.join(timeout=3)

    route_stop.clear()
    route_pause.clear()
    route_speed = spd_init
    route_state.update({
        "active": True, "paused": False,
        "step": 0, "total": -1,
        "loop": 1, "total_loops": loops,
        "lat": c_lat, "lng": c_lng,
    })

    def _walk():
        cur_lat, cur_lng = c_lat, c_lng
        heading = random.uniform(0, 360)
        step_n  = 0
        while True:
            if route_stop.is_set():
                route_state["active"] = False; return
            if not _wait_if_paused():
                route_state["active"] = False; return
            spd = route_speed
            ivl = step_interval(spd)
            cur_lat, cur_lng, heading = rw_next(
                cur_lat, cur_lng, c_lat, c_lng, radius_m, spd, heading)
            step_n += 1
            route_state.update({
                "step": step_n, "lat": cur_lat, "lng": cur_lng})
            print(f"  [RW {step_n}] {cur_lat:.6f}, {cur_lng:.6f}")
            _do_step(cur_lat, cur_lng, ivl)
            if loops != 0 and step_n >= loops * 100: break
        route_state["active"] = False
        print("[RW] Done.")

    route_thread = threading.Thread(target=_walk, daemon=True)
    route_thread.start()
    return jsonify({"ok": True, "msg": f"RW r={radius_m}m @ {spd_init}km/h"})

@app.route("/mounter/mount", methods=["POST"])
def mounter_mount():
    try:
        r = subprocess.run(
            [PYTHON, "-m", "pymobiledevice3", "mounter", "auto-mount"],
            capture_output=True, text=True, timeout=60)
        return jsonify({
            "ok": r.returncode == 0,
            "stdout": r.stdout, "stderr": r.stderr})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route("/ui")
def serve_ui():
    ui_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "index.html")
    if os.path.exists(ui_path):
        return send_file(ui_path)
    return "index.html not found", 404

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if os.geteuid() != 0:
        print("⚠️  Run with sudo: sudo python3 gps_spoofer.py")
        sys.exit(1)
    print("🛰  GPS Spoofer v2 — http://localhost:7788/ui")
    app.run(host="127.0.0.1", port=7788, debug=False)
