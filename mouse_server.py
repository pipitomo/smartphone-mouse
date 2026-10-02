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
_move_remainder_x = 0.0
_move_remainder_y = 0.0

_peer_connections = set()

_loop = None  # asyncioのイベントループを後で参照するための入れ物


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
    global _move_dx, _move_dy, _scroll_amount, _move_remainder_x, _move_remainder_y

    while True:
        await asyncio.sleep(0.01)

        if _move_dx != 0 or _move_dy != 0:
            dx = _move_dx + _move_remainder_x
            dy = _move_dy + _move_remainder_y
            _move_dx = 0.0
            _move_dy = 0.0

            int_dx = int(dx)
            int_dy = int(dy)
            # 整数に切り捨てた端数を捨てずに次回へ持ち越す（小さい動きの取りこぼし防止）
            _move_remainder_x = dx - int_dx
            _move_remainder_y = dy - int_dy

            if int_dx != 0 or int_dy != 0:
                try:
                    pyautogui.moveRel(int_dx, int_dy, duration=0)
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
            # クリップボードコピー＋Ctrl+Vは数十ms程度かかることがあり、
            # メインループ上で直接実行するとカーソル移動が一瞬詰まる。
            # 別スレッドに逃がし、カーソル処理を止めないようにする。
            async def _paste(text):
                try:
                    await asyncio.to_thread(pyperclip.copy, text)
                    await asyncio.to_thread(pyautogui.hotkey, "ctrl", "v")
                except Exception as e:
                    print("[ERROR] text:", e)

            # このコールバックはイベントループとは別スレッドから呼ばれることがあるため、
            # create_taskではなくrun_coroutine_threadsafeで確実にループへ渡す
            asyncio.run_coroutine_threadsafe(_paste(data.get("text", "")), _loop)

        elif data_type == "key":
            async def _press(key):
                try:
                    await asyncio.to_thread(pyautogui.press, key)
                except Exception as e:
                    print("[ERROR] press:", e)

            asyncio.run_coroutine_threadsafe(_press(data.get("key", "")), _loop)

        elif data_type == "mousedown":
            try:
                pyautogui.mouseDown()
            except Exception as e:
                print("[ERROR] mouseDown:", e)

        elif data_type == "mouseup":
            try:
                pyautogui.mouseUp()
            except Exception as e:
                print("[ERROR] mouseUp:", e)

        elif data_type == "hotkey":
            keys = data.get("keys", [])

            async def _hotkey(keys):
                try:
                    await asyncio.to_thread(pyautogui.hotkey, *keys)
                except Exception as e:
                    print("[ERROR] hotkey:", e)

            asyncio.run_coroutine_threadsafe(_hotkey(keys), _loop)


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
            try:
                pyautogui.mouseUp()  # ドラッグ中に切断された場合の保険
            except Exception:
                pass
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
    global _loop
    _loop = asyncio.get_running_loop()

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
    print("              スマホマウス Ver1.4")
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