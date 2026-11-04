"""Windows tray and status windows for the local voice device."""

import ctypes
import queue
import sys
import threading
from collections import deque
from ctypes.wintypes import RECT
from datetime import datetime

from .device import BackendUnavailable, backend_health, listen
from .listener_control import ListenerControl, ListenerStopped


STATUS = {
    "connecting": ("连接中", "#64748b"),
    "waiting": ("等待唤醒", "#0f766e"),
    "awakened": ("已唤醒 · 正在采音", "#1d4ed8"),
    "processing": ("正在处理", "#7c3aed"),
    "playing": ("正在播放", "#0369a1"),
    "pausing": ("正在暂停", "#a16207"),
    "paused": ("已暂停", "#64748b"),
    "failed": ("连接或监听失败", "#b91c1c"),
}

ACCENT = "#5545c7"
TRANSPARENT = "#ff00ff"


def server_status(config):
    try:
        health = backend_health(config)
    except BackendUnavailable:
        return "不可达", "#b91c1c"
    mode = {"s2s": "S2S", "cascade": "串联"}.get(health.get("mode"), "")
    label, color = {
        "configured": ("可连接", "#0f766e"),
        "configuration_required": ("待配置", "#a16207"),
    }.get(health.get("status"), ("未就绪", "#a16207"))
    return f"{label} · {mode}" if mode else label, color


def badge(size, *, bubble=False, status_color=None):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (256, 256))
    draw = ImageDraw.Draw(image)
    if bubble:
        draw.ellipse((5, 5, 250, 250), fill="white", outline="#ddddeb", width=5)
        draw.rounded_rectangle((59, 59, 197, 197), radius=40, fill=ACCENT)
        bars = [(86, 110, 101, 147), (120, 89, 135, 166), (154, 103, 169, 152)]
    else:
        draw.rounded_rectangle((12, 12, 244, 244), radius=66, fill=ACCENT)
        bars = [(61, 101, 86, 155), (115, 63, 140, 193), (169, 89, 194, 167)]
    for bar in bars:
        draw.rounded_rectangle(bar, radius=12, fill="white")
    if status_color:
        dot = (183, 183, 228, 228) if bubble else (175, 175, 249, 249)
        draw.ellipse(dot, fill=status_color, outline="white", width=8 if bubble else 12)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def run_tray(config):
    if sys.platform != "win32":
        raise ValueError("系统托盘入口仅支持 Windows。")
    ctypes.windll.shcore.SetProcessDpiAwareness(1)  # System DPI, before Tk creates windows.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Findtools.Xiaomei.Voice")
    import tkinter as tk
    from tkinter import ttk
    from tkinter.scrolledtext import ScrolledText

    import pystray
    from PIL import ImageTk

    root = tk.Tk()
    root.withdraw()
    root.title("小美语音")
    scale = root.winfo_fpixels("1i") / 96

    def px(value):
        return round(value * scale)

    root.iconphoto(True, *(ImageTk.PhotoImage(badge(size), master=root) for size in (16, 24, 32, 48, 64)))
    root.geometry(f"{px(620)}x{px(410)}")
    root.minsize(px(360), px(240))
    root.protocol("WM_DELETE_WINDOW", root.withdraw)
    events = queue.SimpleQueue()
    history = deque(maxlen=200)
    state = "connecting"
    control = None
    worker = None
    health_stop = threading.Event()
    backend = ("检查中", "#64748b")

    def send(event):
        return lambda *args: events.put((event, ""))

    status = tk.StringVar()
    server_text = tk.StringVar()
    header = ttk.Frame(root, padding=(px(16), px(12)))
    header.pack(fill="x")
    device_label = ttk.Label(header, textvariable=status, font=("Microsoft YaHei UI", 11, "bold"))
    device_label.pack(anchor="w")
    backend_label = ttk.Label(header, textvariable=server_text, font=("Microsoft YaHei UI", 10))
    backend_label.pack(anchor="w", pady=(px(4), 0))
    tabs = ttk.Notebook(root)
    tabs.pack(fill="both", expand=True, padx=px(12), pady=(0, px(12)))
    logs = {}
    for key, title in (("wake", "唤醒日志"), ("activity", "交互过程")):
        page = ttk.Frame(tabs)
        tabs.add(page, text=title)
        log = ScrolledText(page, wrap="word", state="disabled", font=("Microsoft YaHei UI", 10),
                           padx=px(10), pady=px(8), spacing3=px(4), relief="flat", borderwidth=0)
        log.pack(fill="both", expand=True)
        logs[key] = log

    floating = tk.Toplevel(root)
    floating.withdraw()
    floating.title("小美语音状态")
    floating.overrideredirect(True)
    floating.attributes("-topmost", True)
    floating.attributes("-transparentcolor", TRANSPARENT)
    floating.configure(bg=TRANSPARENT)
    floating_button = tk.Button(floating, command=send("activity"), cursor="hand2",
                                relief="flat", borderwidth=0, highlightthickness=0, padx=0, pady=0,
                                bg=TRANSPARENT, activebackground=TRANSPARENT)
    floating_button.pack(fill="both", expand=True)
    bubble_image = None
    badge_color = None
    floating_shown = False

    def position_bubble():
        area = RECT(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0)  # SPI_GETWORKAREA
        size = px(58)
        x = area.right - size - px(18)
        y = area.bottom - size - px(18)
        floating.geometry(f"{size}x{size}+{max(0, x)}+{max(0, y)}")

    def toggle_listener(*args):
        events.put(("resume" if state in ("paused", "failed") else "pause", ""))

    item = pystray.MenuItem
    icon = pystray.Icon("xiaomei-voice", badge(64, status_color=STATUS[state][1]), "小美语音", menu=pystray.Menu(
        item(lambda _: f"设备 · {STATUS[state][0]}", send("activity")),
        item(lambda _: f"服务端 · {backend[0]}", send("activity")),
        pystray.Menu.SEPARATOR,
        item(lambda _: "继续监听" if state in ("paused", "failed") else "暂停监听",
             toggle_listener, enabled=lambda _: state != "pausing"),
        item("显示桌面悬浮图标", send("float"), checked=lambda _: floating_shown),
        pystray.Menu.SEPARATOR,
        item("唤醒日志", send("wake")),
        item("交互过程", send("activity"), default=True),
        item("退出", send("quit")),
    ))

    def poll_server():
        while not health_stop.is_set():
            events.put(("server_status", server_status(config)))
            health_stop.wait(5)

    def show_log(key):
        tabs.select(0 if key == "wake" else 1)
        root.deiconify()
        root.lift()
        root.focus_force()

    def start_listener():
        nonlocal state, control, worker
        state = "connecting"
        control = ListenerControl(lambda kind, detail: events.put((kind, detail)))

        def run(current):
            try:
                listen(config, control=current)
            except ListenerStopped:
                pass
            except Exception as error:
                if not current.stop.is_set():
                    events.put(("failed", str(error)))
            finally:
                events.put(("finished", ""))

        worker = threading.Thread(target=run, args=(control,), daemon=True)
        worker.start()

    def refresh():
        nonlocal bubble_image, badge_color
        label, color = STATUS[state]
        status.set(f"设备 · {label}  ·  {config['device']['wake_word']}")
        server_text.set(f"服务端 · {backend[0]}")
        device_label.configure(foreground=color)
        backend_label.configure(foreground=backend[1])
        icon.title = f"小美语音 · {label}\n服务端 · {backend[0]}"
        icon.update_menu()
        if color != badge_color:
            icon.icon = badge(64, status_color=color)
            bubble = badge(px(58), bubble=True, status_color=color)
            # The Windows color key makes the corners click-through.
            bubble.putalpha(bubble.getchannel("A").point(lambda alpha: 255 if alpha >= 128 else 0))
            bubble_image = ImageTk.PhotoImage(bubble, master=root)
            floating_button.configure(image=bubble_image)
            badge_color = color
        for key, log in logs.items():
            log.configure(state="normal")
            log.delete("1.0", "end")
            for kind, entry in history:
                if key == "activity" or kind == "awakened":
                    log.insert("end", entry + "\n")
            log.see("end")
            log.configure(state="disabled")

    def tick():
        nonlocal state, worker, floating_shown, backend
        changed = False
        while not events.empty():
            kind, detail = events.get_nowait()
            if kind == "server_status":
                if backend != detail:
                    backend = detail
                    changed = True
                continue
            changed = True
            if kind == "quit":
                control.stop.set()
                root.quit()
                return
            if kind == "pause" and worker is not None and state != "failed":
                control.stop.set()
                state = "pausing"
            elif kind == "resume" and worker is None:
                start_listener()
            elif kind == "finished":
                worker = None
                if state != "failed":
                    state = "paused"
                    history.append(("paused", f"{datetime.now():%H:%M:%S}  已暂停"))
            elif kind == "float":
                floating_shown = not floating_shown
                if floating_shown:
                    position_bubble()
                    floating.deiconify()
                else:
                    floating.withdraw()
            elif kind in ("wake", "activity"):
                show_log(kind)
            elif kind in STATUS or kind in ("reply", "transcript", "error", "backend_unavailable"):
                if state == "pausing":
                    continue
                label = STATUS[kind][0] if kind in STATUS else {
                    "reply": "回复", "transcript": "识别", "error": "错误",
                    "backend_unavailable": "服务端未就绪",
                }[kind]
                history.append((kind, f"{datetime.now():%H:%M:%S}  {label}  {detail[:4000]}".rstrip()))
                if kind in STATUS:
                    state = kind
                if kind in ("failed", "backend_unavailable"):
                    show_log("activity")
        if changed:
            refresh()
        root.after(100, tick)

    icon.run_detached()
    try:
        threading.Thread(target=poll_server, daemon=True).start()
        start_listener()
        refresh()
        root.after(100, tick)
        root.mainloop()
    finally:
        health_stop.set()
        if control:
            control.stop.set()
        icon.visible = False
        icon.stop()
        if worker:
            worker.join(timeout=1)
        root.destroy()
