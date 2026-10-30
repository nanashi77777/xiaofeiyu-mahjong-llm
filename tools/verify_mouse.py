"""Real physical click in a disposable local window; no game/QQ actions."""
import ctypes
import json
import os
import time
import tkinter as tk

from majsoul_llm.native import InputDeferred, Window, WindowsMouse


def main():
    mouse = WindowsMouse()
    root = tk.Tk()
    root.title("麻将鼠标输入隔离验证")
    root.geometry("520x180+160+120")
    result = {"clicked":0}
    label = tk.Label(root,text="本窗口仅验证一次鼠标点击与归位，随后自动关闭。")
    label.pack(pady=12)
    button = tk.Button(root,text="隔离点击目标",command=lambda:result.update(clicked=result["clicked"]+1))
    button.pack(pady=10)
    root.update()
    hwnd = mouse.user.GetAncestor(root.winfo_id(),2)
    window = Window(hwnd,os.getpid(),root.title())
    mouse.user.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    mouse.user.SetForegroundWindow(hwnd)
    root.lift()
    root.focus_force()
    for _ in range(20):
        root.update()
        if mouse.user.GetForegroundWindow() == hwnd:
            break
        time.sleep(.05)
    original = mouse.cursor()
    target = (button.winfo_rootx()+button.winfo_width()//2,button.winfo_rooty()+button.winfo_height()//2)
    rect = mouse.rect(window)
    result["foreground_bound"] = mouse.user.GetForegroundWindow() == hwnd
    result["rect_stable"] = mouse.rect(window) == rect
    if not result["foreground_bound"] or not result["rect_stable"]:
        root.destroy()
        print(json.dumps({**result,"blocked":"isolated window could not become the active desktop window"}))
        return 1
    try:
        clicked = mouse.click(window,rect,target,time.monotonic(),time.monotonic()+2)
        for _ in range(10):
            root.update()
            time.sleep(.01)
        result["cursor_restored"] = mouse.cursor() == original
        result["physical_click_recorded"] = clicked > 0
        mouse.stop.set()
        try:
            mouse.click(window,rect,target,time.monotonic(),time.monotonic()+2)
            result["stop_blocked"] = False
        except InputDeferred:
            result["stop_blocked"] = True
        mouse.stop.clear()
        try:
            mouse.click(window,rect,target,time.monotonic()-2,time.monotonic()+2)
            result["stale_blocked"] = False
        except InputDeferred:
            result["stale_blocked"] = True
    finally:
        root.destroy()
    print(json.dumps(result))
    return not (result["clicked"]==1 and result["cursor_restored"] and result["stop_blocked"] and result["stale_blocked"])


if __name__ == "__main__":
    raise SystemExit(main())
