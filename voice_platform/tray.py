"""Windows tray and status windows for the local voice device."""

import queue
import sys
import threading
from collections import deque
from datetime import datetime

from .device import listen
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


def run_tray(config):
    if sys.platform != "win32":
        raise ValueError("系统托盘入口仅支持 Windows。")
    import tkinter as tk
    from tkinter import ttk
    from tkinter.scrolledtext import ScrolledText

    import pystray
    from PIL import Image, ImageDraw

    root = tk.Tk()
    root.withdraw()
    root.title("小美语音")
    root.geometry("620x420")
    root.minsize(360, 240)
    root.protocol("WM_DELETE_WINDOW", root.withdraw)
    events = queue.SimpleQueue()
    history = deque(maxlen=200)
    state = "connecting"
    control = None
    worker = None

    def send(event):
        return lambda *args: events.put((event, ""))

    def badge(color):
        icon_image = Image.new("RGBA", (64, 64))
        draw = ImageDraw.Draw(icon_image)
        draw.ellipse((2, 2, 62, 62), fill=color)
        draw.rounded_rectangle((26, 13, 38, 38), radius=6, fill="white")
        draw.arc((20, 21, 44, 44), 0, 180, fill="white", width=3)
        draw.line((32, 44, 32, 51), fill="white", width=3)
        draw.line((25, 51, 39, 51), fill="white", width=3)
        return icon_image

    status = tk.StringVar()
    ttk.Label(root, textvariable=status, padding=(12, 10)).pack(anchor="w")
    tabs = ttk.Notebook(root)
    tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    logs = {}
    for key, title in (("wake", "唤醒日志"), ("activity", "交互过程")):
        page = ttk.Frame(tabs)
        tabs.add(page, text=title)
        log = ScrolledText(page, wrap="word", state="disabled", font=("Microsoft YaHei UI", 10))
        log.pack(fill="both", expand=True)
        logs[key] = log

    floating = tk.Toplevel(root)
    floating.withdraw()
    floating.title("小美语音状态")
    floating.overrideredirect(True)
    floating.attributes("-topmost", True)
    floating_button = tk.Button(floating, command=send("activity"), relief="flat", fg="white",
                                font=("Microsoft YaHei UI", 10), padx=10, pady=7)
    floating_button.pack(fill="both", expand=True)
    floating_shown = False

    item = pystray.MenuItem
    icon = pystray.Icon("xiaomei-voice", badge(STATUS[state][1]), "小美语音", menu=pystray.Menu(
        item(lambda _: STATUS[state][0], None, enabled=False),
        item("暂停监听", send("pause"), enabled=lambda _: state not in ("paused", "pausing", "failed")),
        item("继续监听", send("resume"), enabled=lambda _: state in ("paused", "failed")),
        item("显示桌面悬浮图标", send("float"), checked=lambda _: floating_shown),
        pystray.Menu.SEPARATOR,
        item("唤醒日志", send("wake")),
        item("交互过程", send("activity"), default=True),
        item("退出", send("quit")),
    ))

    def show_log(key):
        tabs.select(0 if key == "wake" else 1)
        root.deiconify()
        root.lift()

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
        label, color = STATUS[state]
        status.set(f"{label}  ·  {config['device']['wake_word']}")
        icon.title = f"小美语音 · {label}"
        icon.icon = badge(color)
        icon.update_menu()
        floating_button.configure(text=f"●  {label}", bg=color, activebackground=color, activeforeground="white")
        for key, log in logs.items():
            log.configure(state="normal")
            log.delete("1.0", "end")
            for kind, entry in history:
                if key == "activity" or kind == "awakened":
                    log.insert("end", entry + "\n")
            log.see("end")
            log.configure(state="disabled")

    def tick():
        nonlocal state, worker, floating_shown
        changed = False
        while not events.empty():
            kind, detail = events.get_nowait()
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
                    floating.update_idletasks()
                    x = root.winfo_screenwidth() - 235
                    y = root.winfo_screenheight() - 120
                    floating.geometry(f"210x38+{max(0, x)}+{max(0, y)}")
                    floating.deiconify()
                else:
                    floating.withdraw()
            elif kind in ("wake", "activity"):
                show_log(kind)
            elif kind in STATUS or kind in ("reply", "transcript", "error"):
                if state == "pausing":
                    continue
                label = STATUS[kind][0] if kind in STATUS else {"reply": "回复", "transcript": "识别", "error": "错误"}[kind]
                history.append((kind, f"{datetime.now():%H:%M:%S}  {label}  {detail[:4000]}".rstrip()))
                if kind in STATUS:
                    state = kind
                if kind == "failed":
                    show_log("activity")
        if changed:
            refresh()
        root.after(100, tick)

    icon.run_detached()
    try:
        start_listener()
        refresh()
        root.after(100, tick)
        root.mainloop()
    finally:
        if control:
            control.stop.set()
        icon.visible = False
        icon.stop()
        if worker:
            worker.join(timeout=1)
        root.destroy()
