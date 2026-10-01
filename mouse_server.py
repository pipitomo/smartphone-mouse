import asyncio
import json
import socket
from pathlib import Path

import pyautogui
import pyperclip
import qrcode
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription


# ============================================================
# 設定
# ============================================================

HTTP_PORT = 8000
HTML_PATH = Path(__file__).parent / "mouse_remote.html"

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0


# ============================================================
# 移動量・スクロール量をここに溜めておき、cursor_moverが一定間隔でまとめて反映する
# ============================================================

_move_dx = 0.0
_move_dy = 0.0
_scroll_amount = 0.0

_peer_connections = set()


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


# ============================================================
# カーソル移動・スクロールの反映タスク（10msごとにまとめて実行）
# ============================================================

async def cursor_mover():
    global _move_dx, _move_dy, _scroll_amount

    while True:
        await asyncio.sleep(0.01)

        if _move_dx != 0 or _move_dy != 0:
            dx, dy = _move_dx, _move_dy
            _move_dx = 0.0
            _move_dy = 0.0
            try:
                pyautogui.moveRel(dx, dy, duration=0)
            except Exception as e:
                print("[ERROR] moveRel:", e)

        if _scroll_amount != 0:
            amount = _scroll_amount
            _scroll_amount = 0.0
            try:
                pyautogui.scroll(int(amount))
            except Exception as e:
                print("[ERROR] scroll:", e)


# ============================================================
# WebRTC DataChannel
# ============================================================

def setup_data_channel(channel):
    print("[DATA] DataChannel:", channel.label)

    @channel.on("message")
    def on_message(message):
        global _move_dx, _move_dy, _scroll_amount

        try:
            data = json.loads(message)
        except Exception:
            return

        data_type = data.get("type")

        if data_type == "move":
            _move_dx += data.get("dx", 0)
            _move_dy += data.get("dy", 0)

        elif data_type == "scroll":
            _scroll_amount += data.get("amount", 0)

        elif data_type == "click":
            try:
                pyautogui.click()
            except Exception as e:
                print("[ERROR] click:", e)

        elif data_type == "rightclick":
            try:
                pyautogui.rightClick()
            except Exception as e:
                print("[ERROR] rightClick:", e)

        elif data_type == "stop":
            pass

        elif data_type == "text":
            # pyautogui.write()はキーを1つずつ押す方式なので日本語が打てない。
            # クリップボード経由のペーストにすることで文字種を問わず確実に反映する。
            try:
                pyperclip.copy(data.get("text", ""))
                pyautogui.hotkey("ctrl", "v")
            except Exception as e:
                print("[ERROR] text:", e)

        elif data_type == "key":
            try:
                pyautogui.press(data.get("key", ""))
            except Exception as e:
                print("[ERROR] press:", e)


# ============================================================
# WebRTC Offer
# ============================================================

async def offer_handler(request):
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    _peer_connections.add(pc)
    print("[WEBRTC] 接続開始")

    @pc.on("datachannel")
    def on_datachannel(channel):
        setup_data_channel(channel)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        print("[WEBRTC] connectionState:", pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            await pc.close()
            _peer_connections.discard(pc)

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.json_response({
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type,
    })


async def index_handler(request):
    return web.FileResponse(HTML_PATH)


# ============================================================
# メイン
# ============================================================

async def main():
    ip = get_local_ip()
    url = f"http://{ip}:{HTTP_PORT}/mouse_remote.html"

    app = web.Application()
    app.router.add_get("/mouse_remote.html", index_handler)
    app.router.add_post("/offer", offer_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HTTP_PORT)
    await site.start()

    print()
    print("=" * 60)
    print("              スマホマウス Ver1.3")
    print("=" * 60)
    print()
    print("スマホで以下のQRコードを読み取ってください。")
    print()
    print(f"URL: {url}")
    print()

    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)

    print()
    print("=" * 60)
    print("接続待機中...")
    print("=" * 60)
    print()

    asyncio.create_task(cursor_mover())
    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
        print("サーバーを終了しました。")