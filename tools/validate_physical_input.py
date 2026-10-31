"""Exercise production SendInput guards in a disposable local Tk window.

The manual-movement and Escape cases use real synthetic Windows input. They
are isolated checks, never game operations or evidence of a human test.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import threading
import time
import tkinter as tk
from pathlib import Path

import httpx

from majsoul_llm.config import Settings
from majsoul_llm.native import InputDeferred, Window, WindowsMouse
from majsoul_llm.store import utcnow


def production_input_idle():
    settings = Settings.load()
    try:
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.get(f'http://127.0.0.1:{settings.port}/control/status', headers={
                'Authorization':'Bearer '+settings.control_token.get_secret_value()})
            if not response.is_success:
                return False, {'reason':'production_state_not_verified'}
            status = response.json()
    except httpx.ConnectError:
        return True, {'service_connected':False}
    except (httpx.HTTPError, ValueError):
        return False, {'reason':'production_state_not_verified'}
    idle = not (status['enabled'] or status['navigation'].get('active_command')
                or status['navigation'].get('input_owner') or status.get('result_flow',{}).get('active'))
    return idle, {'service_connected':True, 'input_idle':idle}


def run(output):
    idle, gate = production_input_idle()
    if not idle:
        report = {'schema_version':'physical-isolation.v1', 'started_at':utcnow(),
                  'passed':False, 'blocked':'production_input_active_or_unverified',
                  'production_gate':gate, 'game_inputs':0, 'checks':[]}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        print(json.dumps(report))
        return 1
    mouse = WindowsMouse()
    user = mouse.user
    user.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ulong, ctypes.c_size_t]
    previous_foreground, previous_cursor = user.GetForegroundWindow(), mouse.cursor()
    root = tk.Tk()
    root.title('麻将物理输入专项 · 隔离窗口')
    root.geometry('540x240+160+120')
    clicked = []
    tk.Label(root, text='仅验证本窗口，测试结束后自动关闭。').pack(pady=15)
    button = tk.Button(root, text='隔离点击目标', width=22,
                       command=lambda: clicked.append(time.monotonic()))
    button.pack(pady=15)
    root.update()
    hwnd = user.GetAncestor(root.winfo_id(), 2)
    window = Window(hwnd, os.getpid(), root.title())
    report = {'schema_version':'physical-isolation.v1', 'started_at':utcnow(),
              'kind':'real_windows_input_disposable_window', 'game_inputs':0,
              'synthetic_user_interventions':True, 'production_gate':gate, 'checks':[]}
    last_cursor = previous_cursor

    def pump(seconds=.08):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            root.update()
            time.sleep(.003)

    def focus():
        root.deiconify()
        root.lift()
        # Windows may refuse SetForegroundWindow for a new background process.
        # A paired Alt keystroke grants the disposable test input focus without
        # clicking or activating any control in the previous application.
        user.keybd_event(0x12, 0, 0, 0)
        user.keybd_event(0x12, 0, 2, 0)
        user.SetForegroundWindow(hwnd)
        root.focus_force()
        pump()
        if user.GetForegroundWindow() != hwnd:
            report['foreground_failure'] = {'expected_hwnd':hwnd,
                                            'actual_hwnd':user.GetForegroundWindow(),
                                            'window_visible':bool(user.IsWindowVisible(hwnd))}
            raise RuntimeError('isolated_window_not_foreground')

    def point():
        return button.winfo_rootx()+button.winfo_width()//2, button.winfo_rooty()+button.winfo_height()//2

    def record(name, passed, **details):
        report['checks'].append({'name':name, 'passed':bool(passed), **details})

    def rejected(name, *, rect=None, target=None, observed=None, deadline=None):
        before, original = len(clicked), mouse.cursor()
        try:
            mouse.click(window, rect or mouse.rect(window), target or point(),
                        time.monotonic() if observed is None else observed,
                        time.monotonic()+2 if deadline is None else deadline)
            record(name, False, reason='unexpectedly_sent')
        except InputDeferred as error:
            pump()
            record(name, len(clicked)==before and mouse.cursor()==original, reason=str(error))

    def actual_click(name, move_during_press=False):
        nonlocal last_cursor
        before, original = len(clicked), mouse.cursor()
        target, rect = point(), mouse.rect(window)
        outcome = {}
        override = (rect[0]+rect[2]-20, rect[1]+rect[3]-20)
        done = threading.Event()

        def interfere():
            deadline = time.monotonic()+2
            while not done.is_set() and time.monotonic() < deadline:
                if user.GetAsyncKeyState(1) & 0x8000 and mouse.cursor()==target:
                    user.SetCursorPos(*override)
                    outcome['intervention_sent'] = True
                    return
                time.sleep(.001)

        def send():
            try:
                outcome['sent_at'] = mouse.click(window, rect, target, time.monotonic(), time.monotonic()+2)
            except Exception as error:
                outcome['error'] = type(error).__name__
            finally:
                done.set()

        interference = threading.Thread(target=interfere) if move_during_press else None
        if interference:
            interference.start()
        worker = threading.Thread(target=send)
        worker.start()
        while worker.is_alive():
            pump(.01)
        worker.join()
        if interference:
            interference.join()
        pump()
        expected = override if move_during_press else original
        last_cursor = mouse.cursor()
        passed = ('sent_at' in outcome and last_cursor==expected
                  and not (user.GetAsyncKeyState(1) & 0x8000))
        passed &= bool(outcome.get('intervention_sent')) if move_during_press else len(clicked)==before+1
        record(name, passed, cursor_expected=expected, cursor_actual=last_cursor,
               callbacks=len(clicked)-before, **outcome)
        if move_during_press and last_cursor==override:
            user.SetCursorPos(*original)
            last_cursor = original

    try:
        focus()
        report['dpi'] = user.GetDpiForWindow(hwnd)
        actual_click('foreground_click_and_restore')
        old_rect = mouse.rect(window)
        root.geometry('620x270+230+160')
        pump()
        rejected('moved_or_resized_old_geometry_rejected', rect=old_rect)
        actual_click('fresh_geometry_after_move_and_resize')
        rejected('stale_observation_rejected', observed=time.monotonic()-2)
        rejected('expired_deadline_rejected', deadline=time.monotonic()-1)
        rejected('outside_client_rejected', target=(0, 0))
        mouse.stop.set()
        rejected('stop_event_rejected')
        mouse.stop.clear()
        # A real Escape key state reaches exactly the production guard.
        user.keybd_event(0x1B, 0, 0, 0)
        try:
            pump(.03)
            rejected('escape_key_rejected')
            record('escape_latches_stop', mouse.stop.is_set())
        finally:
            user.keybd_event(0x1B, 0, 2, 0)
            mouse.stop.clear()
        actual_click('synthetic_manual_movement_preserved', move_during_press=True)
        cover = tk.Toplevel(root)
        cover.overrideredirect(True)
        cover.attributes('-topmost', True)
        tx, ty = point()
        cover.geometry(f'120x80+{tx-60}+{ty-40}')
        cover.update()
        user.SetForegroundWindow(hwnd)
        pump()
        if user.GetForegroundWindow()==hwnd:
            rejected('covered_target_rejected')
        else:
            record('covered_target_rejected', False, reason='foreground_not_verified')
        cover.destroy()
        overlay = tk.Toplevel(root)
        overlay.title('隔离遮挡窗口')
        overlay.geometry('270x140+260+180')
        overlay.update()
        overlay_hwnd = user.GetAncestor(overlay.winfo_id(), 2)
        user.SetForegroundWindow(overlay_hwnd)
        overlay.focus_force()
        pump()
        rejected('different_foreground_rejected')
        overlay.destroy()
        rect, target = mouse.rect(window), point()
        root.iconify()
        pump()
        rejected('minimized_window_rejected', rect=rect, target=target)
    except Exception as error:
        report['error_type'] = type(error).__name__
    finally:
        root.destroy()
        if mouse.cursor()==last_cursor:
            user.SetCursorPos(*previous_cursor)
        if user.IsWindow(previous_foreground):
            user.SetForegroundWindow(previous_foreground)
        report['finished_at'] = utcnow()
        report['passed'] = not report.get('error_type') and all(c['passed'] for c in report['checks'])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('runtime/physical-input-validation.json'))
    raise SystemExit(run(parser.parse_args().output))
