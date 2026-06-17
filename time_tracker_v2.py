#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkTrace 工作轨迹工具
功能：
1. 窗口追踪（每5秒记录当前窗口）
2. 锁屏/离开检测
3. 任务标注弹窗（现代简洁风格）
4. 每日任务规划
5. SQLite 数据存储
6. HTML 仪表盘报告
7. 控制面板（设置/统计/当前状态）

使用方法：
1. 确保安装了 Python 3.x
2. 双击运行此脚本
3. 控制面板常驻，可最小化到任务栏
4. 关闭控制面板即停止追踪并生成报告
"""

import os
import sys
import io
import time
import json
import sqlite3
import threading
import ctypes
import struct
import html
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict
import tkinter as tk
from tkinter import messagebox
import webbrowser

# Windows 托盘相关常量（win32gui/win32con 已提供大部分，此处仅保留自定义消息ID）
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 1  # 自定义托盘回调消息
IDM_SHOW = 1001             # 菜单：打开面板
IDM_QUIT = 1002             # 菜单：退出

# ========== 配置 ==========
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "time_tracker.db")
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

# 修复 Windows 控制台编码，并兼容 pythonw.exe 无控制台启动
if sys.platform == 'win32':
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worktrace_start.log")
    if getattr(sys.stdout, 'buffer', None):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    else:
        sys.stdout = open(log_path, 'a', encoding='utf-8', buffering=1)
    if getattr(sys.stderr, 'buffer', None):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    else:
        sys.stderr = sys.stdout

# 默认配置
DEFAULT_CONFIG = {
    "check_interval": 180,          # 检测频率（秒），60-300
    "idle_threshold": 300,          # 离开阈值（秒），60-900
    "reminder_interval": 300,       # 短暂偏离容忍时间（秒），120-1800
    "lock_check_interval": 2,       # 锁屏检测间隔（秒），1-10
    "auto_start_track": True,       # 启动后自动开始追踪
    "minimize_to_tray": True,       # 关闭窗口时最小化到托盘（False=直接退出）
    "auto_launch": False,           # 开机自启动
    "silent_start": False,          # 启动时显示主窗口
    "pomodoro_minutes": 30,         # 单个时段时长（分钟）
    "pomodoro_sound": True,         # 完成时段时是否播放提示音
    "rest_per_pomodoro": 5,         # 每个时段对应的建议休息分钟数
    "no_input_threshold": 180,      # 实际无操作判定阈值（秒）
    "window_x": -1,                 # 窗口位置 X（-1 表示自动）
    "window_y": -1,                 # 窗口位置 Y
    "always_on_top": True,          # 窗口置顶
    "theme": "dark"                 # 主题：dark / light
}

# ========== Cyberpunk 主题色板 ==========
THEMES = {
    "dark": {
        "bg":            "#0A0E1A",   # 深空蓝底
        "bg_elev":       "#0E1424",   # 略亮一档（菜单/对话框）
        "border":        "#1F2A3D",
        "border_strong": "#2A3A55",
        "scanline":      "#0F1626",
        "ink_1":         "#C8D3E6",   # 主文字
        "ink_2":         "#8B9BB8",   # 次文字
        "ink_3":         "#5B6B85",   # 辅助/标签
        "accent":        "#38BDF8",   # 电光蓝
        "pulse":         "#A78BFA",   # 紫色脉冲
        "data":          "#4ADE80",   # 霓虹绿（数字）
        "drift":         "#FB923C",   # 偏离琥珀
        "mute":          "#1C2A40",   # 未填充刻度
        "rule":          "#1A2438"
    },
    "light": {
        "bg":            "#F5F7FB",
        "bg_elev":       "#FFFFFF",
        "border":        "#C5CDD9",
        "border_strong": "#94A3B8",
        "scanline":      "#EDF1F8",
        "ink_1":         "#2A3447",
        "ink_2":         "#4B5874",
        "ink_3":         "#6B7A93",
        "accent":        "#2563EB",
        "pulse":         "#7C3AED",
        "data":          "#15803D",   # 翡翠绿
        "drift":         "#EA580C",
        "mute":          "#D8DFEB",
        "rule":          "#DCE3EE"
    }
}

def theme():
    """获取当前主题色板"""
    return THEMES.get(config.get("theme", "dark"), THEMES["dark"])



def load_config():
    """加载配置"""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                # 合并默认值，防止缺少字段
                cfg = dict(DEFAULT_CONFIG)
                cfg.update(saved)
                return cfg
        except:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    """保存配置"""
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

# 全局配置
config = load_config()

# 尝试导入Windows API
try:
    import win32gui
    import win32process
    import win32api
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
    print("提示：未安装 pywin32，窗口追踪功能将受限")

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

@dataclass
class Task:
    """任务定义"""
    id: str
    name: str
    color: str = "#4A90E2"
    created_at: str = ""
    
@dataclass
class Activity:
    """活动记录"""
    id: int = 0
    task_id: str = ""
    task_name: str = ""
    app_name: str = ""
    window_title: str = ""
    url: str = ""
    start_time: str = ""
    end_time: str = ""
    duration: int = 0
    is_idle: bool = False
    is_locked: bool = False

class Database:
    """数据库管理"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.init_db()
    
    def get_conn(self):
        return sqlite3.connect(self.db_path)
    
    def init_db(self):
        conn = self.get_conn()
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                color TEXT DEFAULT '#4A90E2',
                created_at TEXT,
                is_active INTEGER DEFAULT 1
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                task_name TEXT,
                app_name TEXT,
                window_title TEXT,
                url TEXT,
                start_time TEXT,
                end_time TEXT,
                duration INTEGER,
                is_idle INTEGER DEFAULT 0,
                is_locked INTEGER DEFAULT 0,
                created_date TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                task_id TEXT,
                task_name TEXT,
                planned_duration INTEGER,
                actual_duration INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending'
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def save_task(self, task: Task):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO tasks (id, name, color, created_at, is_active)
            VALUES (?, ?, ?, ?, 1)
        ''', (task.id, task.name, task.color, task.created_at or datetime.now().isoformat()))
        conn.commit()
        conn.close()
    
    def delete_task(self, task_id: str):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('UPDATE tasks SET is_active = 0 WHERE id = ?', (task_id,))
        conn.commit()
        conn.close()
    
    def rename_task(self, task_id: str, new_name: str):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('UPDATE tasks SET name = ? WHERE id = ?', (new_name, task_id))
        conn.commit()
        conn.close()
    
    def get_tasks(self) -> List[Task]:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT id, name, color, created_at FROM tasks WHERE is_active = 1 ORDER BY created_at')
        tasks = [Task(*row) for row in cursor.fetchall()]
        conn.close()
        return tasks
    
    def save_activity(self, activity: Activity):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO activities 
            (task_id, task_name, app_name, window_title, url, start_time, end_time, duration, is_idle, is_locked, created_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            activity.task_id, activity.task_name, activity.app_name,
            activity.window_title, activity.url, activity.start_time,
            activity.end_time, activity.duration, int(activity.is_idle),
            int(activity.is_locked), datetime.now().strftime('%Y-%m-%d')
        ))
        conn.commit()
        conn.close()
    
    def get_today_activities(self) -> List[Activity]:
        conn = self.get_conn()
        cursor = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT id, task_id, task_name, app_name, window_title, url, start_time, end_time, duration, is_idle, is_locked
            FROM activities 
            WHERE created_date = ? 
            ORDER BY start_time
        ''', (today,))
        rows = cursor.fetchall()
        conn.close()
        
        activities = []
        for row in rows:
            activities.append(Activity(
                id=row[0], task_id=row[1], task_name=row[2],
                app_name=row[3], window_title=row[4], url=row[5],
                start_time=row[6], end_time=row[7], duration=row[8],
                is_idle=bool(row[9]), is_locked=bool(row[10])
            ))
        return activities
    
    def get_date_activities(self, date_str: str) -> List[Activity]:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, task_id, task_name, app_name, window_title, url, start_time, end_time, duration, is_idle, is_locked
            FROM activities 
            WHERE created_date = ? 
            ORDER BY start_time
        ''', (date_str,))
        rows = cursor.fetchall()
        conn.close()
        
        activities = []
        for row in rows:
            activities.append(Activity(
                id=row[0], task_id=row[1], task_name=row[2],
                app_name=row[3], window_title=row[4], url=row[5],
                start_time=row[6], end_time=row[7], duration=row[8],
                is_idle=bool(row[9]), is_locked=bool(row[10])
            ))
        return activities
    
    def get_available_dates(self, limit=7) -> List[str]:
        """获取有记录的日期列表"""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT created_date FROM activities ORDER BY created_date DESC LIMIT ?', (limit,))
        dates = [row[0] for row in cursor.fetchall()]
        conn.close()
        return dates

class WindowTracker:
    """窗口追踪器"""
    
    @staticmethod
    def get_active_window_info() -> Dict[str, str]:
        try:
            if not HAS_WIN32:
                return WindowTracker._get_window_info_ctypes()
            
            hwnd = win32gui.GetForegroundWindow()
            if hwnd == 0:
                return {'app': 'idle', 'title': 'idle', 'url': ''}
            
            title = win32gui.GetWindowText(hwnd)
            
            if HAS_PSUTIL:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                try:
                    process = psutil.Process(pid)
                    app_name = process.name().lower()
                except:
                    app_name = 'unknown'
            else:
                app_name = WindowTracker._get_process_name_from_hwnd(hwnd)
            
            return {'app': app_name, 'title': title, 'url': ''}
        except Exception as e:
            return {'app': 'error', 'title': str(e), 'url': ''}
    
    @staticmethod
    def _get_window_info_ctypes() -> Dict[str, str]:
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            
            hwnd = user32.GetForegroundWindow()
            if hwnd == 0:
                return {'app': 'idle', 'title': 'idle', 'url': ''}
            
            length = user32.GetWindowTextLengthW(hwnd)
            title_buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title_buffer, length + 1)
            title = title_buffer.value
            
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            
            try:
                PROCESS_QUERY_INFORMATION = 0x0400
                PROCESS_VM_READ = 0x0010
                h_process = kernel32.OpenProcess(
                    PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
                    False, pid.value
                )
                if h_process:
                    app_name = f"process_{pid.value}"
                    kernel32.CloseHandle(h_process)
                else:
                    app_name = 'unknown'
            except:
                app_name = 'unknown'
            
            return {'app': app_name, 'title': title, 'url': ''}
        except:
            return {'app': 'unknown', 'title': 'unknown', 'url': ''}
    
    @staticmethod
    def _get_process_name_from_hwnd(hwnd: int) -> str:
        return 'unknown'
    
    @staticmethod
    def is_screen_locked() -> bool:
        try:
            user32 = ctypes.windll.User32
            return user32.GetForegroundWindow() == 0
        except:
            return False
    
    @staticmethod
    def get_idle_time() -> int:
        try:
            if not HAS_WIN32:
                return 0
            
            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [
                    ('cbSize', ctypes.c_uint),
                    ('dwTime', ctypes.c_ulong)
                ]
            
            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            user32 = ctypes.windll.User32
            user32.GetLastInputInfo(ctypes.byref(lii))
            
            millis = win32api.GetTickCount() - lii.dwTime
            return int(millis / 1000)
        except:
            return 0


class SystemTray:
    """系统托盘图标 - 使用 win32gui 实现（比 ctypes 更可靠）"""
    
    def __init__(self, panel):
        self.panel = panel
        self._hwnd = None
        self._hicon = None
        self._thread = None
        self._running = True
    
    def start(self):
        """在子线程中启动托盘图标"""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def _run(self):
        """托盘消息循环（在子线程中运行）"""
        import win32gui
        import win32con
        import win32api
        
        hinst = win32api.GetModuleHandle(None)
        
        # 加载图标 - 尝试 Python 图标，回退到系统默认
        try:
            self._hicon = win32gui.LoadIcon(hinst, 1)
        except Exception:
            self._hicon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
        
        # 窗口消息处理映射
        message_map = {
            WM_TRAYICON: self._on_tray_notify,
            win32con.WM_COMMAND: self._on_command,
            win32con.WM_DESTROY: self._on_destroy,
            win32con.WM_CLOSE: self._on_close,
        }
        
        # 注册窗口类
        wnd_class = win32gui.WNDCLASS()
        wnd_class.hInstance = hinst
        wnd_class.lpszClassName = "TimeTrackerTray"
        wnd_class.style = win32con.CS_VREDRAW | win32con.CS_HREDRAW
        wnd_class.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        wnd_class.hbrBackground = win32con.COLOR_WINDOW
        wnd_class.lpfnWndProc = message_map
        
        try:
            win32gui.RegisterClass(wnd_class)
        except win32gui.error:
            pass  # 可能已注册
        
        # 创建隐藏窗口
        self._hwnd = win32gui.CreateWindow(
            "TimeTrackerTray", "TimeTrackerTray",
            0, 0, 0, 0, 0,
            0, 0, hinst, None
        )
        
        # 添加托盘图标
        try:
            flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
            nid = (self._hwnd, 0, flags, WM_TRAYICON, self._hicon, "WorkTrace 工作轨迹")
            win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)
            print("[托盘] 图标已添加到系统托盘")
        except Exception as e:
            print(f"[托盘] 添加图标失败: {e}")
        
        # 消息循环（阻塞式，直到收到 WM_QUIT）
        win32gui.PumpMessages()
    
    def _on_tray_notify(self, hwnd, msg, wparam, lparam):
        """处理托盘回调消息"""
        import win32con
        if lparam == win32con.WM_LBUTTONDBLCLK:
            self._show_panel()
        elif lparam == win32con.WM_RBUTTONUP:
            self._show_context_menu()
        return 0
    
    def _on_command(self, hwnd, msg, wparam, lparam):
        """处理菜单命令"""
        if wparam == IDM_SHOW:
            self._show_panel()
        elif wparam == IDM_QUIT:
            self._quit()
        return 0
    
    def _on_close(self, hwnd, msg, wparam, lparam):
        """处理窗口关闭"""
        import win32gui
        win32gui.DestroyWindow(hwnd)
        return 0
    
    def _on_destroy(self, hwnd, msg, wparam, lparam):
        """处理窗口销毁 - 移除托盘图标并退出消息循环"""
        import win32gui
        try:
            nid = (self._hwnd, 0, 0, 0, 0, "")
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, nid)
            print("[托盘] 图标已移除")
        except Exception:
            pass
        win32gui.PostQuitMessage(0)
        self._running = False
        return 0
    
    def _show_context_menu(self):
        """显示右键菜单"""
        import win32gui
        import win32con
        import win32api
        
        menu = win32gui.CreatePopupMenu()
        win32gui.AppendMenu(menu, win32con.MF_STRING, IDM_SHOW, "打开面板")
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu, win32con.MF_STRING, IDM_QUIT, "退出")
        
        pos = win32gui.GetCursorPos()
        # 必须设置前台窗口，否则菜单不会自动消失
        win32gui.SetForegroundWindow(self._hwnd)
        win32gui.TrackPopupMenu(
            menu, win32con.TPM_LEFTALIGN,
            pos[0], pos[1], 0, self._hwnd, None
        )
        win32api.PostMessage(self._hwnd, win32con.WM_NULL, 0, 0)
        win32gui.DestroyMenu(menu)
    
    def update_tooltip(self, text):
        """更新托盘图标提示文字"""
        import win32gui
        if self._hwnd:
            try:
                flags = win32gui.NIF_TIP
                nid = (self._hwnd, 0, flags, 0, self._hicon, text[:127])
                win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, nid)
            except Exception:
                pass
    
    def _show_panel(self):
        """显示主窗口（在主线程中执行）"""
        if self.panel and self.panel.root:
            self.panel.root.after(0, self.panel.show_window)
    
    def _quit(self):
        """退出应用（在主线程中执行）"""
        if self.panel and self.panel.root:
            self.panel.root.after(0, self.panel.force_quit)
    
    def stop(self):
        """停止托盘 - 向托盘窗口发送关闭消息"""
        import win32gui
        import win32con
        if self._hwnd:
            try:
                win32gui.PostMessage(self._hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
        self._running = False


def _bind_full_drag(win, *widgets, on_snap_save=None):
    """给窗口的所有指定 widget 绑定整窗拖拽 + 边缘磁吸（20px）。
    按钮（cursor='hand2'）和输入框不参与拖拽。
    on_snap_save: 磁吸后可选回调（如保存位置）。"""
    SNAP = 20

    def _start(e):
        w = e.widget
        if isinstance(w, (tk.Entry, tk.Scale, tk.Scrollbar)):
            return
        if str(w.cget('cursor')) == 'hand2':
            return
        win._drag_x = e.x_root - win.winfo_x()
        win._drag_y = e.y_root - win.winfo_y()

    def _move(e):
        if not hasattr(win, '_drag_x'):
            return
        nx = e.x_root - win._drag_x
        ny = e.y_root - win._drag_y
        win.geometry(f'+{nx}+{ny}')

    def _end(e):
        if not hasattr(win, '_drag_x'):
            return
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        ww = win.winfo_width()
        wh = win.winfo_height()
        x = win.winfo_x()
        y = win.winfo_y()
        if x < SNAP:
            x = 0
        elif x > sw - ww - SNAP:
            x = sw - ww
        if y < SNAP:
            y = 0
        elif y > sh - wh - SNAP:
            y = sh - wh
        win.geometry(f'+{x}+{y}')
        if on_snap_save:
            on_snap_save()

    for w in widgets:
        w.bind('<Button-1>', _start, add='+')
        w.bind('<B1-Motion>', _move, add='+')
        w.bind('<ButtonRelease-1>', _end, add='+')
        for child in w.winfo_children():
            if isinstance(child, (tk.Frame, tk.Label, tk.Canvas)):
                child.bind('<Button-1>', _start, add='+')
                child.bind('<B1-Motion>', _move, add='+')
                child.bind('<ButtonRelease-1>', _end, add='+')

    win.bind('<Button-1>', _start, add='+')
    win.bind('<B1-Motion>', _move, add='+')
    win.bind('<ButtonRelease-1>', _end, add='+')


class CyberScrollbar(tk.Canvas):
    def __init__(self, parent, command, width=8):
        t = theme()
        super().__init__(parent, width=width, bg=t["bg_elev"],
                         highlightthickness=0, bd=0, cursor='hand2')
        self.command = command
        self.first = 0.0
        self.last = 1.0
        self.bind('<Button-1>', self._jump)
        self.bind('<B1-Motion>', self._jump)

    def set(self, first, last):
        self.first = float(first)
        self.last = float(last)
        self._draw()

    def _draw(self):
        self.delete('all')
        t = theme()
        h = max(1, self.winfo_height())
        w = max(1, self.winfo_width())
        self.create_rectangle(w // 2 - 1, 0, w // 2 + 1, h,
                              fill=t["mute"], outline='')
        y0 = max(0, int(self.first * h))
        y1 = min(h, max(y0 + 24, int(self.last * h)))
        self.create_rectangle(1, y0, w - 1, y1,
                              fill=t["accent"], outline=t["border_strong"])

    def _jump(self, event):
        h = max(1, self.winfo_height())
        self.command('moveto', max(0.0, min(1.0, event.y / h)))


class ModernDialog:
    """任务切换弹窗 — V3 HUD，支持焦点内键盘选择和输入。"""

    def __init__(self, parent, title: str, message: str, options: List[str] = None,
                 task_ids: List[str] = None, on_task_edit=None, on_task_delete=None):
        self.result = None
        self.options = options or []
        self.task_ids = task_ids or []
        self.on_task_edit = on_task_edit
        self.on_task_delete = on_task_delete
        self._parent = parent
        self._message = message or ""
        self._selected_index = 0 if self.options else 0
        self._input_index = len(self.options)
        self._option_rows = []
        self._focused = False

        self.root = tk.Toplevel(parent)
        self.root.title(title)
        self.root.attributes('-topmost', True)
        self.root.overrideredirect(True)

        t = theme()
        self.root.configure(bg=t["bg_elev"])
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = 480
        chrome_h = 270
        row_h = 37
        max_visible_rows = 7
        visible_rows = min(max(len(self.options), 3), max_visible_rows)
        desired_h = chrome_h + visible_rows * row_h
        max_h = sh - 80
        h = min(max(350, desired_h), max_h)
        self._options_area_h = max(111, min(visible_rows * row_h, h - chrome_h))
        self._needs_option_scroll = len(self.options) > max_visible_rows
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f'{w}x{h}+{x}+{y}')

        self._create_ui(title)

    def _create_ui(self, title: str):
        t = theme()
        self.bd = tk.Frame(self.root, bg=t["bg_elev"], highlightthickness=1,
                           highlightbackground=t["border_strong"])
        self.bd.pack(fill='both', expand=True)

        inner = tk.Frame(self.bd, bg=t["bg_elev"])
        inner.pack(fill='both', expand=True)

        self._scanline_canvas = tk.Canvas(inner, bg=t["bg_elev"], highlightthickness=0, bd=0)
        self._scanline_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._draw_scanline()

        hdr = tk.Frame(inner, bg=t["bg_elev"])
        hdr.pack(fill='x', padx=16, pady=(14, 0))

        rv = tk.Canvas(hdr, width=18, height=14, bg=t["bg_elev"],
                       highlightthickness=0, bd=0)
        rv.pack(side='left')
        rv.create_oval(2, 4, 8, 10, fill=t["ink_3"], outline='')
        rv.create_oval(10, 4, 16, 10, fill=t["ink_3"], outline='')

        tk.Label(hdr, text="> 选择任务",
                 font=('Microsoft YaHei', 13, 'bold'),
                 bg=t["bg_elev"], fg=t["accent"], anchor='w',
                 cursor='fleur').pack(side='left', padx=(4, 0))

        close_lbl = tk.Label(hdr, text="x",
                             font=('Microsoft YaHei', 11, 'bold'),
                             bg=t["bg_elev"], fg=t["ink_3"], cursor='hand2')
        close_lbl.pack(side='right')
        close_lbl.bind('<Button-1>', lambda e: self._close())
        close_lbl.bind('<Enter>', lambda e: close_lbl.config(fg=t["drift"]))
        close_lbl.bind('<Leave>', lambda e: close_lbl.config(fg=t["ink_3"]))

        app, win_title = self._parse_context()
        guide = tk.Frame(inner, bg=t["bg_elev"])
        guide.pack(fill='x', padx=16, pady=(10, 6))
        tk.Label(guide, text="已检测到当前应用与窗口",
                 font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["bg_elev"], fg=t["accent"], anchor='w').pack(fill='x')

        readout = tk.Frame(inner, bg=t["bg"], highlightthickness=1,
                           highlightbackground=t["border"])
        readout.pack(fill='x', padx=16, pady=(0, 6))

        top = tk.Frame(readout, bg=t["bg"], padx=10, pady=7)
        top.pack(fill='x')
        tk.Label(top, text="APP",
                 font=('JetBrains Mono', 8, 'bold'),
                 bg=t["bg"], fg=t["pulse"], width=5, anchor='w').pack(side='left')
        tk.Label(top, text=app[:18] or "W / TRACE",
                 font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["bg"], fg=t["accent"], anchor='w').pack(side='left', fill='x', expand=True)

        bottom = tk.Frame(readout, bg=t["bg"], padx=10)
        bottom.pack(fill='x', pady=(0, 7))
        tk.Label(bottom, text="窗口 · " + (win_title[:46] or title),
                 font=('Microsoft YaHei', 9),
                 bg=t["bg"], fg=t["ink_2"], anchor='w').pack(fill='x')

        tk.Label(inner, text="请选择要切换的任务，或在下方创建新任务。",
                 font=('Microsoft YaHei', 8),
                 bg=t["bg_elev"], fg=t["ink_3"], anchor='w').pack(fill='x', padx=16, pady=(0, 8))

        options_shell = tk.Frame(inner, bg=t["bg_elev"], height=self._options_area_h)
        options_shell.pack(fill='x', padx=16)
        options_shell.pack_propagate(False)

        if self._needs_option_scroll:
            options_canvas = tk.Canvas(options_shell, bg=t["bg_elev"],
                                       highlightthickness=0, bd=0)
            options_scroll = CyberScrollbar(options_shell, options_canvas.yview)
            options_wrap = tk.Frame(options_canvas, bg=t["bg_elev"])
            options_wrap.bind('<Configure>',
                              lambda e: options_canvas.configure(scrollregion=options_canvas.bbox('all')))
            self._options_window = options_canvas.create_window((0, 0), window=options_wrap, anchor='nw', width=418)
            options_canvas.configure(yscrollcommand=options_scroll.set)
            options_canvas.bind('<Configure>', lambda e: options_canvas.itemconfigure(self._options_window, width=e.width - 12))
            options_canvas.pack(side='left', fill='both', expand=True)
            options_scroll.pack(side='right', fill='y', padx=(4, 0))
            options_canvas.bind('<MouseWheel>', self._on_options_mousewheel)
            options_wrap.bind('<MouseWheel>', self._on_options_mousewheel)
            self._options_canvas = options_canvas
            self._options_scroll = options_scroll
        else:
            options_wrap = tk.Frame(options_shell, bg=t["bg_elev"])
            options_wrap.pack(fill='both', expand=True)
            self._options_canvas = None

        if self.options:
            for i, opt in enumerate(self.options):
                self._make_option_row(options_wrap, i, opt)
        else:
            empty = tk.Frame(options_wrap, bg=t["bg"], highlightthickness=1,
                             highlightbackground=t["border"])
            empty.pack(fill='x', pady=(0, 8))
            tk.Label(empty, text="NO TASKS · ADD BELOW",
                     font=('JetBrains Mono', 10, 'bold'),
                     bg=t["bg"], fg=t["accent"]).pack(pady=15)

        self.input_wrap = tk.Frame(inner, bg=t["bg"], highlightthickness=1,
                                   highlightbackground=t["border"])
        self.input_wrap.pack(fill='x', padx=16, pady=(8, 0))

        input_row = tk.Frame(self.input_wrap, bg=t["bg"], padx=10, pady=7)
        input_row.pack(fill='x')
        tk.Label(input_row, text=">",
                 font=('JetBrains Mono', 11, 'bold'),
                 bg=t["bg"], fg=t["pulse"]).pack(side='left')

        self.entry = tk.Entry(input_row, font=('Microsoft YaHei', 10),
                              bg=t["bg"], fg=t["ink_1"],
                              insertbackground=t["accent"],
                              relief='flat', bd=0)
        self.entry.pack(side='left', fill='x', expand=True, padx=(7, 0))
        self.entry.insert(0, "输入新任务名")
        self.entry.bind('<FocusIn>', lambda e: self._on_entry_focus())
        self.entry.bind('<Button-1>', lambda e: self._select_input(), add='+')
        self.entry.bind('<Return>', lambda e: self._confirm())
        self.entry.bind('<Escape>', lambda e: self._close())
        self.entry.bind('<Up>', lambda e: self._entry_move_to_options())
        self.entry.bind('<Down>', lambda e: self._entry_move_to_options())

        help_row = tk.Frame(inner, bg=t["bg_elev"])
        help_row.pack(fill='x', padx=16, pady=(8, 12))

        for txt, clr in [
            ("↑/↓ 选择", t["ink_3"]),
            ("Enter 确认", t["ink_3"]),
            ("输入框新建", t["ink_3"]),
            ("R 休息", t["drift"]),
            ("Esc 取消", t["ink_3"]),
        ]:
            tk.Label(help_row, text=txt,
                     font=('Microsoft YaHei', 8),
                     bg=t["bg_elev"], fg=clr).pack(side='left', padx=(0, 10))

        self.root.bind('<Enter>', lambda e: self._activate_focus())
        self.root.bind('<Leave>', lambda e: self._deactivate_if_pointer_left())
        self.root.bind('<Button-1>', lambda e: self._activate_focus(), add='+')
        self.root.bind('<FocusIn>', lambda e: self._set_panel_focus(True))
        self.root.bind('<FocusOut>', lambda e: self._set_panel_focus(False))
        self.root.bind('<Up>', self._on_up)
        self.root.bind('<Down>', self._on_down)
        self.root.bind('<Return>', self._on_enter)
        self.root.bind('r', lambda e: self._rest() if self._panel_accepts_keys() else None)
        self.root.bind('R', lambda e: self._rest() if self._panel_accepts_keys() else None)
        self.root.bind('<Escape>', lambda e: self._close() if self._panel_accepts_keys() else None)

        self._bind_title_drag(hdr, rv)
        self._refresh_selection()

    def _bind_title_drag(self, *widgets):
        def start(e):
            self.root._drag_x = e.x_root - self.root.winfo_x()
            self.root._drag_y = e.y_root - self.root.winfo_y()

        def move(e):
            if not hasattr(self.root, '_drag_x'):
                return
            nx = e.x_root - self.root._drag_x
            ny = e.y_root - self.root._drag_y
            self.root.geometry(f'+{nx}+{ny}')

        def end(e):
            if hasattr(self.root, '_drag_x'):
                delattr(self.root, '_drag_x')
            if hasattr(self.root, '_drag_y'):
                delattr(self.root, '_drag_y')

        for widget in widgets:
            widget.bind('<Button-1>', start, add='+')
            widget.bind('<B1-Motion>', move, add='+')
            widget.bind('<ButtonRelease-1>', end, add='+')

    def _activate_focus(self):
        self._set_panel_focus(True)
        if self.root.focus_get() != self.entry:
            self.root.focus_set()

    def _deactivate_if_pointer_left(self):
        px = self.root.winfo_pointerx()
        py = self.root.winfo_pointery()
        x0 = self.root.winfo_rootx()
        y0 = self.root.winfo_rooty()
        x1 = x0 + self.root.winfo_width()
        y1 = y0 + self.root.winfo_height()
        if not (x0 <= px <= x1 and y0 <= py <= y1) and self.root.focus_get() != self.entry:
            self._set_panel_focus(False)

    def _set_panel_focus(self, focused: bool):
        self._focused = focused
        t = theme()
        self.bd.config(highlightbackground=t["accent"] if focused else t["border_strong"])

    def _panel_accepts_keys(self):
        return self._focused or self.root.focus_get() in (self.root, self.entry)

    def _draw_scanline(self):
        c = self._scanline_canvas
        c.delete('all')
        cw = c.winfo_width() or 480
        ch = c.winfo_height() or 350
        t2 = theme()
        for y in range(0, ch, 4):
            c.create_line(0, y, cw, y, fill=t2["scanline"], width=1)

    def _on_options_mousewheel(self, event):
        if self._options_canvas:
            self._options_canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')
            return 'break'

    def _parse_context(self):
        msg = self._message.strip()
        if "检测到你在使用：" in msg:
            line = msg.splitlines()[0].replace("检测到你在使用：", "")
            if " - " in line:
                return line.split(" - ", 1)
            return line, "请选择当前任务"
        if "你切换到了：" in msg:
            lines = [x.strip() for x in msg.splitlines() if x.strip()]
            if len(lines) >= 2:
                line = lines[1]
                if " - " in line:
                    return line.split(" - ", 1)
                return line, "确认是否切换任务"
        first = msg.splitlines()[0] if msg else "W / TRACE"
        return "W / TRACE", first

    def _make_option_row(self, parent, idx, opt):
        t = theme()
        bg = t["bg"]
        row = tk.Frame(parent, bg=bg, highlightthickness=1,
                       highlightbackground=t["border"], cursor='hand2')
        row.pack(fill='x', pady=(0, 6))

        stripe = tk.Canvas(row, width=4, height=31, bg=bg,
                           highlightthickness=0, bd=0, cursor='hand2')
        stripe.pack(side='left', fill='y')

        key_lbl = tk.Label(row, text=f"[{idx + 1}]",
                           font=('JetBrains Mono', 10, 'bold'),
                           bg=bg, fg=t["ink_3"], cursor='hand2')
        key_lbl.pack(side='left', padx=(10, 8), pady=6)

        name_lbl = tk.Label(row, text=opt[:28],
                            font=('Microsoft YaHei', 10),
                            bg=bg, fg=t["ink_1"], anchor='w', cursor='hand2')
        name_lbl.pack(side='left', fill='x', expand=True, pady=6)

        edit_btn = None
        del_btn = None
        if idx < len(self.task_ids):
            tid = self.task_ids[idx]
            if self.on_task_edit:
                edit_btn = tk.Label(row, text="改", font=('Microsoft YaHei', 8, 'bold'),
                                    bg=bg, fg=t["ink_3"], cursor='hand2', padx=5)
                edit_btn.pack(side='right', padx=(0, 2))
                edit_btn.bind('<Button-1>', lambda e, tid=tid, opt=opt: self._inline_edit(tid, opt))
            if self.on_task_delete:
                del_btn = tk.Label(row, text="删", font=('Microsoft YaHei', 8, 'bold'),
                                   bg=bg, fg=t["drift"], cursor='hand2', padx=5)
                del_btn.pack(side='right', padx=(0, 8))
                del_btn.bind('<Button-1>', lambda e, tid=tid, opt=opt: self._delete_task(tid, opt))

        row_data = {
            'row': row,
            'stripe': stripe,
            'key': key_lbl,
            'name': name_lbl,
            'edit': edit_btn,
            'delete': del_btn,
        }
        self._option_rows.append(row_data)

        for widget in [w for w in row_data.values() if w is not None and w not in (edit_btn, del_btn)]:
            widget.bind('<Button-1>', lambda e, i=idx: self._select_option(i), add='+')
            widget.bind('<Double-Button-1>', lambda e, i=idx: self._confirm_option(i), add='+')
        for widget in [w for w in row_data.values() if w is not None]:
            widget.bind('<MouseWheel>', self._on_options_mousewheel, add='+')

    def _select_option(self, idx):
        self._activate_focus()
        self._selected_index = idx
        self._refresh_selection()

    def _select_input(self):
        self._selected_index = self._input_index
        self._set_panel_focus(True)
        self._refresh_selection()

    def _entry_move_to_options(self):
        if not self.options:
            return 'break'
        self._selected_index = len(self.options) - 1
        self.root.focus_set()
        self._refresh_selection()
        return 'break'

    def _refresh_selection(self):
        t = theme()
        for idx, data in enumerate(self._option_rows):
            selected = idx == self._selected_index
            bg = t["bg"]
            data['row'].config(highlightbackground=t["accent"] if selected else t["border"])
            data['stripe'].delete('all')
            data['stripe'].create_rectangle(0, 0, 4, 40,
                                            fill=t["accent"] if selected else t["mute"],
                                            outline='')
            data['key'].config(fg=t["accent"] if selected else t["ink_3"], bg=bg)
            data['name'].config(fg=t["accent"] if selected else t["ink_1"], bg=bg)
            if data['edit']:
                data['edit'].config(bg=bg)
            if data['delete']:
                data['delete'].config(bg=bg)

        input_selected = self._selected_index == self._input_index
        self.input_wrap.config(highlightbackground=t["accent"] if input_selected else t["border"])
        if input_selected:
            self.entry.focus_set()
        elif self.root.focus_get() == self.entry:
            self.root.focus_set()
        self._scroll_selected_visible()

    def _scroll_selected_visible(self):
        canvas = getattr(self, '_options_canvas', None)
        if not canvas or not (0 <= self._selected_index < len(self._option_rows)):
            return
        canvas.update_idletasks()
        row = self._option_rows[self._selected_index]['row']
        y = row.winfo_y()
        h = row.winfo_height()
        view_top = canvas.canvasy(0)
        view_bottom = view_top + canvas.winfo_height()
        total_h = max(1, canvas.bbox('all')[3])
        if y < view_top:
            canvas.yview_moveto(y / total_h)
        elif y + h > view_bottom:
            canvas.yview_moveto((y + h - canvas.winfo_height()) / total_h)

    def _on_up(self, event=None):
        if not self._panel_accepts_keys() or self.root.focus_get() == self.entry:
            return
        if self._selected_index == self._input_index:
            self._selected_index = max(0, len(self.options) - 1)
        else:
            self._selected_index = max(0, self._selected_index - 1)
        self._refresh_selection()
        return 'break'

    def _on_down(self, event=None):
        if not self._panel_accepts_keys() or self.root.focus_get() == self.entry:
            return
        if self.options and self._selected_index < len(self.options) - 1:
            self._selected_index += 1
        else:
            self._selected_index = self._input_index
        self._refresh_selection()
        return 'break'

    def _on_enter(self, event=None):
        if not self._panel_accepts_keys() or self.root.focus_get() == self.entry:
            return
        if 0 <= self._selected_index < len(self.options):
            self._confirm_option(self._selected_index)
        elif self._selected_index == self._input_index:
            self._confirm()
        return 'break'

    def _confirm_option(self, idx):
        if 0 <= idx < len(self.options):
            self.result = self.options[idx]
            self.root.destroy()

    def _on_entry_focus(self):
        self._set_panel_focus(True)
        self._selected_index = self._input_index
        self._refresh_selection()
        if self.entry.get() == "输入新任务名":
            self.entry.delete(0, 'end')

    def _select(self, option: str):
        self.result = option
        self.root.destroy()

    def _confirm(self):
        text = self.entry.get().strip()
        if text and text != "输入新任务名":
            self.result = f"NEW:{text}"
            self.root.destroy()
            return
        if 0 <= self._selected_index < len(self.options):
            self._confirm_option(self._selected_index)

    def _rest(self):
        self.result = "REST"
        self.root.destroy()

    def _close(self):
        self.result = None
        self.root.destroy()

    def _inline_edit(self, task_id, option_text):
        if not self.on_task_edit:
            return
        t = theme()
        d = tk.Toplevel(self.root)
        d.overrideredirect(True)
        d.configure(bg=t["bg_elev"])
        d.attributes('-topmost', True)

        f = tk.Frame(d, bg=t["bg_elev"], highlightthickness=1,
                     highlightbackground=t["border_strong"], padx=14, pady=12)
        f.pack()

        tk.Label(f, text=f"重命名: {option_text}",
                 font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["bg_elev"], fg=t["accent"]).pack(anchor='w', pady=(0, 8))

        e = tk.Entry(f, font=('Microsoft YaHei', 10),
                     bg=t["bg"], fg=t["ink_1"],
                     insertbackground=t["accent"], relief='flat', bd=0,
                     highlightthickness=1, highlightbackground=t["border"])
        e.pack(ipady=6, ipadx=8)
        e.insert(0, option_text)
        e.select_range(0, 'end')
        e.focus_set()

        def do_save(ev=None):
            new_name = e.get().strip()
            if new_name and new_name != option_text:
                self.on_task_edit(task_id, new_name)
            self.result = new_name or option_text
            d.destroy()
            self.root.destroy()

        e.bind('<Return>', do_save)
        e.bind('<Escape>', lambda ev: d.destroy())

        self.root.update_idletasks()
        rx = self.root.winfo_x() + (self.root.winfo_width() - 280) // 2
        ry = self.root.winfo_y() + (self.root.winfo_height() - 80) // 2
        d.geometry(f'+{rx}+{ry}')

    def _delete_task(self, task_id, task_name):
        if self.on_task_delete:
            confirmed = self.on_task_delete(task_id, task_name)
            if confirmed:
                self.result = f"DELETE:{task_id}"
                self.root.destroy()

    def show(self) -> Optional[str]:
        self.root.lift()
        self._parent.wait_window(self.root)
        return self.result


class SettingsWindow:
    """设置窗口 — V1 外壳 + 铁轨滑块 + pill 开关 + 金属按钮。"""

    def __init__(self, parent, cfg: dict, on_save=None):
        self.cfg = dict(cfg)
        self.on_save = on_save
        self.parent = parent
        self._vars = {}
        self._sliders = {}      # key -> {'canvas','draw','val_lbl','min','max','step','unit'}
        self._toggles = {}      # key -> {'canvas','draw'}

        t = theme()
        self.win = tk.Toplevel(parent)
        self.win.title("SETTINGS")
        self.win.overrideredirect(True)
        self.win.configure(bg=t["bg_elev"])
        self.win.attributes('-topmost', True)

        self.win.update_idletasks()
        w, h = 460, 600
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.win.geometry(f'{w}x{h}+{x}+{y}')

        self._create_ui()

        self.win.bind('<Escape>', lambda e: self.win.destroy())
        self.win.bind('<Control-s>', lambda e: self._save())

    # ---------- UI 构建 ----------
    def _create_ui(self):
        t = theme()
        # 外框（带 strong border + 内层 elev）
        bd = tk.Frame(self.win, bg=t["bg_elev"], highlightthickness=1,
                      highlightbackground=t["border_strong"])
        bd.pack(fill='both', expand=True)

        # 标题栏
        hdr = tk.Frame(bd, bg=t["bg_elev"])
        hdr.pack(fill='x', padx=14, pady=(12, 8))

        rivet = tk.Canvas(hdr, width=8, height=8, bg=t["bg_elev"],
                          highlightthickness=0)
        rivet.pack(side='left', padx=(0, 6))
        rivet.create_oval(1, 1, 7, 7, fill=t["border_strong"], outline='')

        title_lbl = tk.Label(hdr, text="设置",
                             font=('Microsoft YaHei', 12, 'bold'),
                             bg=t["bg_elev"], fg=t["accent"], anchor='w',
                             cursor='fleur')
        title_lbl.pack(side='left')

        close_lbl = tk.Label(hdr, text="x",
                             font=('JetBrains Mono', 10, 'bold'),
                             bg=t["bg"], fg=t["ink_3"],
                             width=2, height=1, cursor='hand2')
        close_lbl.pack(side='right')
        close_lbl.bind('<Button-1>', lambda e: self.win.destroy())
        close_lbl.bind('<Enter>', lambda e, l=close_lbl: l.config(fg=t["drift"]))
        close_lbl.bind('<Leave>', lambda e, l=close_lbl: l.config(fg=t["ink_3"]))

        # 滚动区
        scroll_wrap = tk.Frame(bd, bg=t["bg_elev"])
        scroll_wrap.pack(fill='both', expand=True, padx=14, pady=(8, 0))

        canvas = tk.Canvas(scroll_wrap, bg=t["bg_elev"],
                           highlightthickness=0, bd=0)
        scrollbar = tk.Scrollbar(scroll_wrap, orient='vertical',
                                 command=canvas.yview)
        content = tk.Frame(canvas, bg=t["bg_elev"])

        content.bind('<Configure>',
                     lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=content, anchor='nw', width=400)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        def _wheel(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), 'units')
        canvas.bind_all('<MouseWheel>', _wheel)
        self._wheel_canvas = canvas

        # ---- 专注节奏 ----
        self._make_plate(content, "专注节奏")
        self._make_slider(content, "专注单元", "pomodoro_minutes",
                          5, 60, self.cfg.get("pomodoro_minutes", 30), 5, "m",
                          "一轮连续投入工作的目标时长，主面板进度条按这个周期循环。")
        self._make_slider(content, "短恢复", "rest_per_pomodoro",
                          1, 30, self.cfg.get("rest_per_pomodoro", 5), 1, "m",
                          "每完成一个专注单元后，系统用于统计建议恢复时间的基准。")
        self._make_slider(content, "长恢复", "long_rest_minutes",
                          5, 60, self.cfg.get("long_rest_minutes", 15), 5, "m",
                          "连续完成多轮专注后建议安排的一段较长恢复时间。")

        # ---- 追踪阈值 ----
        self._make_plate(content, "追踪阈值")
        self._make_slider(content, "检测频率", "check_interval",
                          60, 300, self.cfg["check_interval"], 60, "s",
                          "后台检查当前窗口、空闲状态和偏离状态的间隔。数值越小越敏感。")
        self._make_slider(content, "离开判定", "idle_threshold",
                          60, 900, self.cfg["idle_threshold"], 60, "s",
                          "鼠标键盘无操作超过该时间后，自动记为离开/空闲。")
        self._make_slider(content, "偏离容忍", "reminder_interval",
                          120, 1800, self.cfg["reminder_interval"], 60, "s",
                          "当前窗口与任务上下文不一致持续超过该时间后，弹出确认。")
        self._make_slider(content, "锁屏检测", "lock_check_interval",
                          1, 10, self.cfg["lock_check_interval"], 1, "s",
                          "检查系统是否锁屏的间隔，用于更快记录锁屏/离开。")
        self._make_slider(content, "无操作判定", "no_input_threshold",
                          60, 600, self.cfg.get("no_input_threshold", 180), 30, "s",
                          "无鼠标键盘输入超过该时间后，累计到今日无操作统计。")

        # ---- 启动 ----
        self._make_plate(content, "启动")
        self._make_toggle(content, "完成提示音", "pomodoro_sound")
        self._make_toggle(content, "窗口置顶", "always_on_top")
        self._make_toggle(content, "关闭到托盘", "minimize_to_tray")
        self._make_toggle(content, "静默启动", "silent_start")
        self._make_toggle(content, "自动追踪", "auto_start_track")
        self._make_toggle(content, "开机自启", "auto_launch")

        # ---- 底部按钮（metal）----
        sep_bot = tk.Frame(bd, bg=t["border"], height=1)
        sep_bot.pack(fill='x', padx=14, pady=(8, 0))

        btn_row = tk.Frame(bd, bg=t["bg_elev"])
        btn_row.pack(fill='x', padx=14, pady=12)

        self._make_metal_btn(btn_row, "恢复默认", self._reset_defaults,
                             kind='danger').pack(side='left')

        right_box = tk.Frame(btn_row, bg=t["bg_elev"])
        right_box.pack(side='right')
        self._make_metal_btn(right_box, "取消",
                             lambda: self.win.destroy(),
                             kind='normal').pack(side='left', padx=(0, 8))
        self._make_metal_btn(right_box, "保存", self._save,
                             kind='primary').pack(side='left')

        # 拖拽 — 全窗口
        _bind_full_drag(self.win, self.win)

    # ---------- 视觉组件 ----------
    def _make_plate(self, parent, text):
        """plate-label 区段头：铆钉 + 字 + 横线 fill。"""
        t = theme()
        row = tk.Frame(parent, bg=t["bg_elev"])
        row.pack(fill='x', pady=(12, 6))

        rivet = tk.Canvas(row, width=8, height=8, bg=t["bg_elev"],
                          highlightthickness=0)
        rivet.pack(side='left', padx=(0, 6))
        rivet.create_oval(1, 1, 7, 7, fill=t["border_strong"], outline='')

        tk.Label(row, text=text,
                 font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["bg_elev"], fg=t["ink_2"]
                 ).pack(side='left', padx=(0, 8))


    def _show_help_tip(self, widget, text):
        self._hide_help_tip()
        t = theme()
        tip = tk.Toplevel(self.win)
        tip.overrideredirect(True)
        tip.attributes('-topmost', True)
        tip.configure(bg=t["bg_elev"])
        box = tk.Frame(tip, bg=t["bg_elev"], highlightthickness=1,
                       highlightbackground=t["border_strong"], padx=8, pady=6)
        box.pack()
        tk.Label(box, text=text,
                 font=('Microsoft YaHei', 8),
                 bg=t["bg_elev"], fg=t["ink_2"],
                 justify='left', wraplength=240).pack()
        x = widget.winfo_rootx() + 14
        y = widget.winfo_rooty() + 16
        tip.geometry(f'+{x}+{y}')
        self._help_tip = tip

    def _hide_help_tip(self):
        tip = getattr(self, '_help_tip', None)
        if tip:
            try:
                tip.destroy()
            except Exception:
                pass
        self._help_tip = None

    def _make_slider(self, parent, label, key, min_val, max_val,
                     current, step, unit, help_text=None):
        """铁轨滑块：背景刻度 + accent fill + square knob。"""
        t = theme()
        row = tk.Frame(parent, bg=t["bg_elev"])
        row.pack(fill='x', pady=4)

        # 标签 + 说明
        label_box = tk.Frame(row, bg=t["bg_elev"], width=104, height=24)
        label_box.pack(side='left')
        label_box.pack_propagate(False)
        tk.Label(label_box, text=label,
                 font=('Microsoft YaHei', 9),
                 bg=t["bg_elev"], fg=t["ink_2"],
                 anchor='w'
                 ).pack(side='left')
        if help_text:
            help_lbl = tk.Label(label_box, text="i",
                                font=('JetBrains Mono', 7, 'bold'),
                                bg=t["bg"], fg=t["ink_3"],
                                width=2, cursor='question_arrow',
                                highlightthickness=1,
                                highlightbackground=t["border_strong"])
            help_lbl.pack(side='left', padx=(5, 0))
            help_lbl.bind('<Enter>', lambda e, txt=help_text: self._show_help_tip(e.widget, txt))
            help_lbl.bind('<Leave>', lambda e: self._hide_help_tip())

        # 数值标签
        val_lbl = tk.Label(row, text=f"{current}{unit}",
                           font=('JetBrains Mono', 10, 'bold'),
                           bg=t["bg_elev"], fg=t["data"],
                           anchor='e', width=6)
        val_lbl.pack(side='right')

        # Canvas 绘制 rail
        cw, ch = 220, 22
        c = tk.Canvas(row, width=cw, height=ch, bg=t["bg_elev"],
                      highlightthickness=0, bd=0, cursor='hand2')
        c.pack(side='right', padx=(8, 8))

        var = tk.IntVar(value=current)
        self._vars[key] = var

        def draw():
            t2 = theme()
            c.delete('all')
            v = var.get()
            ratio = (v - min_val) / max(1, (max_val - min_val))
            ratio = max(0, min(1, ratio))
            mid_y = ch // 2
            pad = 6
            track_left = pad
            track_right = cw - pad

            # 背景轨道
            c.create_line(track_left, mid_y, track_right, mid_y,
                          fill=t2["border_strong"], width=1)

            # 5 个刻度
            for i in range(5):
                x = track_left + int((track_right - track_left) * i / 4)
                c.create_line(x, mid_y - 3, x, mid_y + 3,
                              fill=t2["border_strong"], width=1)

            # accent fill
            knob_x = track_left + int((track_right - track_left) * ratio)
            if knob_x > track_left:
                c.create_line(track_left, mid_y, knob_x, mid_y,
                              fill=t2["accent"], width=3)

            # square knob（带 accent border + bg fill 模拟玻璃感）
            ks = 5
            c.create_rectangle(knob_x - ks, mid_y - ks,
                               knob_x + ks, mid_y + ks,
                               fill=t2["bg"], outline=t2["accent"], width=2)

        def set_from_x(px):
            pad = 6
            track_left = pad
            track_right = cw - pad
            ratio = (px - track_left) / max(1, (track_right - track_left))
            ratio = max(0, min(1, ratio))
            v = min_val + ratio * (max_val - min_val)
            # 量化到 step
            v = round(v / step) * step
            v = max(min_val, min(max_val, int(v)))
            var.set(v)
            val_lbl.config(text=f"{v}{unit}")
            draw()

        c.bind('<Button-1>', lambda e: set_from_x(e.x))
        c.bind('<B1-Motion>', lambda e: set_from_x(e.x))

        draw()
        self._sliders[key] = {
            'canvas': c, 'draw': draw, 'val_lbl': val_lbl,
            'min': min_val, 'max': max_val, 'step': step, 'unit': unit,
        }

    def _make_toggle(self, parent, label, key):
        """V2 pill toggle：药丸轨道 + 圆形滑块。"""
        t = theme()
        row = tk.Frame(parent, bg=t["bg_elev"])
        row.pack(fill='x', pady=4)

        tk.Label(row, text=label,
                 font=('Microsoft YaHei', 9),
                 bg=t["bg_elev"], fg=t["ink_2"], anchor='w'
                 ).pack(side='left')

        var = tk.BooleanVar(value=bool(self.cfg.get(key, False)))
        self._vars[key] = var

        cw, ch = 42, 20
        c = tk.Canvas(row, width=cw, height=ch, bg=t["bg_elev"],
                      highlightthickness=0, bd=0, cursor='hand2')
        c.pack(side='right')

        def draw():
            t2 = theme()
            c.delete('all')
            on = var.get()
            r = ch // 2
            track = t2["accent"] if on else t2["mute"]
            outline = t2["accent"] if on else t2["border_strong"]
            c.create_oval(0, 0, 2 * r, 2 * r,
                          fill=track, outline=outline, width=1)
            c.create_oval(cw - 2 * r, 0, cw, 2 * r,
                          fill=track, outline=outline, width=1)
            c.create_rectangle(r, 0, cw - r, 2 * r,
                               fill=track, outline='')
            c.create_line(r, 0, cw - r, 0, fill=outline, width=1)
            c.create_line(r, 2 * r - 1, cw - r, 2 * r - 1,
                          fill=outline, width=1)

            knob_r = r - 3
            kx = cw - r if on else r
            knob_fill = t2["bg"] if on else t2["ink_3"]
            c.create_oval(kx - knob_r, r - knob_r,
                          kx + knob_r, r + knob_r,
                          fill=knob_fill, outline='')

        def toggle(e=None):
            var.set(not var.get())
            draw()

        c.bind('<Button-1>', toggle)
        draw()

        self._toggles[key] = {'canvas': c, 'draw': draw}

    def _make_metal_btn(self, parent, text, on_click, kind='normal'):
        """克制工业按钮。"""
        t = theme()
        if kind == 'primary':
            fg = t["accent"]; bd_color = t["accent"]
        elif kind == 'danger':
            fg = t["drift"]; bd_color = t["drift"]
        else:
            fg = t["ink_2"]; bd_color = t["border_strong"]

        btn = tk.Label(parent, text=text,
                       font=('Microsoft YaHei', 9, 'bold'),
                       bg=t["bg"], fg=fg,
                       padx=14, pady=6,
                       cursor='hand2',
                       highlightthickness=1,
                       highlightbackground=bd_color)
        btn.bind('<Button-1>', lambda e: on_click())
        btn.bind('<Enter>', lambda e, b=btn: b.config(bg=t["bg_elev"]))
        btn.bind('<Leave>', lambda e, b=btn: b.config(bg=t["bg"]))
        return btn

    # ---------- 操作 ----------
    def _save(self):
        for key, var in self._vars.items():
            self.cfg[key] = var.get()
        save_config(self.cfg)

        # 开机自启
        auto = self.cfg.get("auto_launch", False)
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            app_name = "TimeTracker"
            script_path = os.path.join(SCRIPT_DIR, "time_tracker_v2.py")
            pythonw_path = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(pythonw_path):
                pythonw_path = sys.executable
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            if auto:
                launch_cmd = f'"{pythonw_path}" "{script_path}"'
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, launch_cmd)
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            print(f"[开机自启动] 设置失败: {e}")

        if self.on_save:
            self.on_save(self.cfg)
        self.win.destroy()

    def _reset_defaults(self):
        for key, var in self._vars.items():
            var.set(DEFAULT_CONFIG.get(key, var.get()))
        # 刷新滑块/开关
        for key, info in self._sliders.items():
            v = self._vars.get(key)
            if v:
                info['val_lbl'].config(text=f"{v.get()}{info['unit']}")
                info['draw']()
        for key, info in self._toggles.items():
            info['draw']()



class StatsWindow:
    """统计窗口 — V3: V1 外壳 + KPI 卡片 + 本周柱状图 + 月环 + 任务排行。"""

    def __init__(self, parent, db: Database):
        self.db = db
        t = theme()
        self.win = tk.Toplevel(parent)
        self.win.title("STATS")
        self.win.overrideredirect(True)
        self.win.configure(bg=t["bg_elev"])
        self.win.attributes('-topmost', True)

        self.win.update_idletasks()
        self._stats_w = 520
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        x = (sw - self._stats_w) // 2
        y = max(40, (sh - 460) // 2)
        self.win.geometry(f'{self._stats_w}x460+{x}+{y}')

        self._create_ui()
        self._load_data()
        self._fit_height()
        self.win.bind('<Escape>', lambda e: self.win.destroy())

    # ================================================================
    #  UI 构建
    # ================================================================

    def _create_ui(self):
        t = theme()

        # V1 shell: 边框
        bd = tk.Frame(self.win, bg=t["bg_elev"], highlightthickness=1,
                      highlightbackground=t["border_strong"])
        bd.pack(fill='both', expand=True)

        inner = tk.Frame(bd, bg=t["bg_elev"])
        inner.pack(fill='both', expand=True)

        # 扫描线
        self._scanline_canvas = tk.Canvas(inner, bg=t["bg_elev"], highlightthickness=0, bd=0)
        self._scanline_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._draw_scanline()

        # ---- Title bar ----
        titlebar = tk.Frame(inner, bg=t["bg_elev"])
        titlebar.pack(fill='x', padx=18, pady=(14, 0))

        # rivet dots
        rv = tk.Canvas(titlebar, width=18, height=14, bg=t["bg_elev"],
                       highlightthickness=0, bd=0)
        rv.pack(side='left')
        rv.create_oval(2, 4, 8, 10, fill=t["ink_3"], outline='')
        rv.create_oval(10, 4, 16, 10, fill=t["ink_3"], outline='')

        tk.Label(titlebar, text="> 统计",
                 font=('Microsoft YaHei', 13, 'bold'),
                 bg=t["bg_elev"], fg=t["accent"]).pack(side='left', padx=(4, 0))

        close_lbl = tk.Label(titlebar, text="x",
                             font=('Microsoft YaHei', 11, 'bold'),
                             bg=t["bg_elev"], fg=t["ink_3"], cursor='hand2')
        close_lbl.pack(side='right')

        nav = tk.Frame(titlebar, bg=t["bg_elev"])
        nav.place(relx=0.5, rely=0.5, anchor='center')

        prev_btn = tk.Label(nav, text="<",
                            font=('JetBrains Mono', 10, 'bold'),
                            bg=t["bg_elev"], fg=t["ink_3"], cursor='hand2')
        prev_btn.pack(side='left', padx=(0, 6))

        self.date_lbl = tk.Label(nav, text="--",
                                 font=('JetBrains Mono', 11, 'bold'),
                                 bg=t["bg_elev"], fg=t["ink_1"])
        self.date_lbl.pack(side='left')

        next_btn = tk.Label(nav, text=">",
                            font=('JetBrains Mono', 10, 'bold'),
                            bg=t["bg_elev"], fg=t["ink_3"], cursor='hand2')
        next_btn.pack(side='left', padx=(6, 0))
        close_lbl.bind('<Button-1>', lambda e: self.win.destroy())
        close_lbl.bind('<Enter>', lambda e: close_lbl.config(fg=t["drift"]))
        close_lbl.bind('<Leave>', lambda e: close_lbl.config(fg=t["ink_3"]))

        # 标题分隔线
        tk.Frame(inner, bg=t["rule"], height=1).pack(fill='x', padx=18, pady=(10, 0))

        # ---- KPI 卡片行 ----
        kpi_row = tk.Frame(inner, bg=t["bg_elev"])
        kpi_row.pack(fill='x', padx=18, pady=(12, 0))

        self.kpi_cards = {}
        kpi_configs = [
            ("tasks", "任务数", t["accent"]),
            ("total", "总时长", t["data"]),
            ("focused", "专注", t["pulse"]),
            ("efficiency", "效率", t["drift"]),
        ]
        for key, name, color in kpi_configs:
            self.kpi_cards[key] = self._make_kpi_card(kpi_row, name, color)

        # ---- 本周概览 + 月环 ----
        week_block = tk.Frame(inner, bg=t["bg_elev"])
        week_block.pack(fill='x', padx=18, pady=(10, 0))

        # 左侧：柱状图
        chart_col = tk.Frame(week_block, bg=t["bg_elev"])
        chart_col.pack(side='left', fill='x', expand=True)

        self._make_plate(chart_col, "本周概览")

        self.chart_canvas = tk.Canvas(chart_col, bg=t["bg"], height=70,
                                      highlightthickness=0, bd=0)
        self.chart_canvas.pack(fill='x', pady=(8, 2))

        self.chart_labels = tk.Frame(chart_col, bg=t["bg"])
        self.chart_labels.pack(fill='x')

        # 右侧：月环
        ring_col = tk.Frame(week_block, bg=t["bg_elev"], width=110)
        ring_col.pack(side='right', fill='y')
        ring_col.pack_propagate(False)

        self.ring_canvas = tk.Canvas(ring_col, bg=t["bg_elev"], width=80, height=80,
                                     highlightthickness=0, bd=0)
        self.ring_canvas.pack(pady=(10, 0))

        self.ring_meta = tk.Label(ring_col,
                                  text="目标 100h · --",
                                  font=('JetBrains Mono', 9),
                                  bg=t["bg_elev"], fg=t["ink_3"])
        self.ring_meta.pack()

        # ---- 任务排行 ----
        rank_section = tk.Frame(inner, bg=t["bg_elev"])
        rank_section.pack(fill='x', padx=18, pady=(10, 0))

        self._make_plate(rank_section, "任务排行")

        self.rank_frame = tk.Frame(rank_section, bg=t["bg_elev"])
        self.rank_frame.pack(fill='x', pady=(6, 0))

        # ---- Footer ----
        footer = tk.Frame(inner, bg=t["bg_elev"])
        footer.pack(fill='x', padx=18, pady=(10, 14))

        tk.Frame(footer, bg=t["rule"], height=1).pack(fill='x', pady=(0, 8))

        self.footer_lbl = tk.Label(footer,
                                   text="日均 --  |  最长连续 -- 天",
                                   font=('JetBrains Mono', 9),
                                   bg=t["bg_elev"], fg=t["ink_3"])
        self.footer_lbl.pack()

        # 拖拽
        _bind_full_drag(self.win, self.win)

    def _fit_height(self):
        self.win.update_idletasks()
        req_h = min(max(420, self.win.winfo_reqheight() + 2), self.win.winfo_screenheight() - 80)
        x = self.win.winfo_x()
        y = max(40, min(self.win.winfo_y(), self.win.winfo_screenheight() - req_h - 40))
        self.win.geometry(f'{self._stats_w}x{req_h}+{x}+{y}')
        self._draw_scanline()

    def _draw_scanline(self):
        """扫描线纹理（V1 外壳）。"""
        c = self._scanline_canvas
        c.delete('all')
        cw = c.winfo_width() or 520
        ch = c.winfo_height() or 540
        t2 = theme()
        for y in range(0, ch, 4):
            c.create_line(0, y, cw, y, fill=t2["scanline"], width=1)

    def _make_plate(self, parent, text):
        """plate-label：铆钉 + 文字 + 横线填满。"""
        t2 = theme()
        wrap = tk.Frame(parent, bg=t2["bg_elev"])
        wrap.pack(fill='x')

        rv = tk.Canvas(wrap, width=14, height=10, bg=t2["bg_elev"],
                       highlightthickness=0, bd=0)
        rv.pack(side='left')
        rv.create_oval(3, 2, 9, 8, fill=t2["ink_3"], outline='')

        tk.Label(wrap, text=text,
                 font=('Microsoft YaHei', 9, 'bold'),
                 bg=t2["bg_elev"], fg=t2["ink_2"]).pack(side='left', padx=(3, 8))

        line = tk.Frame(wrap, bg=t2["rule"], height=1)
        line.pack(side='left', fill='x', expand=True, padx=(0, 4))

    def _make_kpi_card(self, parent, name, color):
        """KPI 卡片：2px 顶部彩色条纹 + 数值 + 标签。"""
        t2 = theme()
        card = tk.Frame(parent, bg=t2["bg"], highlightthickness=1,
                        highlightbackground=t2["border"])
        card.pack(side='left', expand=True, fill='x', padx=(0, 6))

        # 顶部彩色条纹（2px Canvas 模拟发光）
        stripe = tk.Canvas(card, bg=t2["bg"], height=3,
                           highlightthickness=0, bd=0)
        stripe.pack(fill='x')
        stripe.create_rectangle(0, 0, 200, 3, fill=color, outline='')

        val = tk.Label(card, text="--",
                       font=('JetBrains Mono', 22),
                       bg=t2["bg"], fg=color)
        val.pack(pady=(8, 0))

        lb = tk.Label(card, text=name,
                      font=('Microsoft YaHei', 9),
                      bg=t2["bg"], fg=t2["ink_3"])
        lb.pack(pady=(0, 8))

        return val

    # ================================================================
    #  数据加载
    # ================================================================

    def _load_data(self):
        t = theme()

        # 刷新扫描线（窗口可能 resize 过）
        self._draw_scanline()

        # 今天日期
        today = datetime.now()
        today_str = today.strftime('%Y-%m-%d')
        self.date_lbl.config(text=today_str)

        # 获取今日活动
        activities = self.db.get_date_activities(today_str)

        # ---- KPI ----
        task_summary = {}
        for act in activities:
            if act.task_name not in task_summary:
                task_summary[act.task_name] = 0
            task_summary[act.task_name] += act.duration

        total_sec = sum(task_summary.values())
        work_sec = sum(v for k, v in task_summary.items()
                       if '休息' not in k and '离开' not in k and '锁屏' not in k)
        work_tasks = len([k for k in task_summary
                          if '休息' not in k and '离开' not in k and '锁屏' not in k])

        self.kpi_cards["tasks"].config(text=str(work_tasks))
        self.kpi_cards["total"].config(text=self._fmt_duration(total_sec))
        self.kpi_cards["focused"].config(text=self._fmt_duration(work_sec))
        eff = f"{int(work_sec / total_sec * 100)}%" if total_sec > 0 else "--"
        self.kpi_cards["efficiency"].config(text=eff)

        # ---- 本周柱状图 ----
        weekday = today.weekday()  # Mon=0
        monday = today - timedelta(days=weekday)
        days = [monday + timedelta(days=i) for i in range(7)]

        daily_data = []
        dow_labels = ['一', '二', '三', '四', '五', '六', '日']
        max_daily = 1
        for i, d in enumerate(days):
            ds = d.strftime('%Y-%m-%d')
            if ds > today_str:
                daily_data.append((ds, 0, False, True))
            else:
                acts = self.db.get_date_activities(ds)
                day_work = sum(
                    a.duration for a in acts
                    if not a.is_idle and not a.is_locked
                    and '休息' not in a.task_name and '离开' not in a.task_name
                    and '锁屏' not in a.task_name
                )
                is_today = (ds == today_str)
                daily_data.append((ds, day_work, is_today, False))
                if day_work > max_daily:
                    max_daily = day_work

        self._draw_week_chart(daily_data, max_daily, dow_labels, today_str)

        # ---- 月环 ----
        month_prefix = today.strftime('%Y-%m')
        conn = self.db.get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COALESCE(SUM(duration), 0) FROM activities
            WHERE created_date LIKE ? AND is_idle = 0 AND is_locked = 0
        ''', (f'{month_prefix}%',))
        month_sec = cursor.fetchone()[0]
        conn.close()
        month_hours = month_sec / 3600
        target_hours = 100.0
        pct = min(100, int(month_hours / target_hours * 100))
        self._draw_ring(pct)
        self.ring_meta.config(text=f"目标 {int(target_hours)}h · {int(month_hours)}h")

        # ---- 任务排行 ----
        self._draw_ranking(task_summary, work_sec)

        # ---- Footer ----
        self._update_footer(days, daily_data)

    def _draw_week_chart(self, daily_data, max_daily, dow_labels, today_str):
        """7 天柱状图，today 高亮。"""
        t2 = theme()
        c = self.chart_canvas
        c.delete('all')

        # 清除旧轴标签
        for w in self.chart_labels.winfo_children():
            w.destroy()

        w = c.winfo_width() or 340
        h = 70
        n = len(daily_data)
        gap = 5
        bar_w = max(10, (w - gap * (n + 1)) // n)
        max_val = max(max_daily, 1)

        for i, (ds, sec, is_today, is_future) in enumerate(daily_data):
            x = gap + i * (bar_w + gap)
            bar_h = max(3, (sec / max_val) * (h - 20)) if max_val > 0 else 3
            y = h - 10 - bar_h

            if is_today:
                # today：绿色发光
                for glow in range(3, 0, -1):
                    c.create_rectangle(x - glow, y - glow,
                                       x + bar_w + glow, h - 10 + glow,
                                       fill='', outline=t2["data"],
                                       width=1)
                c.create_rectangle(x, y, x + bar_w, h - 10,
                                   fill=t2["data"], outline=t2["data"])
            elif is_future:
                c.create_rectangle(x, y, x + bar_w, h - 10,
                                   fill=t2["mute"], outline='')
            else:
                fill = t2["accent"] + "40"  # 半透明
                c.create_rectangle(x, y, x + bar_w, h - 10,
                                   fill=t2["accent"], outline=t2["accent"], width=1)
                # 渐变模拟：顶部略亮
                c.create_rectangle(x, y, x + bar_w, y + max(1, bar_h // 3),
                                   fill='', outline=t2["accent"], width=1)

            # 轴标签
            lbl = dow_labels[i]
            lbl_fg = t2["data"] if is_today else t2["ink_3"]
            tk.Label(self.chart_labels, text=lbl,
                     font=('JetBrains Mono', 8),
                     bg=t2["bg"], fg=lbl_fg
                     ).pack(side='left', expand=True)

    def _draw_ring(self, pct):
        """月环进度：Canvas 弧形。"""
        t2 = theme()
        c = self.ring_canvas
        c.delete('all')

        cx, cy, r = 40, 40, 30
        # 背景轨道
        c.create_arc(cx - r, cy - r, cx + r, cy + r,
                     start=0, extent=359.9,
                     style='arc', outline=t2["mute"], width=4)
        # 前景弧
        extent = 359.9 * pct / 100
        c.create_arc(cx - r, cy - r, cx + r, cy + r,
                     start=-90, extent=extent,
                     style='arc', outline=t2["data"], width=4)

        # 中心文字
        pct_text = f"{pct}%" if pct >= 1 else "<1%"
        c.create_text(cx, cy - 3, text=pct_text,
                      font=('JetBrains Mono', 16, 'bold'),
                      fill=t2["data"])
        c.create_text(cx, cy + 14, text="MONTH",
                      font=('JetBrains Mono', 8),
                      fill=t2["ink_3"])

    def _draw_ranking(self, task_summary, total_sec):
        """彩色进度条排行。"""
        t2 = theme()
        for w in self.rank_frame.winfo_children():
            w.destroy()

        if not task_summary:
            tk.Label(self.rank_frame, text="(暂无数据)",
                     font=('Microsoft YaHei', 9),
                     bg=t2["bg_elev"], fg=t2["ink_3"]).pack(pady=10)
            return

        sorted_tasks = sorted(task_summary.items(), key=lambda x: x[1], reverse=True)
        max_dur = max(v for _, v in sorted_tasks) if sorted_tasks else 1
        bar_colors = [t2["data"], t2["accent"], t2["pulse"], t2["drift"]]

        for idx, (name, dur) in enumerate(sorted_tasks):
            row = tk.Frame(self.rank_frame, bg=t2["bg_elev"])
            row.pack(fill='x', pady=2)

            color = bar_colors[idx % len(bar_colors)]

            # 名称
            tk.Label(row, text=name[:14],
                     font=('Microsoft YaHei', 9),
                     bg=t2["bg_elev"], fg=t2["ink_2"], anchor='w', width=14
                     ).pack(side='left')

            # 进度条轨道
            track = tk.Canvas(row, bg=t2["bg"], highlightthickness=0, bd=0,
                              height=8, width=140)
            track.pack(side='left', fill='x', expand=True, padx=(6, 6))

            bar_w = max(4, int((dur / max_dur) * 140))
            track.create_rectangle(0, 0, 140, 8, fill=t2["bg"], outline='')
            track.create_rectangle(0, 0, bar_w, 8, fill=color, outline='')

            # 时长
            tk.Label(row, text=self._fmt_duration(dur),
                     font=('JetBrains Mono', 9, 'bold'),
                     bg=t2["bg_elev"], fg=color, anchor='e', width=7
                     ).pack(side='right')

    def _update_footer(self, days, daily_data):
        """日均 + 最长连续天数。"""
        total_week = sum(d[1] for d in daily_data if not d[3])
        day_count = sum(1 for d in daily_data if not d[3] and d[1] > 0)
        avg_sec = total_week // max(day_count, 1)
        avg_str = f"{avg_sec // 3600}h{avg_sec % 3600 // 60}m"

        # 最长连续
        streak = 0
        max_streak = 0
        for d in reversed(daily_data):
            if not d[3] and d[1] > 0:
                streak += 1
                if streak > max_streak:
                    max_streak = streak
            else:
                streak = 0

        self.footer_lbl.config(
            text=f"日均 {avg_str}  |  最长连续 {max_streak} 天")

    # ================================================================
    #  辅助
    # ================================================================

    def _fmt_duration(self, seconds: int) -> str:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        if h > 0:
            return f"{h}h{m}m"
        return f"{m}m"


class TaskManagerWindow:
    """任务管理窗口 — V3: V1 卡片墙。"""

    def __init__(self, parent, db: Database, tracker):
        self.db = db
        self.tracker = tracker
        self.name_labels = {}

        t = theme()
        self.win = tk.Toplevel(parent)
        self.win.title("TASKS")
        self.win.overrideredirect(True)
        self.win.configure(bg=t["bg_elev"])
        self.win.attributes('-topmost', True)

        self.win.update_idletasks()
        w, h = 420, 400
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.win.geometry(f'{w}x{h}+{x}+{y}')

        self._create_ui()
        self._refresh_list()

        self.win.bind('<Escape>', lambda e: self.win.destroy())

    def _create_ui(self):
        t = theme()
        bd = tk.Frame(self.win, bg=t["bg_elev"], highlightthickness=1,
                      highlightbackground=t["border_strong"])
        bd.pack(fill='both', expand=True)

        inner = tk.Frame(bd, bg=t["bg_elev"])
        inner.pack(fill='both', expand=True)

        self._scanline_canvas = tk.Canvas(inner, bg=t["bg_elev"], highlightthickness=0, bd=0)
        self._scanline_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._draw_scanline()

        # 标题行
        hdr = tk.Frame(inner, bg=t["bg_elev"])
        hdr.pack(fill='x', padx=16, pady=(14, 0))

        rv = tk.Canvas(hdr, width=18, height=14, bg=t["bg_elev"],
                       highlightthickness=0, bd=0)
        rv.pack(side='left')
        rv.create_oval(2, 4, 8, 10, fill=t["ink_3"], outline='')
        rv.create_oval(10, 4, 16, 10, fill=t["ink_3"], outline='')

        tk.Label(hdr, text="> 任务列表",
                 font=('Microsoft YaHei', 13, 'bold'),
                 bg=t["bg_elev"], fg=t["accent"], anchor='w',
                 cursor='fleur').pack(side='left', padx=(4, 0))

        close_lbl = tk.Label(hdr, text="x",
                             font=('Microsoft YaHei', 11, 'bold'),
                             bg=t["bg_elev"], fg=t["ink_3"], cursor='hand2')
        close_lbl.pack(side='right')
        close_lbl.bind('<Button-1>', lambda e: self.win.destroy())
        close_lbl.bind('<Enter>', lambda e: close_lbl.config(fg=t["drift"]))
        close_lbl.bind('<Leave>', lambda e: close_lbl.config(fg=t["ink_3"]))

        tk.Frame(inner, bg=t["rule"], height=1).pack(fill='x', padx=16, pady=(10, 10))

        # 卡片墙
        list_bg = tk.Frame(inner, bg=t["bg_elev"])
        list_bg.pack(fill='both', expand=True, padx=16)

        self.task_list_canvas = tk.Canvas(list_bg, bg=t["bg_elev"],
                                          highlightthickness=0, bd=0)
        self.task_list_scroll = CyberScrollbar(list_bg, self.task_list_canvas.yview)
        self.task_list_frame = tk.Frame(self.task_list_canvas, bg=t["bg_elev"])
        self.task_list_window = self.task_list_canvas.create_window(
            (0, 0), window=self.task_list_frame, anchor='nw', width=376)
        self.task_list_canvas.configure(yscrollcommand=self.task_list_scroll.set)
        self.task_list_frame.bind('<Configure>', self._on_task_list_configure)
        self.task_list_canvas.bind('<Configure>', self._on_task_list_canvas_configure)
        self.task_list_canvas.bind('<MouseWheel>', self._on_task_list_mousewheel)
        self.task_list_frame.bind('<MouseWheel>', self._on_task_list_mousewheel)
        self.task_list_canvas.pack(side='left', fill='both', expand=True)
        self.task_list_scroll.pack(side='right', fill='y', padx=(4, 0))

        # 底部新增行
        add_wrap = tk.Frame(inner, bg=t["bg_elev"], highlightthickness=1,
                            highlightbackground=t["border"])
        add_wrap.pack(fill='x', padx=16, pady=(8, 14))

        add_row = tk.Frame(add_wrap, bg=t["bg"], padx=10, pady=7)
        add_row.pack(fill='x')

        tk.Label(add_row, text=">",
                 font=('JetBrains Mono', 11, 'bold'),
                 bg=t["bg"], fg=t["pulse"]).pack(side='left')

        self.new_entry = tk.Entry(add_row, font=('Microsoft YaHei', 10),
                                  bg=t["bg"], fg=t["ink_1"],
                                  insertbackground=t["accent"],
                                  relief='flat', bd=0)
        self.new_entry.pack(side='left', fill='x', expand=True, padx=(7, 0))
        self.new_entry.insert(0, "输入新任务名")
        self.new_entry.bind('<FocusIn>', self._on_new_entry_focus)
        self.new_entry.bind('<Return>', lambda e: self._add_task())

        _bind_full_drag(self.win, self.win)

    def _draw_scanline(self):
        c = self._scanline_canvas
        c.delete('all')
        cw = c.winfo_width() or 420
        ch = c.winfo_height() or 400
        t2 = theme()
        for y in range(0, ch, 4):
            c.create_line(0, y, cw, y, fill=t2["scanline"], width=1)

    def _on_task_list_configure(self, event=None):
        self.task_list_canvas.configure(scrollregion=self.task_list_canvas.bbox('all'))

    def _on_task_list_canvas_configure(self, event):
        self.task_list_canvas.itemconfigure(self.task_list_window, width=event.width - 14)

    def _on_task_list_mousewheel(self, event):
        self.task_list_canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')
        return 'break'

    def _on_new_entry_focus(self, event=None):
        if self.new_entry.get() == "输入新任务名":
            self.new_entry.delete(0, 'end')

    def _refresh_list(self):
        t = theme()
        self._draw_scanline()
        for widget in self.task_list_frame.winfo_children():
            widget.destroy()

        tasks = self.db.get_tasks()
        if not tasks:
            empty = tk.Frame(self.task_list_frame, bg=t["bg_elev"],
                             highlightthickness=1, highlightbackground=t["border"])
            empty.pack(fill='both', expand=True, pady=12)
            tk.Label(empty, text="ADD FIRST TASK",
                     font=('JetBrains Mono', 14, 'bold'),
                     bg=t["bg_elev"], fg=t["accent"]).pack(expand=True)
            tk.Label(empty, text="输入任务名后按 ENTER",
                     font=('Microsoft YaHei', 9),
                     bg=t["bg_elev"], fg=t["ink_3"]).pack(pady=(0, 44))
            return

        today_activities = self.db.get_today_activities()
        task_duration = {}
        for act in today_activities:
            task_duration[act.task_id] = task_duration.get(act.task_id, 0) + act.duration

        palette = [t["data"], t["accent"], t["pulse"], t["drift"]]
        for idx, task in enumerate(tasks):
            is_current = (self.tracker.current_task
                          and self.tracker.current_task.id == task.id)
            dur = task_duration.get(task.id, 0)
            self._make_task_card(task, is_current, dur, palette[idx % len(palette)])

    def _make_task_card(self, task, is_current, duration, color):
        t = theme()
        card_bg = t["bg"]
        border = t["accent"] if is_current else t["border"]
        card = tk.Frame(self.task_list_frame, bg=card_bg,
                        highlightthickness=1, highlightbackground=border)
        card.pack(fill='x', pady=(0, 8))

        row = tk.Frame(card, bg=card_bg, padx=0, pady=0)
        row.pack(fill='x')

        stripe = tk.Canvas(row, width=4, height=50, bg=card_bg,
                           highlightthickness=0, bd=0)
        stripe.pack(side='left', fill='y')
        stripe.create_rectangle(0, 0, 4, 60, fill=color, outline='')

        info = tk.Frame(row, bg=card_bg, padx=10, pady=8)
        info.pack(side='left', fill='x', expand=True)

        name_color = t["accent"] if is_current else t["ink_1"]
        name_lbl = tk.Label(info, text=task.name[:24],
                            font=('Microsoft YaHei', 10, 'bold'),
                            bg=card_bg, fg=name_color, anchor='w')
        name_lbl.pack(fill='x')
        self.name_labels[task.id] = name_lbl

        pomo_count = duration // max(1, config.get("pomodoro_minutes", 30) * 60)
        meta = "尚未开始" if duration <= 0 else f"{self._fmt_duration(duration)} · {pomo_count} 轮专注"
        tk.Label(info, text=meta,
                 font=('Microsoft YaHei', 8),
                 bg=card_bg, fg=t["ink_3"], anchor='w').pack(fill='x', pady=(2, 0))

        if is_current:
            tk.Label(row, text="ACTIVE",
                     font=('JetBrains Mono', 8, 'bold'),
                     bg=card_bg, fg=t["accent"]).pack(side='left', padx=(0, 8))

        actions = tk.Frame(row, bg=card_bg, padx=8)
        actions.pack(side='right')

        edit_btn = self._make_action_btn(actions, "改", t["ink_2"])
        edit_btn.pack(side='left', padx=(0, 5))
        edit_btn.bind('<Button-1>', lambda e, tsk=task: self._edit_task(tsk, self.name_labels.get(tsk.id)))

        del_btn = self._make_action_btn(actions, "删", t["drift"])
        del_btn.pack(side='left')
        del_btn.bind('<Button-1>', lambda e, tsk=task: self._delete_task(tsk))

        for widget in (card, row, stripe, info, name_lbl, actions, edit_btn, del_btn):
            widget.bind('<MouseWheel>', self._on_task_list_mousewheel, add='+')

    def _make_action_btn(self, parent, text, fg):
        t = theme()
        lbl = tk.Label(parent, text=text,
                       font=('Microsoft YaHei', 9, 'bold'),
                       bg=t["bg"], fg=fg,
                       padx=7, pady=3, cursor='hand2',
                       highlightthickness=1, highlightbackground=t["border"])
        return lbl

    def _edit_task(self, task, label_widget=None):
        t = theme()
        old_name = task.name

        d = tk.Toplevel(self.win)
        d.overrideredirect(True)
        d.configure(bg=t["bg_elev"])
        d.attributes('-topmost', True)

        f = tk.Frame(d, bg=t["bg_elev"], highlightthickness=1,
                     highlightbackground=t["border_strong"], padx=14, pady=12)
        f.pack()

        tk.Label(f, text=f"重命名: {old_name}",
                 font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["bg_elev"], fg=t["accent"]).pack(anchor='w', pady=(0, 8))

        e = tk.Entry(f, font=('Microsoft YaHei', 10),
                     bg=t["bg"], fg=t["ink_1"],
                     insertbackground=t["accent"], relief='flat', bd=0,
                     highlightthickness=1, highlightbackground=t["border"])
        e.pack(ipady=6, ipadx=8)
        e.insert(0, old_name)
        e.select_range(0, 'end')
        e.focus_set()

        def do_save(ev=None):
            new_name = e.get().strip()
            if new_name and new_name != old_name:
                self.db.rename_task(task.id, new_name)
                task.name = new_name
                for tsk in self.tracker.today_tasks:
                    if tsk.id == task.id:
                        tsk.name = new_name
                self._refresh_list()
            d.destroy()

        e.bind('<Return>', do_save)
        e.bind('<Escape>', lambda ev: d.destroy())

        self.win.update_idletasks()
        rx = self.win.winfo_x() + (self.win.winfo_width() - 260) // 2
        ry = self.win.winfo_y() + (self.win.winfo_height() - 80) // 2
        d.geometry(f'+{rx}+{ry}')

    def _delete_task(self, task):
        t = theme()
        d = tk.Toplevel(self.win)
        d.overrideredirect(True)
        d.configure(bg=t["bg_elev"])
        d.attributes('-topmost', True)

        f = tk.Frame(d, bg=t["bg_elev"], highlightthickness=1,
                     highlightbackground=t["border_strong"], padx=16, pady=12)
        f.pack()

        is_current = (self.tracker.current_task
                      and self.tracker.current_task.id == task.id)
        msg = f"删除: {task.name}?"
        if is_current:
            msg += "\n(当前进行中的任务)"

        tk.Label(f, text=msg,
                 font=('Microsoft YaHei', 10),
                 bg=t["bg_elev"], fg=t["drift"]).pack(pady=(0, 10))

        btn_row = tk.Frame(f, bg=t["bg_elev"])
        btn_row.pack()

        def do_delete():
            self.db.delete_task(task.id)
            self.tracker.today_tasks = [t for t in self.tracker.today_tasks
                                        if t.id != task.id]
            if is_current:
                self.tracker.current_task = None
                if self.tracker.current_activity and self.tracker.current_activity.task_id == task.id:
                    self.tracker._end_activity()
                    self.tracker.current_task = None
            self._refresh_list()
            d.destroy()

        yes_btn = tk.Label(btn_row, text="确认",
                           font=('Microsoft YaHei', 9, 'bold'),
                           bg=t["bg_elev"], fg=t["drift"], cursor='hand2',
                           highlightthickness=1, highlightbackground=t["drift"], padx=12, pady=4)
        yes_btn.pack(side='left', padx=(0, 10))
        yes_btn.bind('<Button-1>', lambda e: do_delete())

        no_btn = tk.Label(btn_row, text="取消",
                          font=('Microsoft YaHei', 9),
                          bg=t["bg_elev"], fg=t["ink_2"], cursor='hand2',
                          highlightthickness=1, highlightbackground=t["border"], padx=12, pady=4)
        no_btn.pack(side='left')
        no_btn.bind('<Button-1>', lambda e: d.destroy())

        d.bind('<Escape>', lambda e: d.destroy())

        self.win.update_idletasks()
        rx = self.win.winfo_x() + (self.win.winfo_width() - 220) // 2
        ry = self.win.winfo_y() + (self.win.winfo_height() - 90) // 2
        d.geometry(f'+{rx}+{ry}')

    def _add_task(self):
        name = self.new_entry.get().strip()
        if not name or name == "输入新任务名":
            return

        task = Task(
            id=f"task_{int(time.time())}",
            name=name,
            created_at=datetime.now().isoformat()
        )
        self.db.save_task(task)
        self.tracker.today_tasks.append(task)

        self.new_entry.delete(0, 'end')
        self.new_entry.insert(0, "输入新任务名")
        self._refresh_list()

    def _fmt_duration(self, seconds: int) -> str:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        if h > 0:
            return f"{h}h {m}m"
        return f"{m}m"


class ScanlineCanvas(tk.Canvas):
    """带 Cyberpunk 扫描线纹理 + 主题色背景的画布。

    主窗口背景 + 30 格刻度进度条都用 Canvas 绘制，避免 tkinter 在 Windows
    下边距 / 圆角缺失的视觉妥协。"""

    def __init__(self, master, width, height):
        super().__init__(master, width=width, height=height,
                         highlightthickness=0, bd=0)
        self._w = width
        self._h = height
        self.bind('<Configure>', lambda e: self._resize(e.width, e.height))

    def _resize(self, w, h):
        self._w = w
        self._h = h


class ControlPanel:
    """W / TRACE 主窗口 — Cyberpunk 仪表盘。

    340 x 180，无边框，主题色由 config['theme'] 切换（dark / light）。
    """

    WIDTH = 340
    HEIGHT = 180

    def __init__(self, tracker, silent=False):
        self.tracker = tracker
        self.root = tk.Tk()
        self.root.title("W / TRACE")
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', config.get("always_on_top", True))

        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        cx = config.get("window_x", -1)
        cy = config.get("window_y", -1)
        if cx < 0 or cy < 0:
            cx = sw - self.WIDTH - 24
            cy = 80
        cx = max(0, min(cx, sw - self.WIDTH))
        cy = max(0, min(cy, sh - self.HEIGHT))
        self.root.geometry(f'{self.WIDTH}x{self.HEIGHT}+{cx}+{cy}')

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.tray = SystemTray(self)
        self.tray.start()

        # 状态变量
        self._menu_window = None
        self._child_windows = []     # 子窗口引用，用于主题切换时刷新
        self._colon_visible = True
        self._last_completed = 0
        self._tick_pulse_phase = 0  # 当前刻度脉冲相位 0..1
        self._badge_pulse_phase = 0
        self._tick_pulse_dir = 1
        self._badge_pulse_dir = 1
        self._cached_state = None

        t = theme()
        self.root.configure(bg=t["bg"])
        self._build_ui()
        self._bind_drag()

        # 主刷新 1Hz
        self._update_loop()
        # 冒号闪烁 1Hz
        self._colon_blink_loop()
        # 脉冲动画 60ms 平滑
        self._pulse_loop()

        if silent:
            self.root.withdraw()
        else:
            self.show_window()

        # 首次启动引导：如果还没有任务，延迟弹出任务确认框
        if not self.tracker.today_tasks and self.tracker.running:
            self.root.after(800, self._switch_task)

    # ---------- 显隐 / 退出 ----------
    def show_window(self):
        self.root.deiconify()
        self.root.lift()

    def hide_window(self):
        self.root.withdraw()

    def force_quit(self):
        self._do_quit()

    def _on_close(self):
        if config.get("minimize_to_tray", True):
            self.hide_window()
        else:
            self._do_quit()

    def _do_quit(self):
        self._save_position()
        self.tray.stop()
        if self.tracker.running:
            self.tracker.stop()
            try:
                report_path = self.tracker.generate_html_report()
                print(f"今日复盘已保存: {report_path}")
            except Exception:
                pass
        self.root.destroy()

    def _save_position(self):
        try:
            config["window_x"] = self.root.winfo_x()
            config["window_y"] = self.root.winfo_y()
            save_config(config)
        except Exception:
            pass

    # ---------- UI 构建 ----------
    def _build_ui(self):
        t = theme()
        # 整窗用单个 Canvas 绘制 — 这样扫描线、刻度、文字都能精确控制
        self.canvas = tk.Canvas(self.root, width=self.WIDTH, height=self.HEIGHT,
                                bg=t["bg"], highlightthickness=1,
                                highlightbackground=t["border"], bd=0)
        self.canvas.pack(fill='both', expand=True)

        # 扫描线只画一次（不随刷新重绘）
        self._draw_scanlines()

        # 菜单按钮 — 右上角
        self.menu_hot = tk.Label(self.root, text='...',
                                 bg=t["bg"], fg=t["ink_2"],
                                 font=('JetBrains Mono', 12, 'bold'),
                                 cursor='hand2')
        self.menu_hot.place(x=self.WIDTH - 30, y=6, width=24, height=18)
        self.menu_hot.bind('<Button-1>', self._toggle_menu)
        self.menu_hot.bind('<Enter>', lambda e: self.menu_hot.config(fg=theme()["accent"]))
        self.menu_hot.bind('<Leave>', lambda e: self.menu_hot.config(fg=theme()["ink_2"]))

        self._render_static()

    def _draw_scanlines(self):
        c = self.canvas
        t = theme()
        for y in range(0, self.HEIGHT, 3):
            c.create_line(0, y, self.WIDTH, y, fill=t["scanline"], width=1, tags='scanline')

    def _render_static(self):
        """重绘画布（除扫描线外）。"""
        c = self.canvas
        c.delete('dynamic')
        t = theme()
        c.configure(bg=t["bg"], highlightbackground=t["border"])
        self.menu_hot.configure(bg=t["bg"], fg=t["ink_2"])

        # 顶栏 logo
        c.create_text(18, 16, text="W / TRACE", anchor='w',
                      fill=t["accent"],
                      font=('Microsoft YaHei', 9, 'bold'),
                      tags='dynamic')

        # 任务名（中部偏上）
        task_name = "> 未开始"
        if self.tracker.current_task:
            task_name = f"> {self.tracker.current_task.name}"
        c.create_text(18, 50, text=task_name, anchor='w',
                      fill=t["ink_2"],
                      font=('Microsoft YaHei', 10),
                      tags='dynamic')

        # 30 格刻度进度
        self._render_ticks()

        # 时间数字
        time_str = self._compute_time_str()
        c.create_text(18, 118, text=time_str, anchor='w',
                      fill=t["data"],
                      font=('JetBrains Mono', 22, 'bold'),
                      tags=('dynamic', 'time'))

        # 状态徽章（右下）
        self._render_badge()

        # 元数据底部
        sessions = self._compute_session_count()
        focused_min = self._compute_focused_minutes()
        c.create_text(18, 158, text=f"第 {sessions:02d} 轮  ·  专注 {focused_min} 分钟",
                      anchor='w',
                      fill=t["ink_3"],
                      font=('Microsoft YaHei', 8),
                      tags='dynamic')

    def _render_ticks(self):
        """30 格刻度。完成格 = data 色；当前格 = pulse 色；未来格 = mute 色。"""
        c = self.canvas
        t = theme()
        completed, progress = self._compute_tick_state()

        x0 = 18
        y_base = 82
        gap = 2
        tick_w = 7
        tick_h_filled = 9
        tick_h_now = 14
        tick_h_empty = 6
        pulse = self._tick_pulse_phase
        cursor_fill = self._blend(t["pulse"], t["accent"], pulse)
        halo_color = self._blend(t["border_strong"], t["accent"], pulse)

        for i in range(30):
            x = x0 + i * (tick_w + gap)
            if i < completed:
                c.create_rectangle(x, y_base + (tick_h_now - tick_h_filled),
                                   x + tick_w, y_base + tick_h_now,
                                   fill=t["data"], outline='', tags='dynamic')
            elif i == completed:
                cx = x + tick_w / 2
                halo = 4 + int(pulse * 5)
                c.create_rectangle(x - halo, y_base - halo,
                                   x + tick_w + halo, y_base + tick_h_now + halo,
                                   fill='', outline=halo_color, width=1,
                                   tags='dynamic')
                c.create_rectangle(x - 3, y_base - 3,
                                   x + tick_w + 3, y_base + tick_h_now + 3,
                                   fill='', outline=t["accent"], width=2,
                                   tags='dynamic')
                c.create_rectangle(x, y_base - 1,
                                   x + tick_w, y_base + tick_h_now + 1,
                                   fill=cursor_fill, outline='', tags='dynamic')
                c.create_line(cx, y_base - 11, cx, y_base + tick_h_now + 11,
                              fill=halo_color, width=1 + int(pulse * 2), tags='dynamic')
                c.create_line(x - 5, y_base - 7, x + tick_w + 5, y_base - 7,
                              fill=t["accent"], width=1, tags='dynamic')
                c.create_line(x - 5, y_base + tick_h_now + 7,
                              x + tick_w + 5, y_base + tick_h_now + 7,
                              fill=t["accent"], width=1, tags='dynamic')
            else:
                c.create_rectangle(x, y_base + (tick_h_now - tick_h_empty),
                                   x + tick_w, y_base + tick_h_now,
                                   fill=t["mute"], outline='', tags='dynamic')

    def _render_badge(self):
        """ON / DRIFT / OFF / IDLE 徽章 — 右下"""
        c = self.canvas
        t = theme()
        state, drift_min = self._compute_badge_state()
        if state == 'on':
            badge_text = "专注中"
            color = t["accent"]
        elif state == 'drift':
            badge_text = f"偏离 {drift_min:02d}分"
            color = t["drift"]
        elif state == 'paused':
            badge_text = "暂停"
            color = t["ink_3"]
        else:
            badge_text = "空闲"
            color = t["ink_3"]

        # 测量文本长度
        font_obj = ('Microsoft YaHei', 8, 'bold')
        # 估算字符宽度（中文约 12px，英文约 6px）
        cn_chars = sum(1 for ch in badge_text if '一' <= ch <= '鿿')
        en_chars = len(badge_text) - cn_chars
        text_w = cn_chars * 12 + en_chars * 6
        pad_x = 6
        pad_y = 3
        x_right = self.WIDTH - 18
        y_center = 118

        x0 = x_right - text_w - pad_x * 2
        x1 = x_right
        y0 = y_center - 8
        y1 = y_center + 8

        # 边框
        c.create_rectangle(x0, y0, x1, y1, outline=color, width=1, fill='', tags='dynamic')
        c.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=badge_text,
                      fill=color, font=font_obj, tags='dynamic')
        self._badge_color = color

    # ---------- 拖拽 + 磁吸 ----------
    def _bind_drag(self):
        _bind_full_drag(self.root, self.root, self.canvas, on_snap_save=self._save_position)


    # ---------- 菜单 ----------
    def _toggle_menu(self, event=None):
        if self._menu_window is not None and self._menu_window.winfo_exists():
            self._close_menu()
            return
        self._show_menu()

    def _show_menu(self):
        t = theme()
        m = tk.Toplevel(self.root)
        m.overrideredirect(True)
        m.attributes('-topmost', True)
        m.configure(bg=t["bg_elev"])

        bd = tk.Frame(m, bg=t["bg_elev"], highlightthickness=1,
                      highlightbackground=t["border_strong"])
        bd.pack(fill='both', expand=True)

        cur_theme = config.get("theme", "dark")
        theme_label = "切换浅色" if cur_theme == "dark" else "切换深色"

        items = [
            ("切换任务",       self._switch_task),
            None,
            ("今日复盘",      self._generate_today_review),
            ("统计",             self._open_stats),
            ("任务列表",             self._open_task_manager),
            None,
            (theme_label,         self._toggle_theme),
            ("设置",          self._open_settings),
            None,
            ("退出",              self._do_quit),
        ]

        row_h = 32
        total_h = 18
        for it in items:
            if it is None:
                sep = tk.Frame(bd, bg=t["rule"], height=1)
                sep.pack(fill='x', padx=10, pady=3)
                total_h += 7
                continue
            label, cb = it
            lab = tk.Label(bd, text=label,
                           font=('Microsoft YaHei', 9, 'bold'),
                           bg=t["bg_elev"], fg=t["ink_2"],
                           anchor='w', padx=14, pady=8,
                           cursor='hand2')
            lab.pack(fill='x')

            def on_enter(e, l=lab):
                l.configure(bg=t["bg"], fg=t["accent"])

            def on_leave(e, l=lab):
                l.configure(bg=t["bg_elev"], fg=t["ink_2"])

            lab.bind('<Enter>', on_enter)
            lab.bind('<Leave>', on_leave)
            lab.bind('<Button-1>', lambda e, c=cb: self._menu_action(c))
            total_h += row_h

        self.root.update_idletasks()
        m.update_idletasks()
        rx = self.root.winfo_x() + self.WIDTH - 140
        ry = self.root.winfo_y() + 30
        total_h = bd.winfo_reqheight() + 12
        m.geometry(f'140x{total_h}+{rx}+{ry}')

        self._menu_window = m
        m.bind('<FocusOut>', lambda e: self._close_menu())
        m.focus_set()

    def _close_menu(self):
        try:
            if self._menu_window:
                self._menu_window.destroy()
        except Exception:
            pass
        self._menu_window = None

    def _menu_action(self, cb):
        self._close_menu()
        try:
            cb()
        except Exception as e:
            print(f"菜单动作错误: {e}")

    # ---------- 菜单回调 ----------
    def _toggle_theme(self):
        new_theme = "light" if config.get("theme", "dark") == "dark" else "dark"
        config["theme"] = new_theme
        save_config(config)
        # 刷新所有已打开的子窗口
        alive = []
        for name, w in self._child_windows:
            try:
                if w.win.winfo_exists():
                    t = theme()
                    w.win.configure(bg=t["bg_elev"])
                    # 重新创建 UI 内容
                    for child in w.win.winfo_children():
                        child.destroy()
                    if hasattr(w, '_create_ui'):
                        if name == 'stats':
                            w._create_ui()
                            w._load_data()
                        elif name == 'tasks':
                            w._create_ui()
                            w._refresh_list()
                        else:
                            w._create_ui()
                    alive.append((name, w))
            except Exception:
                pass
        self._child_windows = alive
        # 重绘扫描线 + 动态内容
        self.canvas.delete('all')
        self._draw_scanlines()
        self._render_static()

    def _switch_task(self):
        window_info = WindowTracker.get_active_window_info()
        self.tracker._ask_for_task(window_info)

    def _stop_current(self):
        if self.tracker.running:
            self.tracker.stop()

    def _open_settings(self):
        w = SettingsWindow(self.root, config, on_save=self._on_settings_saved)
        self._child_windows.append(('settings', w))

    def _on_settings_saved(self, new_cfg):
        global config
        config.update(new_cfg)
        self.tracker.check_interval = config["check_interval"]
        self.tracker.idle_threshold = config["idle_threshold"]
        self.tracker.reminder_interval = config["reminder_interval"]
        self.tracker.lock_check_interval = config["lock_check_interval"]
        self.tracker.no_input_threshold = config.get("no_input_threshold", 180)
        self.root.attributes('-topmost', config.get("always_on_top", True))
        self._render_static()

    def _open_stats(self):
        w = StatsWindow(self.root, self.tracker.db)
        self._child_windows.append(('stats', w))

    def _open_task_manager(self):
        w = TaskManagerWindow(self.root, self.tracker.db, self.tracker)
        self._child_windows.append(('tasks', w))

    def _generate_today_review(self):
        path = self.tracker.generate_html_report()
        webbrowser.open(f'file:///{path}')

    # ---------- 状态计算 ----------
    def _compute_tick_state(self):
        """返回 (completed_in_session, progress_in_current_session)"""
        unit_sec = max(1, config.get("pomodoro_minutes", 30)) * 60
        if self.tracker.running and self.tracker.current_task:
            cur_seconds = self.tracker.get_current_task_seconds()
            completed = (cur_seconds // unit_sec) % 30  # 单页 30 格
            in_unit = cur_seconds - (cur_seconds // unit_sec) * unit_sec
            # 30 格 = 30 分钟（每格 1 分钟），所以已完成格数 = in_unit // 60
            completed_ticks = min(in_unit // 60, 29)
            return completed_ticks, in_unit / unit_sec
        return 0, 0.0

    def _compute_time_str(self):
        unit_sec = max(1, config.get("pomodoro_minutes", 30)) * 60
        if self.tracker.running and self.tracker.current_task:
            cur_seconds = self.tracker.get_current_task_seconds()
            in_unit = cur_seconds - (cur_seconds // unit_sec) * unit_sec
            mm = in_unit // 60
            ss = in_unit % 60
            sep = ':' if self._colon_visible else ' '
            return f"{mm:02d}{sep}{ss:02d}"
        return "00 00"

    def _compute_badge_state(self):
        if not self.tracker.running:
            if self.tracker.current_task:
                return 'paused', 0
            return 'idle', 0
        if not self.tracker.current_task:
            return 'idle', 0
        if self.tracker.is_deviating:
            drift_sec = int(time.time() - self.tracker.deviation_start_time) \
                if self.tracker.deviation_start_time else 0
            return 'drift', max(1, drift_sec // 60)
        return 'on', 0

    def _compute_session_count(self):
        return self.tracker.get_today_completed_pomodoros() + 1

    def _compute_focused_minutes(self):
        try:
            activities = self.tracker.db.get_today_activities()
            sec = 0
            for act in activities:
                if not act.is_idle and not act.is_locked \
                        and not self.tracker._is_non_work_task(act.task_name):
                    sec += act.duration
            if self.tracker.current_activity \
                    and not self.tracker.current_activity.is_idle \
                    and not self.tracker.current_activity.is_locked \
                    and not self.tracker._is_non_work_task(self.tracker.current_activity.task_name):
                from datetime import datetime as _dt
                start = _dt.fromisoformat(self.tracker.current_activity.start_time)
                sec += int((_dt.now() - start).total_seconds())
            return sec // 60
        except Exception:
            return 0

    # ---------- 循环 ----------
    def _update_loop(self):
        try:
            self._render_static()
            # 完成新番茄反馈
            unit_sec = max(1, config.get("pomodoro_minutes", 30)) * 60
            if self.tracker.running and self.tracker.current_task:
                cur_seconds = self.tracker.get_current_task_seconds()
                completed = cur_seconds // unit_sec
                if completed > self._last_completed and config.get("pomodoro_sound", True):
                    self._play_chime()
                self._last_completed = completed
            self.tray.update_tooltip(
                f"W/TRACE · {self.tracker.current_task.name if self.tracker.current_task else 'idle'}"
            )
        except Exception as e:
            print(f"刷新错误: {e}")
        self.root.after(1000, self._update_loop)

    def _colon_blink_loop(self):
        self._colon_visible = not self._colon_visible
        # 不重绘整画布，只更新时间文字
        try:
            t = theme()
            self.canvas.itemconfigure('time', text=self._compute_time_str())
        except Exception:
            pass
        self.root.after(1000, self._colon_blink_loop)

    def _pulse_loop(self):
        """当前进度光标呼吸。"""
        try:
            self._tick_pulse_phase += 0.09 * self._tick_pulse_dir
            if self._tick_pulse_phase >= 1.0:
                self._tick_pulse_phase = 1.0
                self._tick_pulse_dir = -1
            elif self._tick_pulse_phase <= 0.0:
                self._tick_pulse_phase = 0.0
                self._tick_pulse_dir = 1
            self._render_static()
        except Exception:
            pass
        self.root.after(80, self._pulse_loop)

    def _blend(self, hex1, hex2, t_):
        def to_rgb(h):
            h = h.lstrip('#')
            return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        r1, g1, b1 = to_rgb(hex1)
        r2, g2, b2 = to_rgb(hex2)
        r = int(r1 * (1 - t_) + r2 * t_)
        g = int(g1 * (1 - t_) + g2 * t_)
        b = int(b1 * (1 - t_) + b2 * t_)
        return f"#{r:02X}{g:02X}{b:02X}"

    def _play_chime(self):
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_OK)
        except Exception:
            pass

    def run(self):
        self.root.mainloop()

class TimeTracker:
    """时间追踪主类"""
    
    def __init__(self):
        self.db = Database(DB_PATH)
        self.running = False
        self.current_task: Optional[Task] = None
        self.current_activity: Optional[Activity] = None
        self.last_window = None
        self.today_tasks: List[Task] = []
        self.panel = None  # ControlPanel 引用，由 main() 设置
        self._dialog_active = False

        # 从配置加载参数
        self.check_interval = config["check_interval"]
        self.idle_threshold = config["idle_threshold"]
        self.reminder_interval = config["reminder_interval"]
        self.lock_check_interval = config["lock_check_interval"]
        self.no_input_threshold = config.get("no_input_threshold", 180)

        # 线程
        self.track_thread = None
        self.lock_check_thread = None
        self.last_reminder_time = 0
        self.deviation_start_time = 0  # 任务偏离开始时间

        # 番茄钟与休息统计
        self.current_task_history_seconds = 0  # 当前任务在今日的历史累计专注秒数
        self.today_no_input_seconds = 0        # 今日实际无操作秒数（连续3分钟以上无输入）
        self.is_deviating = False              # 当前是否处于偏离状态（焦点窗口与任务不一致）
    
    def start(self):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 时间追踪已启动")
        self.running = True
        
        self._load_today_tasks()
        
        self.track_thread = threading.Thread(target=self._track_loop)
        self.track_thread.daemon = True
        self.track_thread.start()
        
        self.lock_check_thread = threading.Thread(target=self._lock_check_loop)
        self.lock_check_thread.daemon = True
        self.lock_check_thread.start()
    
    def stop(self):
        self.running = False
        if self.current_activity:
            self._end_activity()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 时间追踪已暂停")

    def stop_today(self):
        self.running = False
        if self.current_activity:
            self._end_activity()
        self.current_task = None
        self.deviation_start_time = 0
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 今日工作轨迹记录已停止")

    def _load_today_tasks(self):
        self.today_tasks = self.db.get_tasks()
        if not self.today_tasks:
            self._show_task_planning()

        # 加载任务后，如果没有当前任务，立即弹出选择
        if self.today_tasks and not self.current_task and self.panel:
            # 延迟一点让 UI 完全就绪
            self.panel.root.after(500, self._prompt_select_task)

    def _prompt_select_task(self):
        """弹出任务选择对话框，让用户选择当前要做的任务。"""
        if self._dialog_active or self.current_task:
            return
        if not self.panel:
            return
        window_info = WindowTracker.get_active_window_info()
        options = [task.name for task in self.today_tasks]
        task_ids = [task.id for task in self.today_tasks]
        msg = "请选择你现在要做的任务："
        dialog = ModernDialog(self.panel.root, "选择任务", msg, options,
                             task_ids=task_ids,
                             on_task_edit=self._on_task_edit,
                             on_task_delete=self._on_task_delete_confirm)
        result = dialog.show()
        self._process_task_selection(result, window_info)

    def _on_task_edit(self, task_id: str, new_name: str):
        """弹窗内修改任务名称的回调"""
        self.db.rename_task(task_id, new_name)
        for t in self.today_tasks:
            if t.id == task_id:
                print(f"[任务已重命名] {t.name} → {new_name}")
                t.name = new_name
                break
    
    def _on_task_delete_confirm(self, task_id: str, task_name: str) -> bool:
        """弹窗内删除任务 — 直接执行删除，不二次确认（弹窗内已有确认）"""
        self.db.delete_task(task_id)
        self.today_tasks = [t for t in self.today_tasks if t.id != task_id]
        if self.current_task and self.current_task.id == task_id:
            self.current_task = None
            if self.current_activity and self.current_activity.task_id == task_id:
                self._end_activity()
        return True
    
    def _show_task_planning(self):
        """在主线程弹出任务规划对话框（线程安全）。
        规划完成后：单任务自动开始，多任务弹出选择。"""
        if not self.panel:
            return

        def _do():
            try:
                while True:
                    existing = [task.name for task in self.today_tasks]
                    task_ids = [task.id for task in self.today_tasks]
                    dialog = ModernDialog(
                        self.panel.root,
                        "今日任务规划",
                        "早上好！请规划今天的任务：\n在下方输入任务名称，按回车或点击添加。\n可以多次添加，添加完关闭即可。",
                        existing,
                        task_ids=task_ids,
                        on_task_edit=self._on_task_edit,
                        on_task_delete=self._on_task_delete_confirm
                    )
                    result = dialog.show()

                    if result and result.startswith("DELETE:"):
                        deleted_id = result[7:]
                        self.db.delete_task(deleted_id)
                        self.today_tasks = [t for t in self.today_tasks if t.id != deleted_id]
                        continue

                    if not result:
                        break

                    if result.startswith("NEW:"):
                        task_name = result[4:].strip()
                        if task_name:
                            task = Task(
                                id=f"task_{int(time.time())}",
                                name=task_name,
                                created_at=datetime.now().isoformat()
                            )
                            self.db.save_task(task)
                            self.today_tasks.append(task)
                            print(f"[新任务已保存] {task_name}")
                    elif result in existing:
                        pass
                    else:
                        break

                # 规划完成后：单任务自动开始，多任务弹出选择
                if len(self.today_tasks) == 1 and not self.current_task:
                    self.current_task = self.today_tasks[0]
                    window_info = WindowTracker.get_active_window_info()
                    self._start_activity(self.current_task, window_info)
                    print(f"[自动开始] {self.current_task.name}")
                elif len(self.today_tasks) > 1 and not self.current_task:
                    self.panel.root.after(200, self._prompt_select_task)

            except Exception as e:
                print(f"[任务规划] 错误: {e}")

        # 判断是否在主线程
        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            event = threading.Event()
            def _wrapper():
                try:
                    _do()
                finally:
                    event.set()
            self.panel.root.after(0, _wrapper)
            event.wait()
    
    def _track_loop(self):
        while self.running:
            try:
                if WindowTracker.is_screen_locked():
                    if self.current_activity and not self.current_activity.is_locked:
                        self._end_activity()
                        self._start_activity(Task(id='locked', name='🔒 锁屏/离开'), is_locked=True)
                    time.sleep(self.check_interval)
                    continue
                
                window_info = WindowTracker.get_active_window_info()
                
                idle_time = WindowTracker.get_idle_time()
                if idle_time >= self.idle_threshold:
                    if self.current_activity and not self.current_activity.is_idle:
                        self._end_activity()
                        self._start_activity(Task(id='idle', name='离开座位'), is_idle=True)
                    # 无操作累计：超过设定阈值后，每个 check_interval 累入今日无操作
                    if idle_time >= self.no_input_threshold:
                        self.today_no_input_seconds += self.check_interval
                    time.sleep(self.check_interval)
                    continue
                # 非 idle 区间，但 idle_time 仍可能 ≥ no_input_threshold（短暂离开但未超 idle 阈值）
                if idle_time >= self.no_input_threshold:
                    self.today_no_input_seconds += self.check_interval

                if self._is_window_changed(window_info):
                    self._handle_window_change(window_info)

                # 任务偏离提醒：有当前任务但焦点窗口与任务不一致
                if self.current_task and self.current_activity and self.running:
                    if not self._is_same_task_context(window_info):
                        # 焦点窗口与当前任务不一致
                        self.is_deviating = True
                        if self.deviation_start_time == 0:
                            self.deviation_start_time = time.time()
                        elif time.time() - self.deviation_start_time >= self.reminder_interval:
                            # 超过偏离阈值，弹窗确认
                            self.deviation_start_time = 0
                            self.last_reminder_time = time.time()
                            self._ask_task_confirmation(window_info)
                    else:
                        # 焦点窗口恢复一致，重置偏离计时
                        self.deviation_start_time = 0
                        self.is_deviating = False
                else:
                    self.is_deviating = False
                
                # 未识别提醒：无当前任务时定期提醒
                if not self.current_task and self.running:
                    now = time.time()
                    if now - self.last_reminder_time >= self.reminder_interval:
                        self.last_reminder_time = now
                        self._ask_for_task(window_info)
                
                if self.current_activity:
                    self.current_activity.duration += self.check_interval
                
                time.sleep(self.check_interval)
                
            except Exception as e:
                print(f"追踪出错: {e}")
                time.sleep(self.check_interval)
    
    def _lock_check_loop(self):
        while self.running:
            try:
                if WindowTracker.is_screen_locked():
                    if self.current_activity and not self.current_activity.is_locked:
                        self._end_activity()
                        self._start_activity(Task(id='locked', name='🔒 锁屏/离开'), is_locked=True)
                time.sleep(self.lock_check_interval)
            except:
                time.sleep(self.lock_check_interval)
    
    def _is_window_changed(self, window_info: Dict[str, str]) -> bool:
        if not self.last_window:
            self.last_window = window_info
            return True
        
        changed = (
            window_info['app'] != self.last_window['app'] or
            window_info['title'] != self.last_window['title']
        )
        
        if changed:
            self.last_window = window_info
        return changed
    
    def _handle_window_change(self, window_info: Dict[str, str]):
        if not self.current_task:
            self._ask_for_task(window_info)
            return
        
        if self._is_same_task_context(window_info):
            if self.current_activity:
                self.current_activity.app_name = window_info['app']
                self.current_activity.window_title = window_info['title']
                self.current_activity.url = window_info['url']
            return
        
        self._ask_task_confirmation(window_info)
    
    def _is_same_task_context(self, window_info: Dict[str, str]) -> bool:
        """判断当前焦点窗口是否与进行中的任务属于同一上下文。
        严格匹配：只有同一个应用进程才视为同一上下文。
        不同浏览器（Chrome vs Edge）不算同一上下文，避免误判。"""
        if not self.current_activity:
            return False
        
        # 同一应用进程才算同一上下文
        if window_info['app'] == self.current_activity.app_name:
            return True
        
        return False
    
    def _ask_for_task(self, window_info: Dict[str, str]):
        if self._dialog_active:
            return
        if not self.panel:
            return
        self._dialog_active = True

        def _do():
            try:
                options = [task.name for task in self.today_tasks]
                task_ids = [task.id for task in self.today_tasks]
                msg = f"检测到你在使用：{window_info.get('app', '')} - {window_info.get('title', '')}\n\n请选择当前任务，或在下方输入新建："

                dialog = ModernDialog(self.panel.root, "任务确认", msg, options,
                                     task_ids=task_ids,
                                     on_task_edit=self._on_task_edit,
                                     on_task_delete=self._on_task_delete_confirm)
                result = dialog.show()

                self._process_task_selection(result, window_info)
            finally:
                self._dialog_active = False

        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            event = threading.Event()
            def _wrapper():
                try:
                    _do()
                finally:
                    event.set()
            self.panel.root.after(0, _wrapper)
            event.wait()
    
    def _ask_task_confirmation(self, window_info: Dict[str, str]):
        if self._dialog_active:
            return
        if not self.panel:
            return
        self._dialog_active = True

        def _do():
            try:
                options = [
                    f"✅ 仍在做：{self.current_task.name}",
                    "🔄 切换到其他任务",
                    "休息/娱乐"
                ]

                dialog = ModernDialog(
                    self.panel.root,
                    "任务确认",
                    f"你切换到了：\n{window_info['app']} - {window_info['title']}\n\n还在做'{self.current_task.name}'吗？",
                    options
                )
                result = dialog.show()

                if result and result.startswith("✅"):
                    self._end_activity()
                    self._start_activity(self.current_task, window_info)
                elif result and result.startswith("🔄"):
                    self._end_activity()
                    self._dialog_active = False
                    self._ask_for_task(window_info)
                    return
                elif result == "休息/娱乐" or result == "REST":
                    self._end_activity()
                    self.current_task = None
            finally:
                self._dialog_active = False

        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            event = threading.Event()
            def _wrapper():
                try:
                    _do()
                finally:
                    event.set()
            self.panel.root.after(0, _wrapper)
            event.wait()
    
    def _process_task_selection(self, result: str, window_info: Dict[str, str]):
        if not result:
            return

        if result == "REST":
            self.current_task = Task(id='rest', name='休息/娱乐')
            self._start_activity(self.current_task, window_info)
        elif result.startswith("NEW:"):
            task_name = result[4:].strip()
            if not task_name:
                return
            task = Task(
                id=f"task_{int(time.time())}",
                name=task_name,
                created_at=datetime.now().isoformat()
            )
            self.db.save_task(task)
            self.today_tasks.append(task)
            # 新建任务后直接开始
            if self.running:
                self.current_task = task
                self._start_activity(task, window_info)
            print(f"[新任务已保存] {task_name}")
        else:
            for task in self.today_tasks:
                if task.name == result:
                    self.current_task = task
                    self._start_activity(task, window_info)
                    break
    
    def _start_activity(self, task: Task, window_info: Dict[str, str] = None, is_idle: bool = False, is_locked: bool = False):
        now = datetime.now()
        self.current_activity = Activity(
            task_id=task.id, task_name=task.name,
            app_name=window_info['app'] if window_info else '',
            window_title=window_info['title'] if window_info else '',
            url=window_info['url'] if window_info else '',
            start_time=now.isoformat(),
            is_idle=is_idle, is_locked=is_locked
        )
        print(f"[{now.strftime('%H:%M:%S')}] 开始任务: {task.name}")
    
    def _end_activity(self):
        if not self.current_activity:
            return
        
        now = datetime.now()
        self.current_activity.end_time = now.isoformat()
        start = datetime.fromisoformat(self.current_activity.start_time)
        self.current_activity.duration = int((now - start).total_seconds())
        self.db.save_activity(self.current_activity)
        print(f"[{now.strftime('%H:%M:%S')}] 结束任务: {self.current_activity.task_name} ({self.current_activity.duration}秒)")
        self.current_activity = None
    
    def generate_report(self) -> str:
        activities = self.db.get_today_activities()
        if not activities:
            return "今日暂无记录"

        task_summary = self._summarize_activities(activities)

        report = []
        report.append("=" * 50)
        report.append(f"[WorkTrace 今日复盘] - {datetime.now().strftime('%Y年%m月%d日')}")
        report.append("=" * 50)
        report.append("")
        report.append("[任务统计]：")
        for task_name, duration in sorted(task_summary.items(), key=lambda x: x[1], reverse=True):
            report.append(f"  {task_name}: {self._fmt_duration(duration)}")

        report.append("")
        report.append("[时间线]：")
        for act in activities:
            start = datetime.fromisoformat(act.start_time)
            end = datetime.fromisoformat(act.end_time) if act.end_time else datetime.now()
            minutes = int((end - start).total_seconds() / 60)
            report.append(f"  {start.strftime('%H:%M')}-{end.strftime('%H:%M')} [{act.task_name}] {minutes}分钟")

        report.append("")
        report.append("=" * 50)
        return "\n".join(report)

    def generate_html_report(self) -> str:
        activities = self.db.get_today_activities()
        now = datetime.now()
        report_path = os.path.join(SCRIPT_DIR, f"worktrace_review_{now.strftime('%Y%m%d')}.html")

        task_summary = self._summarize_activities(activities)
        total_sec = sum(task_summary.values())
        focus_sec = sum(v for k, v in task_summary.items() if not self._is_non_work_task(k))
        away_sec = total_sec - focus_sec
        top_task = max(task_summary.items(), key=lambda x: x[1])[0] if task_summary else "暂无"
        efficiency = int(focus_sec / total_sec * 100) if total_sec else 0

        task_rows = []
        for name, duration in sorted(task_summary.items(), key=lambda x: x[1], reverse=True):
            percent = int(duration / total_sec * 100) if total_sec else 0
            task_rows.append(f"""
            <div class=\"task-row\">
                <div class=\"task-main\">
                    <span>{html.escape(name)}</span>
                    <strong>{self._fmt_duration(duration)}</strong>
                </div>
                <div class=\"bar\"><div style=\"width:{percent}%\"></div></div>
            </div>
            """)

        timeline_rows = []
        for act in activities:
            start = datetime.fromisoformat(act.start_time)
            end = datetime.fromisoformat(act.end_time) if act.end_time else now
            title = html.escape(act.window_title or act.app_name or "")
            timeline_rows.append(f"""
            <div class=\"timeline-item\">
                <div class=\"time\">{start.strftime('%H:%M')} - {end.strftime('%H:%M')}</div>
                <div class=\"content\">
                    <strong>{html.escape(act.task_name)}</strong>
                    <span>{self._fmt_duration(act.duration)} · {html.escape(act.app_name or '')}</span>
                    <p>{title}</p>
                </div>
            </div>
            """)

        if not activities:
            diagnosis = "今天还没有记录。先开始一次工作轨迹记录，再生成复盘。"
            suggestion = "建议先建立今日任务，再开始追踪。"
        elif away_sec > focus_sec:
            diagnosis = "今天离开、休息或未归类时间偏多，需要检查是否有漏记任务。"
            suggestion = "明天可以先列出 3 个重点任务，减少未归类时间。"
        elif len(task_summary) >= 6:
            diagnosis = "今天任务切换较多，可能存在碎片化工作。"
            suggestion = "明天可以把相近任务合并，优先保证大块时间。"
        else:
            diagnosis = f"今天主要投入在“{top_task}”，整体记录比较集中。"
            suggestion = "明天可以继续保持，结束工作后及时生成复盘。"

        html_doc = f"""<!doctype html>
<html lang=\"zh-CN\">
<head>
<meta charset=\"utf-8\">
<title>WorkTrace 今日复盘</title>
<style>
body {{ margin:0; background:#f4f6fb; color:#111827; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif; }}
.container {{ max-width:1080px; margin:0 auto; padding:32px; }}
.hero {{ background:linear-gradient(135deg,#111827,#374151); color:white; border-radius:28px; padding:32px; box-shadow:0 24px 60px rgba(17,24,39,.22); }}
.hero p {{ color:#d1d5db; margin:8px 0 0; }}
.grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin:22px 0; }}
.card {{ background:white; border-radius:22px; padding:22px; box-shadow:0 14px 36px rgba(15,23,42,.08); }}
.card .label {{ color:#6b7280; font-size:13px; }}
.card .value {{ font-size:28px; font-weight:800; margin-top:8px; }}
.section {{ background:white; border-radius:24px; padding:24px; margin-top:18px; box-shadow:0 14px 36px rgba(15,23,42,.08); }}
.section h2 {{ margin:0 0 16px; font-size:20px; }}
.task-row {{ margin:14px 0; }}
.task-main {{ display:flex; justify-content:space-between; gap:16px; }}
.bar {{ height:10px; background:#e5e7eb; border-radius:99px; overflow:hidden; margin-top:8px; }}
.bar div {{ height:100%; background:linear-gradient(90deg,#2563eb,#7c3aed); border-radius:99px; }}
.diagnosis {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
.note {{ background:#f9fafb; border:1px solid #e5e7eb; border-radius:18px; padding:18px; line-height:1.7; }}
.timeline-item {{ display:grid; grid-template-columns:130px 1fr; gap:16px; padding:16px 0; border-bottom:1px solid #eef2f7; }}
.timeline-item:last-child {{ border-bottom:0; }}
.time {{ color:#6b7280; font-weight:700; }}
.content strong {{ display:block; font-size:16px; }}
.content span {{ display:block; color:#6b7280; margin-top:4px; }}
.content p {{ color:#9ca3af; margin:6px 0 0; }}
@media (max-width:800px) {{ .grid,.diagnosis {{ grid-template-columns:1fr; }} .timeline-item {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<div class=\"container\">
  <div class=\"hero\">
    <h1>WorkTrace 今日复盘</h1>
    <p>{now.strftime('%Y年%m月%d日 %H:%M')} 生成 · 本地报告</p>
  </div>

  <div class=\"grid\">
    <div class=\"card\"><div class=\"label\">总记录时长</div><div class=\"value\">{self._fmt_duration(total_sec)}</div></div>
    <div class=\"card\"><div class=\"label\">有效工作时间</div><div class=\"value\">{self._fmt_duration(focus_sec)}</div></div>
    <div class=\"card\"><div class=\"label\">离开/休息时间</div><div class=\"value\">{self._fmt_duration(away_sec)}</div></div>
    <div class=\"card\"><div class=\"label\">有效占比</div><div class=\"value\">{efficiency}%</div></div>
  </div>

  <div class=\"section\">
    <h2>汇总与诊断</h2>
    <div class=\"diagnosis\">
      <div class=\"note\"><strong>一句话总结</strong><br>今天主要投入在“{html.escape(top_task)}”，有效工作时间 {self._fmt_duration(focus_sec)}。</div>
      <div class=\"note\"><strong>AI 诊断占位</strong><br>{html.escape(diagnosis)}<br><br><strong>明日建议：</strong>{html.escape(suggestion)}</div>
    </div>
  </div>

  <div class=\"section\">
    <h2>任务时间排行</h2>
    {''.join(task_rows) if task_rows else '<p>暂无任务记录</p>'}
  </div>

  <div class=\"section\">
    <h2>时间线</h2>
    {''.join(timeline_rows) if timeline_rows else '<p>暂无时间线记录</p>'}
  </div>
</div>
</body>
</html>"""

        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(html_doc)
        return report_path

    def _summarize_activities(self, activities: List[Activity]) -> Dict[str, int]:
        task_summary = {}
        for act in activities:
            task_summary[act.task_name] = task_summary.get(act.task_name, 0) + act.duration
        return task_summary

    def _is_non_work_task(self, task_name: str) -> bool:
        return any(keyword in task_name for keyword in ['休息', '离开', '锁屏'])

    def _fmt_duration(self, seconds: int) -> str:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours:
            return f"{hours}小时{minutes}分钟"
        return f"{minutes}分钟"

    # ========== 番茄钟与休息统计 ==========
    def get_current_task_seconds(self) -> int:
        """当前任务今日累计专注秒数（含历史活动 + 进行中活动）"""
        if not self.current_task:
            return 0
        total = 0
        try:
            activities = self.db.get_today_activities()
            for act in activities:
                if act.task_id == self.current_task.id and not act.is_idle and not act.is_locked:
                    total += act.duration
        except Exception:
            pass
        if self.current_activity and not self.current_activity.is_idle and not self.current_activity.is_locked:
            try:
                start = datetime.fromisoformat(self.current_activity.start_time)
                total += int((datetime.now() - start).total_seconds())
            except Exception:
                pass
        return total

    def get_today_completed_pomodoros(self) -> int:
        """今日所有任务累计完成的番茄数"""
        unit = max(1, config.get("pomodoro_minutes", 30)) * 60
        focus_total = 0
        try:
            activities = self.db.get_today_activities()
            for act in activities:
                if not act.is_idle and not act.is_locked and not self._is_non_work_task(act.task_name):
                    focus_total += act.duration
        except Exception:
            pass
        if self.current_activity and not self.current_activity.is_idle and not self.current_activity.is_locked \
                and not self._is_non_work_task(self.current_activity.task_name):
            try:
                start = datetime.fromisoformat(self.current_activity.start_time)
                focus_total += int((datetime.now() - start).total_seconds())
            except Exception:
                pass
        return focus_total // unit

    def get_no_input_seconds(self) -> int:
        return self.today_no_input_seconds


def main():
    print("=" * 50)
    print("[WorkTrace 工作轨迹] - 本地记录与复盘")
    print("=" * 50)
    
    tracker = TimeTracker()

    # 静默启动模式
    silent = config.get("silent_start", False)

    # 启动控制面板（主线程）
    panel = ControlPanel(tracker, silent=silent)

    # 给 tracker 设置 panel 引用，用于线程安全的 UI 调度
    tracker.panel = panel

    # 自动开始追踪（在 panel 创建后，确保 UI 可用）
    if config.get("auto_start_track", True):
        tracker.start()

    panel.run()


if __name__ == '__main__':
    main()
