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
import math
import json
import sqlite3
import threading
import ctypes
import struct
import html
import re
import urllib.request
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
    "edge_hide": False,             # 吸边隐藏：拖到屏幕左右边缘自动隐藏成露出条，移到露出条唤出
    "theme": "dark",                # 主题：dark / light
    # AI 内容感知偏离判定
    "ai_enabled": True,             # 是否启用 AI 内容判定（关闭则退回进程名兜底）
    "body_send": True,              # 是否把正文摘要外发给 AI（关闭则只发标题+网址）
    "ark_api_key": "",              # 火山方舟 API Key —— 留空，真实 key 写入本地 config.json（已 gitignore）
    "ark_endpoint": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
    "ark_model": "ep-20260604101325-f2wcq"
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
    """获取 Memphis 色板，并兼容旧视觉键名。"""
    return {
        "red": "#FF6B6B",
        "teal": "#4ECDC4",
        "yellow": "#FFE66D",
        "black": "#000000",
        "white": "#FFFFFF",
        "cream": "#FFF8DC",
        "muted": "#666666",
        "bg": "#FFFFFF",
        "bg_elev": "#FFFFFF",
        "border": "#000000",
        "border_strong": "#000000",
        "scanline": "#FFF8DC",
        "ink_1": "#000000",
        "ink_2": "#000000",
        "ink_3": "#666666",
        "accent": "#4ECDC4",
        "pulse": "#FFE66D",
        "data": "#FF6B6B",
        "drift": "#FF6B6B",
        "mute": "#FFF8DC",
        "rule": "#000000"
    }



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

def _apply_auto_launch(enabled):
    """设置/取消开机自启（仅在 auto_launch 改变时调用，避免每次保存都写注册表）。"""
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "TimeTracker"
        script_path = os.path.join(SCRIPT_DIR, "time_tracker_v2.py")
        pythonw_path = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.exists(pythonw_path):
            pythonw_path = sys.executable
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        if enabled:
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

# UI Automation：用于读取浏览器地址栏 + 文档正文（内容感知偏离判定）
# 延迟到追踪线程内首次使用时再 import，避免主线程/无 COM 环境下报错。
HAS_UIA = None  # None=未探测, True/False=探测结果


def _win_set_topmost(win, on=True):
    """对 Tk 窗口设置真正的 Windows 置顶层级（HWND_TOPMOST）。
    overrideredirect 无边框窗口的 -topmost 属性不可靠，需要直接调用
    SetWindowPos 才能稳定地置顶/取消置顶，且不抢焦点。
    关键点：HWND_TOPMOST(-1) 必须以指针宽度传递，用 c_void_p 包装，
    否则 64 位下被截断成 32 位，Windows 不认这个特殊句柄，置顶不生效。"""
    try:
        win.attributes('-topmost', bool(on))
    except Exception:
        pass
    if sys.platform != 'win32':
        return
    try:
        win.update_idletasks()
        user32 = ctypes.windll.user32
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.SetWindowPos.restype = ctypes.c_bool
        # Tk 把真正的顶层 OS 窗口放在 winfo_id() 的父级
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOACTIVATE = 0x0010
        insert = ctypes.c_void_p(HWND_TOPMOST if on else HWND_NOTOPMOST)
        user32.SetWindowPos(ctypes.c_void_p(hwnd), insert, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:
        pass


def _virtual_screen():
    """返回整个虚拟桌面（跨所有显示器）的 (x, y, w, h)。
    Tk 的 winfo_screenwidth/height 只给主屏尺寸，导致多屏下窗口被
    误判越界并被拉回主屏。失败返回 None。"""
    if sys.platform != 'win32':
        return None
    try:
        u = ctypes.windll.user32
        vx = u.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
        vy = u.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
        vw = u.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        vh = u.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
        if vw > 0 and vh > 0:
            return (vx, vy, vw, vh)
    except Exception:
        pass
    return None


def _monitor_rect(win):
    """返回 win 当前所在显示器的工作区 (x, y, w, h)。
    用于吸边隐藏跟随窗口当前所在屏幕，而不是固定回主屏。失败返回 None。"""
    if sys.platform != 'win32':
        return None
    try:
        win.update_idletasks()
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        MONITOR_DEFAULTTONEAREST = 2
        hmon = user32.MonitorFromWindow(ctypes.c_void_p(hwnd),
                                        MONITOR_DEFAULTTONEAREST)

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        pass
    return None


def _place_centered(win, parent, w, h):
    """把 win 居中到 parent 所在屏幕（多屏友好），并 clamp 进虚拟桌面可见区，
    保证子窗口跟随主窗口出现在用户当前那块屏幕上，而不是固定回主屏。"""
    x = y = None
    try:
        parent.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        if pw > 1 and ph > 1:
            x = px + (pw - w) // 2
            y = py + (ph - h) // 2
    except Exception:
        pass
    if x is None:
        x = (win.winfo_screenwidth() - w) // 2
        y = (win.winfo_screenheight() - h) // 2
    vs = _virtual_screen()
    if vs:
        vx, vy, vw, vh = vs
        x = max(vx, min(x, vx + vw - w))
        y = max(vy, min(y, vy + vh - h))
    win.geometry(f'{w}x{h}+{x}+{y}')


def _place_overlay(win, parent, w, h):
    """把无边框 overrideredirect 弹窗可靠地居中到 parent 当前屏幕位置。
    解决两个老问题：
      1) overrideredirect 窗口首次映射常忽略坐标落到 (0,0) —— 先 withdraw，
         定位后 deiconify 并再设一次 geometry，才能稳定落在目标位置。
      2) 用 parent.winfo_rootx/rooty（绝对屏幕坐标）定位，多屏/无边框下比
         winfo_x 可靠，并 clamp 进虚拟桌面可见区。
    适用于 rename/delete 这类相对父窗居中的小弹窗。"""
    try:
        win.withdraw()
    except Exception:
        pass
    x = y = None
    try:
        parent.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        if pw > 1 and ph > 1:
            x = px + (pw - w) // 2
            y = py + (ph - h) // 2
    except Exception:
        pass
    if x is None:
        x = (win.winfo_screenwidth() - w) // 2
        y = (win.winfo_screenheight() - h) // 2
    vs = _virtual_screen()
    if vs:
        vx, vy, vw, vh = vs
        x = max(vx, min(x, vx + vw - w))
        y = max(vy, min(y, vy + vh - h))
    geo = f'+{x}+{y}'
    try:
        win.geometry(geo)
        win.deiconify()
        win.update_idletasks()
        win.geometry(geo)
    except Exception:
        try:
            win.deiconify()
        except Exception:
            pass


def _asset(name):
    """脚本同级 icons/ 下资源的绝对路径。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icons', name)


@dataclass
class Task:
    """任务定义"""
    id: str
    name: str
    color: str = "#4A90E2"
    created_at: str = ""
    keywords: str = ""   # 逗号分隔的关键词画像（自动学习累积）
    
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

        # 任务关键词加权表（自动学习）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS task_keywords (
                task_id TEXT,
                term TEXT,
                weight REAL DEFAULT 0,
                PRIMARY KEY (task_id, term)
            )
        ''')

        # 迁移：为老库的 tasks 表补 keywords 列
        cols = [r[1] for r in cursor.execute("PRAGMA table_info(tasks)").fetchall()]
        if 'keywords' not in cols:
            cursor.execute("ALTER TABLE tasks ADD COLUMN keywords TEXT DEFAULT ''")

        conn.commit()
        conn.close()
    
    def save_task(self, task: Task):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO tasks (id, name, color, created_at, is_active, keywords)
            VALUES (?, ?, ?, ?, 1, ?)
        ''', (task.id, task.name, task.color, task.created_at or datetime.now().isoformat(),
              getattr(task, 'keywords', '') or ''))
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
        cursor.execute("SELECT id, name, color, created_at, COALESCE(keywords, '') "
                       "FROM tasks WHERE is_active = 1 ORDER BY created_at")
        tasks = [Task(id=r[0], name=r[1], color=r[2], created_at=r[3], keywords=r[4])
                 for r in cursor.fetchall()]
        conn.close()
        return tasks

    def bump_keywords(self, task_id: str, terms, decay: float = 1.0):
        """将一批词累加到任务的关键词权重表。decay<1 时对历史轻微衰减以适应任务演变。"""
        if not task_id or not terms:
            return
        conn = self.get_conn()
        cursor = conn.cursor()
        if decay < 1.0:
            cursor.execute("UPDATE task_keywords SET weight = weight * ? WHERE task_id = ?",
                           (decay, task_id))
        for term in terms:
            cursor.execute('''
                INSERT INTO task_keywords (task_id, term, weight) VALUES (?, ?, 1)
                ON CONFLICT(task_id, term) DO UPDATE SET weight = weight + 1
            ''', (task_id, term))
        conn.commit()
        conn.close()

    def get_top_keywords(self, task_id: str, limit: int = 20):
        """返回 [(term, weight), ...]，按权重降序。"""
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT term, weight FROM task_keywords WHERE task_id = ? "
                       "ORDER BY weight DESC LIMIT ?", (task_id, limit))
        rows = cursor.fetchall()
        conn.close()
        return rows

    def sync_task_keywords_field(self, task_id: str, limit: int = 12):
        """把 top 关键词回写到 tasks.keywords 字段（逗号分隔），供快速读取。"""
        top = self.get_top_keywords(task_id, limit)
        kw = ",".join(t for t, _ in top)
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE tasks SET keywords = ? WHERE id = ?", (kw, task_id))
        conn.commit()
        conn.close()
        return kw
    
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


# 关键词抽取：中文 2/3-gram + 英文词，供自动学习加权与本地预筛
_RE_CJK = re.compile(r'[一-鿿]+')
_RE_EN = re.compile(r'[A-Za-z][A-Za-z0-9+#.\-]{1,}')
_EN_STOP = {'the', 'and', 'for', 'you', 'are', 'http', 'https', 'www', 'com', 'cn',
            'html', 'with', 'this', 'that', 'from', 'not', 'has', 'was', '但是',
            'new', 'get', 'all', 'can', 'org', 'net'}
# 高频中文虚词二元组，作为噪声过滤
_CN_STOP2 = {'我们', '你们', '他们', '这个', '那个', '一个', '可以', '因为', '所以',
             '但是', '如果', '就是', '这样', '那样', '什么', '怎么', '没有', '还是',
             '这些', '那些', '自己', '现在', '已经', '不是', '或者', '而且'}


def extract_terms(text, limit=60):
    """从文本抽取候选关键词集合（去重）。返回 list。"""
    if not text:
        return []
    low = text.lower()
    terms = set()
    for w in _RE_EN.findall(low):
        w = w.strip('.-')
        if len(w) >= 2 and w not in _EN_STOP:
            terms.add(w)
    for run in _RE_CJK.findall(text):
        n = len(run)
        for i in range(n - 1):
            bg = run[i:i + 2]
            if bg not in _CN_STOP2:
                terms.add(bg)
        for i in range(n - 2):
            terms.add(run[i:i + 3])
    return list(terms)[:limit]


class ContentReader:
    """用 UI Automation 读取前台窗口的网址与正文摘要。
    抓不到就返回空串，绝不抛异常、绝不阻塞 UI（搜索超时受 SetGlobalSearchTimeout 限制）。"""
    _BROWSERS = ('chrome.exe', 'msedge.exe', 'edge.exe', 'firefox.exe', 'opera.exe',
                 'brave.exe', '360se.exe', '360chrome.exe', 'qqbrowser.exe')
    _uia = None

    @staticmethod
    def _ensure():
        """首次在当前线程调用时初始化 uiautomation（会自动初始化本线程 COM）。"""
        global HAS_UIA
        if HAS_UIA is not None:
            return HAS_UIA
        try:
            import uiautomation as _uia
            _uia.SetGlobalSearchTimeout(1.0)
            ContentReader._uia = _uia
            HAS_UIA = True
        except Exception as e:
            print(f"[内容抓取] uiautomation 不可用，降级: {e}")
            HAS_UIA = False
        return HAS_UIA

    @staticmethod
    def is_browser(app_name):
        return any(b in (app_name or '').lower() for b in ContentReader._BROWSERS)

    @staticmethod
    def read(hwnd, app_name, want_body=True, body_limit=500):
        result = {'url': '', 'body': ''}
        if not hwnd or not ContentReader._ensure():
            return result
        uia = ContentReader._uia
        is_browser = ContentReader.is_browser(app_name)
        try:
            win = uia.ControlFromHandle(hwnd)
            if not win:
                return result
            if is_browser:
                result['url'] = ContentReader._read_url(win)
            if want_body:
                result['body'] = ContentReader._read_body(win, is_browser, body_limit)
        except Exception as e:
            print(f"[内容抓取] 失败: {e}")
        return result

    @staticmethod
    def _read_url(win):
        try:
            edit = win.EditControl(searchDepth=12)
            if edit.Exists(0.6, 0.1):
                try:
                    v = edit.GetValuePattern().Value
                except Exception:
                    v = edit.Name
                v = (v or '').strip()
                # 粗过滤：地址栏值一般无空格；纯提示文案（如“搜索或输入网址”）含空格
                if v and ' ' not in v:
                    return v
        except Exception:
            pass
        return ''

    @staticmethod
    def _read_body(win, is_browser, limit):
        # 优先文档控件，读 TextPattern（GetText 传 maxLength 防止拉取整页）
        try:
            doc = win.DocumentControl(searchDepth=25)
            if doc.Exists(0.8, 0.1):
                try:
                    txt = doc.GetTextPattern().DocumentRange.GetText(limit * 3)
                    txt = ContentReader._clean(txt, limit)
                    if txt:
                        return txt
                except Exception:
                    pass
        except Exception:
            pass
        # 退回：收集顶层子控件的 Name 文本
        try:
            parts = []
            total = 0
            for c in win.GetChildren():
                n = (c.Name or '').strip()
                if n:
                    parts.append(n)
                    total += len(n)
                if total > limit:
                    break
            return ContentReader._clean(' '.join(parts), limit)
        except Exception:
            return ''

    @staticmethod
    def _clean(txt, limit):
        if not txt:
            return ''
        return ' '.join(txt.split())[:limit]


class ArkClient:
    """火山方舟偏离判定客户端。给定任务与当前内容，返回 (relation, reason)。
    relation ∈ related|maybe_drift|drift；任何失败/超时都返回 None 交由上层降级。"""

    def __init__(self, cfg):
        self.api_key = cfg.get('ark_api_key', '')
        self.endpoint = cfg.get('ark_endpoint', '')
        self.model = cfg.get('ark_model', '')
        self._cache = {}       # key -> (ts, relation, reason)
        self._cache_ttl = 60   # 同一内容 60s 内不重复调用

    def available(self):
        return bool(self.api_key and self.endpoint and self.model)

    def classify(self, task_name, keywords, app, title, url, body):
        if not self.available():
            return None
        key = f"{task_name}|{app}|{url}|{title}|{(body or '')[:80]}"
        now = time.time()
        hit = self._cache.get(key)
        if hit and now - hit[0] < self._cache_ttl:
            return (hit[1], hit[2])

        sysmsg = ('你是工作偏离判定器。只输出一个 JSON：'
                  '{"relation":"related|maybe_drift|drift","reason":"一句话中文"}。不要输出多余内容。')
        parts = [f"任务: {task_name}"]
        if keywords:
            parts.append(f"关键词: {keywords}")
        cur = f"当前: 进程={app} 标题={title}"
        if url:
            cur += f" 网址={url}"
        parts.append(cur)
        if body:
            parts.append(f"正文摘要: {body}")
        usermsg = "\n".join(parts)

        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": sysmsg},
                         {"role": "user", "content": usermsg}],
            "temperature": 0.2,
            "max_tokens": 256,
        }
        try:
            req = urllib.request.Request(
                self.endpoint,
                data=json.dumps(payload).encode('utf-8'),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self.api_key}"},
                method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode('utf-8'))
            content = data["choices"][0]["message"]["content"]
            relation, reason = self._parse(content)
            if relation:
                self._cache[key] = (now, relation, reason)
                return (relation, reason)
            return None
        except Exception as e:
            print(f"[AI判定] 失败降级: {e}")
            return None

    @staticmethod
    def _parse(content):
        obj = None
        try:
            obj = json.loads(content)
        except Exception:
            import re
            m = re.search(r'\{.*\}', content or '', re.S)
            if m:
                try:
                    obj = json.loads(m.group(0))
                except Exception:
                    obj = None
        if not isinstance(obj, dict):
            return (None, '')
        rel = obj.get('relation', '')
        if rel not in ('related', 'maybe_drift', 'drift'):
            return (None, '')
        return (rel, obj.get('reason', ''))


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
        
        # 加载托盘图标：优先用打包的 worktrace.ico（取系统小图标尺寸，托盘最清晰），
        # 失败再回退到脚本资源 / 系统默认图标。
        self._hicon = None
        ico = _asset('worktrace.ico')
        try:
            if os.path.exists(ico):
                cx = win32api.GetSystemMetrics(win32con.SM_CXSMICON) or 16
                cy = win32api.GetSystemMetrics(win32con.SM_CYSMICON) or 16
                self._hicon = win32gui.LoadImage(
                    0, ico, win32con.IMAGE_ICON, cx, cy,
                    win32con.LR_LOADFROMFILE)
        except Exception as e:
            print(f"[托盘] 加载自定义图标失败: {e}")
        if not self._hicon:
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
        vs = _virtual_screen()
        if vs:
            vx, vy, vw, vh = vs
        else:
            vx, vy = 0, 0
            vw = win.winfo_screenwidth()
            vh = win.winfo_screenheight()
        ww = win.winfo_width()
        wh = win.winfo_height()
        x = win.winfo_x()
        y = win.winfo_y()
        # 仅在贴近整个虚拟桌面的外缘时磁吸，屏幕之间的内缝不吸，
        # 这样窗口可以自由停在副屏 B 上，不会被拉回主屏 A。
        if x < vx + SNAP:
            x = vx
        elif x > vx + vw - ww - SNAP:
            x = vx + vw - ww
        if y < vy + SNAP:
            y = vy
        elif y > vy + vh - wh - SNAP:
            y = vy + vh - wh
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


def _bind_title_drag(win, *widgets, on_snap_save=None):
    """仅让指定的标题栏 widget（及其子树）可拖动窗口，内容区不参与拖拽。
    跳过按钮（cursor='hand2'）和输入框。用于设置/统计/任务列表等含交互控件的面板，
    避免拖动滑块/内容时误拖窗口。全局统一：面板一律标题栏拖拽。"""
    SNAP = 20

    def _start(e):
        w = e.widget
        try:
            if str(w.cget('cursor')) == 'hand2':
                return
        except Exception:
            pass
        if isinstance(w, (tk.Entry, tk.Scale, tk.Scrollbar)):
            return
        win._drag_x = e.x_root - win.winfo_x()
        win._drag_y = e.y_root - win.winfo_y()

    def _move(e):
        if not hasattr(win, '_drag_x'):
            return
        win.geometry(f'+{e.x_root - win._drag_x}+{e.y_root - win._drag_y}')

    def _end(e):
        if not hasattr(win, '_drag_x'):
            return
        vs = _virtual_screen()
        if vs:
            vx, vy, vw, vh = vs
        else:
            vx, vy = 0, 0
            vw = win.winfo_screenwidth()
            vh = win.winfo_screenheight()
        ww, wh = win.winfo_width(), win.winfo_height()
        x, y = win.winfo_x(), win.winfo_y()
        if x < vx + SNAP:
            x = vx
        elif x > vx + vw - ww - SNAP:
            x = vx + vw - ww
        if y < vy + SNAP:
            y = vy
        elif y > vy + vh - wh - SNAP:
            y = vy + vh - wh
        win.geometry(f'+{x}+{y}')
        delattr(win, '_drag_x')
        if hasattr(win, '_drag_y'):
            delattr(win, '_drag_y')
        if on_snap_save:
            on_snap_save()

    def _bind_tree(widget):
        _start_ok = True
        try:
            if str(widget.cget('cursor')) == 'hand2':
                _start_ok = False
        except Exception:
            pass
        if not isinstance(widget, tk.Entry) and _start_ok:
            widget.bind('<Button-1>', _start, add='+')
            widget.bind('<B1-Motion>', _move, add='+')
            widget.bind('<ButtonRelease-1>', _end, add='+')
        for child in widget.winfo_children():
            _bind_tree(child)

    for w in widgets:
        _bind_tree(w)


class CyberScrollbar(tk.Canvas):
    def __init__(self, parent, command, width=10):
        t = theme()
        super().__init__(parent, width=width, bg=t["cream"],
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
        self.create_rectangle(1, 1, w - 1, h - 1,
                              fill=t["cream"], outline=t["black"], width=2)
        y0 = max(2, int(self.first * h))
        y1 = min(h - 2, max(y0 + 28, int(self.last * h)))
        self.create_rectangle(2, y0, w - 2, y1,
                              fill=t["teal"], outline=t["black"], width=2)

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
        self._modal_child = None

        self.root = tk.Toplevel(parent)
        self.root.title(title)
        self.root.attributes('-topmost', True)
        self.root.overrideredirect(True)

        t = theme()
        self.root.configure(bg=t["cream"])
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = 480
        row_h = 47
        max_visible_rows = 8
        visible_rows = min(max(len(self.options), 3), max_visible_rows)
        # 给选项区固定可见高度（满行，不再被估算的 chrome 高度反向挤压）；
        # 行数超过可见上限才滚动。窗口最终高度在 _finalize_size 里按实测内容决定，
        # 避免估算 chrome 偏小导致最底行被裁切。
        self._options_area_h = max(111, visible_rows * row_h)
        self._needs_option_scroll = len(self.options) > max_visible_rows
        self._dialog_w = w
        self._dialog_max_h = sh - 80
        self._dialog_parent = parent
        _place_centered(self.root, parent, w, 420)

        self._create_ui(title)
        self._finalize_size()

    def _finalize_size(self):
        """按实测内容高度确定窗口高度，避免估算 chrome 偏差导致最底行被裁切。"""
        try:
            self.root.update_idletasks()
            req_h = self.bd.winfo_reqheight() + 18
            h = max(350, min(req_h, self._dialog_max_h))
            _place_centered(self.root, self._dialog_parent, self._dialog_w, h)
        except Exception:
            pass

    def _create_ui(self, title: str):
        t = theme()
        self.bd = tk.Frame(self.root, bg=t["cream"], highlightthickness=3,
                           highlightbackground=t["black"])
        self.bd.pack(fill='both', expand=True)

        inner = tk.Frame(self.bd, bg=t["cream"])
        inner.pack(fill='both', expand=True)

        self._scanline_canvas = tk.Canvas(inner, bg=t["cream"], highlightthickness=0, bd=0)
        self._scanline_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._draw_scanline()

        hdr = tk.Frame(inner, bg=t["yellow"])
        hdr.pack(fill='x')

        title_box = tk.Frame(hdr, bg=t["yellow"], padx=12, pady=10)
        title_box.pack(side='left', fill='x', expand=True)
        rv = tk.Canvas(title_box, width=12, height=12, bg=t["yellow"], highlightthickness=0, bd=0)
        rv.pack(side='left', padx=(0, 8))
        rv.create_oval(1, 1, 11, 11, fill=t["black"], outline='')

        tk.Label(title_box, text="选择任务",
                 font=('Microsoft YaHei', 13, 'bold'),
                 bg=t["yellow"], fg=t["black"], anchor='w',
                 cursor='fleur').pack(side='left', padx=(4, 0))

        close_lbl = tk.Label(hdr, text="×",
                             font=('JetBrains Mono', 13, 'bold'),
                             bg=t["red"], fg=t["white"], width=3, cursor='hand2')
        close_lbl.pack(side='right', fill='y')
        close_lbl.bind('<Button-1>', lambda e: self._close())
        close_lbl.bind('<Enter>', lambda e: close_lbl.config(bg=t["black"], fg=t["yellow"]))
        close_lbl.bind('<Leave>', lambda e: close_lbl.config(bg=t["red"], fg=t["white"]))

        tk.Frame(inner, bg=t["black"], height=4).pack(fill='x')

        app, win_title = self._parse_context()
        guide = tk.Frame(inner, bg=t["cream"])
        guide.pack(fill='x', padx=14, pady=(10, 6))
        tk.Label(guide, text="已检测到当前应用与窗口",
                 font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["cream"], fg=t["black"], anchor='w').pack(fill='x')

        readout = tk.Frame(inner, bg=t["cream"], highlightthickness=3,
                           highlightbackground=t["black"])
        readout.pack(fill='x', padx=14, pady=(0, 6))

        top = tk.Frame(readout, bg=t["cream"], padx=10, pady=7)
        top.pack(fill='x')
        tk.Label(top, text="APP",
                 font=('JetBrains Mono', 8, 'bold'),
                 bg=t["cream"], fg=t["red"], width=5, anchor='w').pack(side='left')
        tk.Label(top, text=app[:18] or "W / TRACE",
                 font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["cream"], fg=t["black"], anchor='w').pack(side='left', fill='x', expand=True)

        bottom = tk.Frame(readout, bg=t["cream"], padx=10)
        bottom.pack(fill='x', pady=(0, 7))
        tk.Label(bottom, text="窗口 · " + (win_title[:46] or title),
                 font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["cream"], fg=t["muted"], anchor='w').pack(fill='x')

        tk.Label(inner, text="请选择要切换的任务，或在下方创建新任务。双击任务，或选中后按 Enter 确认。",
                 font=('Microsoft YaHei', 8, 'bold'),
                 bg=t["cream"], fg=t["black"], anchor='w').pack(fill='x', padx=14, pady=(0, 8))

        options_shell = tk.Frame(inner, bg=t["cream"])
        options_shell.pack(fill='x', padx=14)

        if self._needs_option_scroll:
            # 任务多于可见上限：固定可视高度 + 滚动条
            options_shell.configure(height=self._options_area_h)
            options_shell.pack_propagate(False)
            options_canvas = tk.Canvas(options_shell, bg=t["cream"],
                                       highlightthickness=0, bd=0)
            options_scroll = CyberScrollbar(options_shell, options_canvas.yview)
            options_wrap = tk.Frame(options_canvas, bg=t["cream"])
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
            # 任务不多：让选项区按实际行高自适应（不固定高度），窗口高度由
            # _finalize_size 实测决定，避免最底行被裁切
            options_wrap = tk.Frame(options_shell, bg=t["cream"])
            options_wrap.pack(fill='both', expand=True)
            self._options_canvas = None

        self._options_wrap = options_wrap

        if self.options:
            for i, opt in enumerate(self.options):
                self._make_option_row(options_wrap, i, opt)
        else:
            empty = tk.Frame(options_wrap, bg=t["cream"], highlightthickness=3,
                             highlightbackground=t["black"])
            empty.pack(fill='x', pady=(0, 8))
            tk.Label(empty, text="NO TASKS · ADD BELOW",
                     font=('JetBrains Mono', 10, 'bold'),
                     bg=t["cream"], fg=t["red"]).pack(pady=15)

        self.input_wrap = tk.Frame(inner, bg=t["cream"], highlightthickness=3,
                                   highlightbackground=t["black"])
        self.input_wrap.pack(fill='x', padx=14, pady=(8, 0))

        input_row = tk.Frame(self.input_wrap, bg=t["cream"], padx=10, pady=7)
        input_row.pack(fill='x')
        tk.Label(input_row, text=">",
                 font=('JetBrains Mono', 11, 'bold'),
                 bg=t["cream"], fg=t["red"]).pack(side='left')

        self.entry = tk.Entry(input_row, font=('Microsoft YaHei', 10, 'bold'),
                              bg=t["cream"], fg=t["black"],
                              insertbackground=t["teal"],
                              relief='flat', bd=0)
        self.entry.pack(side='left', fill='x', expand=True, padx=(7, 0))
        self.entry.insert(0, "输入新任务名")
        self.entry.bind('<FocusIn>', lambda e: self._on_entry_focus())
        self.entry.bind('<Button-1>', lambda e: self._select_input(), add='+')
        self.entry.bind('<Return>', lambda e: self._confirm())
        self.entry.bind('<Escape>', lambda e: self._close())
        self.entry.bind('<Up>', lambda e: self._entry_move_to_options())
        self.entry.bind('<Down>', lambda e: self._entry_move_to_options())

        help_row = tk.Frame(inner, bg=t["cream"])
        help_row.pack(fill='x', padx=14, pady=(8, 12))

        for txt, clr in [
            ("↑/↓ 选择", t["ink_3"]),
            ("Enter 确认", t["ink_3"]),
            ("输入框新建", t["ink_3"]),
            ("R 休息", t["drift"]),
            ("Esc 取消", t["ink_3"]),
        ]:
            tk.Label(help_row, text=txt,
                     font=('Microsoft YaHei', 8, 'bold'),
                     bg=t["cream"], fg=clr).pack(side='left', padx=(0, 10))

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

        def bind_one(widget):
            # 跳过交互控件（按钮 hand2 / 输入框），其余区域可拖拽
            try:
                if str(widget.cget('cursor')) == 'hand2':
                    return
            except Exception:
                pass
            if isinstance(widget, tk.Entry):
                return
            widget.bind('<Button-1>', start, add='+')
            widget.bind('<B1-Motion>', move, add='+')
            widget.bind('<ButtonRelease-1>', end, add='+')

        def bind_tree(widget):
            bind_one(widget)
            for child in widget.winfo_children():
                bind_tree(child)

        for widget in widgets:
            bind_tree(widget)

    def _activate_focus(self):
        # 重命名/删除等模态子窗打开时，主弹窗不要抢焦点——focus_set 在 Windows 上
        # 会把主弹窗抬到置顶子窗之上，导致子窗看似"隐藏"。
        if getattr(self, '_modal_child', None) is not None:
            try:
                if self._modal_child.winfo_exists():
                    self._modal_child.lift()
                    return
            except Exception:
                self._modal_child = None
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
        self.bd.config(highlightbackground=t["red"] if focused else t["black"])

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
        bg = t["cream"]
        row = tk.Frame(parent, bg=bg, highlightthickness=2,
                       highlightbackground=t["black"], cursor='hand2')
        row.pack(fill='x', pady=(0, 6))

        stripe = tk.Canvas(row, width=5, height=34, bg=bg,
                           highlightthickness=0, bd=0, cursor='hand2')
        stripe.pack(side='left', fill='y')

        key_lbl = tk.Label(row, text=f"[{idx + 1}]",
                           font=('JetBrains Mono', 10, 'bold'),
                           bg=bg, fg=t["black"], cursor='hand2')
        key_lbl.pack(side='left', padx=(10, 8), pady=6)

        name_lbl = tk.Label(row, text=opt[:28],
                            font=('Microsoft YaHei', 10, 'bold'),
                            bg=bg, fg=t["black"], anchor='w', cursor='hand2')
        name_lbl.pack(side='left', fill='x', expand=True, pady=6)

        edit_btn = None
        del_btn = None
        if idx < len(self.task_ids):
            tid = self.task_ids[idx]
            if self.on_task_edit:
                edit_btn = tk.Label(row, text="改", font=('Microsoft YaHei', 8, 'bold'),
                                    bg=t["white"], fg=t["black"], cursor='hand2', padx=5,
                                    highlightthickness=2, highlightbackground=t["black"])
                edit_btn.pack(side='right', padx=(0, 3))
                edit_btn.bind('<Button-1>', lambda e, tid=tid, opt=opt: self._inline_edit(tid, opt))
            if self.on_task_delete:
                del_btn = tk.Label(row, text="删", font=('Microsoft YaHei', 8, 'bold'),
                                   bg=t["white"], fg=t["red"], cursor='hand2', padx=5,
                                   highlightthickness=2, highlightbackground=t["black"])
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
            bg = t["white"] if selected else t["cream"]
            data['row'].config(bg=bg, highlightbackground=t["black"])
            data['stripe'].config(bg=bg)
            data['stripe'].delete('all')
            data['stripe'].create_rectangle(0, 0, 5, 44,
                                            fill=t["yellow"] if selected else t["teal"],
                                            outline='')
            data['key'].config(fg=t["red"] if selected else t["black"], bg=bg)
            data['name'].config(fg=t["black"], bg=bg)
            if data['edit']:
                data['edit'].config(bg=t["white"])
            if data['delete']:
                data['delete'].config(bg=t["white"])

        input_selected = self._selected_index == self._input_index
        self.input_wrap.config(highlightbackground=t["red"] if input_selected else t["black"])
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
        d.withdraw()
        d.overrideredirect(True)
        d.configure(bg=t["cream"])
        d.attributes('-topmost', True)

        f = tk.Frame(d, bg=t["cream"], highlightthickness=3,
                     highlightbackground=t["black"], padx=14, pady=12)
        f.pack()

        tk.Label(f, text=f"重命名: {option_text}",
                 font=('Microsoft YaHei', 10, 'bold'),
                 bg=t["cream"], fg=t["black"]).pack(anchor='w', pady=(0, 8))

        e = tk.Entry(f, font=('Microsoft YaHei', 10, 'bold'),
                     bg=t["white"], fg=t["black"],
                     insertbackground=t["teal"], relief='flat', bd=0,
                     highlightthickness=3, highlightbackground=t["black"])
        e.pack(ipady=6, ipadx=8)
        e.insert(0, option_text)
        e.select_range(0, 'end')
        e.focus_set()

        def _close_edit(ev=None):
            self._modal_child = None
            try:
                d.grab_release()
            except Exception:
                pass
            d.destroy()

        def do_save(ev=None):
            new_name = e.get().strip()
            if new_name and new_name != option_text:
                if self.on_task_edit:
                    self.on_task_edit(task_id, new_name)
                try:
                    idx = self.task_ids.index(task_id)
                    self.options[idx] = new_name
                except ValueError:
                    pass
                self._rebuild_options()
            _close_edit()

        e.bind('<Return>', do_save)
        e.bind('<Escape>', _close_edit)

        _place_overlay(d, self.root, 280, 80)
        e.focus_set()
        _win_set_topmost(d, True)
        # 标记为模态子窗：主弹窗在此期间不抢焦点；grab_set 双保险。
        self._modal_child = d
        d.bind('<Destroy>', lambda ev: setattr(self, '_modal_child', None) if ev.widget is d else None)
        try:
            d.grab_set()
        except Exception:
            pass

    def _delete_task(self, task_id, task_name):
        if self.on_task_delete:
            confirmed = self.on_task_delete(task_id, task_name)
            if confirmed:
                try:
                    idx = self.task_ids.index(task_id)
                    self.task_ids.pop(idx)
                    self.options.pop(idx)
                except ValueError:
                    pass
                self._rebuild_options()

    def _rebuild_options(self):
        wrap = getattr(self, '_options_wrap', None)
        if not wrap:
            return
        for child in wrap.winfo_children():
            child.destroy()
        self._option_rows = []
        self._input_index = len(self.options)
        if self._selected_index > len(self.options):
            self._selected_index = self._input_index
        t = theme()
        if self.options:
            for i, opt in enumerate(self.options):
                self._make_option_row(wrap, i, opt)
        else:
            empty = tk.Frame(wrap, bg=t["cream"], highlightthickness=3,
                             highlightbackground=t["black"])
            empty.pack(fill='x', pady=(0, 8))
            tk.Label(empty, text="NO TASKS · ADD BELOW",
                     font=('JetBrains Mono', 10, 'bold'),
                     bg=t["cream"], fg=t["red"]).pack(pady=15)
        if getattr(self, '_options_canvas', None):
            self._options_canvas.update_idletasks()
            self._options_canvas.configure(scrollregion=self._options_canvas.bbox('all'))
        self._refresh_selection()

    def show(self) -> Optional[str]:
        self.root.lift()
        _win_set_topmost(self.root, True)
        self._parent.wait_window(self.root)
        return self.result


class SettingsWindow:
    """设置窗口 — Memphis 风格。"""

    def theme(self):
        return {
            "red": "#FF6B6B",
            "teal": "#4ECDC4",
            "yellow": "#FFE66D",
            "black": "#000000",
            "white": "#FFFFFF",
            "cream": "#FFF8DC",
            "muted": "#666666"
        }

    def __init__(self, parent, cfg: dict, on_save=None):
        self.cfg = dict(cfg)
        self.on_save = on_save
        self.parent = parent
        self._vars = {}
        self._sliders = {}      # key -> {'canvas','draw','val_lbl','min','max','step','unit'}
        self._toggles = {}      # key -> {'canvas','draw'}
        self._orig_auto_launch = bool(cfg.get("auto_launch", False))

        t = self.theme()
        self.win = tk.Toplevel(parent)
        self.win.title("SETTINGS")
        self.win.overrideredirect(True)
        self.win.configure(bg=t["cream"])
        self.win.attributes('-topmost', True)

        self.win.update_idletasks()
        w, h = 340, 600
        _place_centered(self.win, parent, w, h)

        self._create_ui()

        self.win.bind('<Escape>', lambda e: self.win.destroy())
        self.win.bind('<Control-s>', lambda e: self._save())
        self.win.focus_set()
        _win_set_topmost(self.win, True)

    # ---------- UI 构建 ----------
    def _create_ui(self):
        t = self.theme()
        bd = tk.Frame(self.win, bg=t["cream"], highlightthickness=4,
                      highlightbackground=t["black"])
        bd.pack(fill='both', expand=True)

        hdr = tk.Frame(bd, bg=t["yellow"], highlightthickness=0)
        hdr.pack(fill='x')

        title_box = tk.Frame(hdr, bg=t["yellow"], padx=12, pady=10)
        title_box.pack(side='left', fill='x', expand=True)

        rivet = tk.Canvas(title_box, width=11, height=11, bg=t["yellow"], highlightthickness=0)
        rivet.pack(side='left', padx=(0, 8))
        rivet.create_oval(1, 1, 10, 10, fill=t["black"], outline='')

        title_lbl = tk.Label(title_box, text="SETTINGS", font=('JetBrains Mono', 14, 'bold'),
                             bg=t["yellow"], fg=t["black"], anchor='w', cursor='fleur')
        title_lbl.pack(side='left')

        close_lbl = tk.Label(hdr, text="×", font=('JetBrains Mono', 13, 'bold'),
                             bg=t["red"], fg=t["white"], width=3, cursor='hand2')
        close_lbl.pack(side='right', fill='y')
        close_lbl.bind('<Button-1>', lambda e: self.win.destroy())
        close_lbl.bind('<Enter>', lambda e, l=close_lbl: l.config(bg=t["black"], fg=t["yellow"]))
        close_lbl.bind('<Leave>', lambda e, l=close_lbl: l.config(bg=t["red"], fg=t["white"]))

        tk.Frame(bd, bg=t["black"], height=4).pack(fill='x')

        scroll_wrap = tk.Frame(bd, bg=t["cream"])
        scroll_wrap.pack(fill='both', expand=True, padx=12, pady=(12, 0))

        canvas = tk.Canvas(scroll_wrap, bg=t["cream"], highlightthickness=0, bd=0)
        scrollbar = CyberScrollbar(scroll_wrap, canvas.yview)
        content = tk.Frame(canvas, bg=t["cream"])

        content.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=content, anchor='nw', width=286)
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
                          5, 60, self.cfg.get("pomodoro_minutes", 30), 5, "分钟",
                          "一轮连续投入工作的目标时长，主面板进度条按这个周期循环。")
        self._make_slider(content, "短恢复", "rest_per_pomodoro",
                          1, 30, self.cfg.get("rest_per_pomodoro", 5), 1, "分钟",
                          "每完成一个专注单元后，系统用于统计建议恢复时间的基准。")
        self._make_slider(content, "长恢复", "long_rest_minutes",
                          5, 60, self.cfg.get("long_rest_minutes", 15), 5, "分钟",
                          "连续完成多轮专注后建议安排的一段较长恢复时间。")

        # ---- 追踪阈值 ----
        self._make_plate(content, "追踪阈值")
        self._make_slider(content, "检测频率", "check_interval",
                          1, 5, int(self.cfg["check_interval"] // 60), 1, "分钟",
                          "后台检查当前窗口、空闲状态和偏离状态的间隔。数值越小越敏感。",
                          scale=60)
        self._make_slider(content, "离开判定", "idle_threshold",
                          1, 15, int(self.cfg["idle_threshold"] // 60), 1, "分钟",
                          "鼠标键盘无操作超过该时间后，自动记为离开/空闲。",
                          scale=60)
        self._make_slider(content, "偏离容忍", "reminder_interval",
                          2, 30, int(self.cfg["reminder_interval"] // 60), 1, "分钟",
                          "当前窗口与任务上下文不一致持续超过该时间后，弹出确认。",
                          scale=60)
        self._make_slider(content, "锁屏检测", "lock_check_interval",
                          1, 10, self.cfg["lock_check_interval"], 1, "秒",
                          "检查系统是否锁屏的间隔（秒级轮询），用于更快记录锁屏/离开。")
        self._make_slider(content, "无操作判定", "no_input_threshold",
                          1, 10, int(self.cfg.get("no_input_threshold", 180) // 60), 1, "分钟",
                          "无鼠标键盘输入超过该时间后，累计到今日无操作统计。",
                          scale=60)

        # ---- 启动 ----
        self._make_plate(content, "启动")
        self._make_toggle(content, "完成提示音", "pomodoro_sound")
        self._make_toggle(content, "吸边隐藏", "edge_hide")
        self._make_toggle(content, "关闭到托盘", "minimize_to_tray")
        self._make_toggle(content, "静默启动", "silent_start")
        self._make_toggle(content, "自动追踪", "auto_start_track")
        self._make_toggle(content, "开机自启", "auto_launch")

        # ---- AI 判定 ----
        self._make_plate(content, "AI 判定")
        self._make_toggle(content, "内容感知偏离", "ai_enabled")
        self._make_toggle(content, "正文外发", "body_send")

        tk.Frame(bd, bg=t["black"], height=4).pack(fill='x', pady=(8, 0))

        btn_row = tk.Frame(bd, bg=t["cream"])
        btn_row.pack(fill='x', padx=0, pady=0)

        inner_btn = tk.Frame(btn_row, bg=t["cream"], padx=12, pady=12)
        inner_btn.pack(fill='x')

        self._make_metal_btn(inner_btn, "恢复默认", self._reset_defaults,
                             kind='danger').pack(side='left')

        right_box = tk.Frame(inner_btn, bg=t["cream"])
        right_box.pack(side='right')
        self._make_metal_btn(right_box, "取消",
                             lambda: self.win.destroy(),
                             kind='normal').pack(side='left', padx=(0, 8))
        self._make_metal_btn(right_box, "保存", self._save,
                             kind='primary').pack(side='left')

        # 拖拽 — 仅标题栏
        _bind_title_drag(self.win, hdr)

    # ---------- 视觉组件 ----------
    def _make_plate(self, parent, text):
        t = self.theme()
        row = tk.Frame(parent, bg=t["red"], highlightthickness=3, highlightbackground=t["black"])
        row.pack(fill='x', pady=(12, 8))

        inner = tk.Frame(row, bg=t["red"], padx=8, pady=4)
        inner.pack(fill='x')
        rivet = tk.Canvas(inner, width=9, height=9, bg=t["red"], highlightthickness=0)
        rivet.pack(side='left', padx=(0, 6))
        rivet.create_oval(1, 1, 8, 8, fill=t["white"], outline=t["black"], width=1)

        tk.Label(inner, text=text, font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["red"], fg=t["white"]).pack(side='left', padx=(0, 8))

    def _show_help_tip(self, widget, text):
        self._hide_help_tip()
        t = self.theme()
        tip = tk.Toplevel(self.win)
        tip.withdraw()
        tip.overrideredirect(True)
        tip.attributes('-topmost', True)
        tip.configure(bg=t["black"])
        box = tk.Frame(tip, bg=t["cream"], highlightthickness=3,
                       highlightbackground=t["black"], padx=8, pady=6)
        box.pack()
        tk.Label(box, text=text, font=('Microsoft YaHei', 8), bg=t["cream"], fg=t["black"],
                 justify='left', wraplength=220).pack()
        x = widget.winfo_rootx() + 14
        y = widget.winfo_rooty() + 16
        geo = f'+{x}+{y}'
        tip.geometry(geo)
        tip.deiconify()
        tip.update_idletasks()
        tip.geometry(geo)  # overrideredirect 首次映射可能落到 (0,0)，再设一次
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
                     current, step, unit, help_text=None, scale=1):
        t = self.theme()
        row = tk.Frame(parent, bg=t["white"])
        row.pack(fill='x', pady=5)

        label_box = tk.Frame(row, bg=t["white"], width=90, height=28)
        label_box.pack(side='left')
        label_box.pack_propagate(False)
        tk.Label(label_box, text=label, font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["white"], fg=t["black"], anchor='w').pack(side='left')
        if help_text:
            help_lbl = tk.Label(label_box, text="?", font=('JetBrains Mono', 8, 'bold'),
                                bg=t["yellow"], fg=t["black"], width=2, cursor='question_arrow',
                                highlightthickness=2, highlightbackground=t["black"])
            help_lbl.pack(side='left', padx=(5, 0))
            help_lbl.bind('<Enter>', lambda e, txt=help_text: self._show_help_tip(e.widget, txt))
            help_lbl.bind('<Leave>', lambda e: self._hide_help_tip())

        val_lbl = tk.Label(row, text=f"{current}{unit}", font=('JetBrains Mono', 10, 'bold'),
                           bg=t["white"], fg=t["red"], anchor='e', width=7)
        val_lbl.pack(side='right')

        cw, ch = 132, 24
        c = tk.Canvas(row, width=cw, height=ch, bg=t["white"], highlightthickness=0, bd=0, cursor='hand2')
        c.pack(side='right', padx=(6, 8))

        var = tk.IntVar(value=current)
        self._vars[key] = var

        def draw():
            t2 = self.theme()
            c.delete('all')
            v = var.get()
            ratio = (v - min_val) / max(1, (max_val - min_val))
            ratio = max(0, min(1, ratio))
            mid_y = ch // 2
            track_left = 10
            track_right = cw - 10
            c.create_line(track_left, mid_y, track_right, mid_y, fill=t2["black"], width=4)
            for i in range(5):
                x = track_left + int((track_right - track_left) * i / 4)
                c.create_line(x, mid_y - 7, x, mid_y + 7, fill=t2["black"], width=2)
            knob_x = track_left + int((track_right - track_left) * ratio)
            c.create_line(track_left, mid_y, knob_x, mid_y, fill=t2["teal"], width=8)
            c.create_oval(knob_x - 10, mid_y - 10, knob_x + 10, mid_y + 10,
                          fill=t2["yellow"], outline=t2["black"], width=3)

        def set_from_x(px):
            track_left = 10
            track_right = cw - 10
            ratio = (px - track_left) / max(1, (track_right - track_left))
            ratio = max(0, min(1, ratio))
            v = min_val + ratio * (max_val - min_val)
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
            'scale': scale,
        }

    def _make_toggle(self, parent, label, key):
        t = self.theme()
        row = tk.Frame(parent, bg=t["white"])
        row.pack(fill='x', pady=6)

        tk.Label(row, text=label, font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["white"], fg=t["black"], anchor='w').pack(side='left')

        var = tk.BooleanVar(value=bool(self.cfg.get(key, False)))
        self._vars[key] = var

        cw, ch = 48, 24
        c = tk.Canvas(row, width=cw, height=ch, bg=t["white"], highlightthickness=0, bd=0, cursor='hand2')
        c.pack(side='right')

        def draw():
            t2 = self.theme()
            c.delete('all')
            on = var.get()
            fill = t2["teal"] if on else t2["white"]
            c.create_rectangle(0, 0, cw, ch, fill=fill, outline=t2["black"], width=3)
            kx0 = cw - 20 if on else 4
            c.create_rectangle(kx0, 5, kx0 + 14, 19,
                               fill=t2["white"] if on else t2["black"], outline='', width=0)

        def toggle(e=None):
            var.set(not var.get())
            draw()

        c.bind('<Button-1>', toggle)
        draw()
        self._toggles[key] = {'canvas': c, 'draw': draw}

    def _make_metal_btn(self, parent, text, on_click, kind='normal'):
        t = self.theme()
        if kind == 'primary':
            bg = t["teal"]
            fg = t["black"]
        elif kind == 'danger':
            bg = t["red"]
            fg = t["white"]
        else:
            bg = t["white"]
            fg = t["black"]

        btn = tk.Label(parent, text=text, font=('Microsoft YaHei', 9, 'bold'),
                       bg=bg, fg=fg, padx=14, pady=6, relief='flat', cursor='hand2',
                       highlightthickness=3, highlightbackground=t["black"])
        btn.bind('<Button-1>', lambda e: on_click())
        btn.bind('<Enter>', lambda e, b=btn: b.config(bg=t["yellow"], fg=t["black"]))
        btn.bind('<Leave>', lambda e, b=btn, bg0=bg, fg0=fg: b.config(bg=bg0, fg=fg0))
        return btn

    # ---------- 操作 ----------
    def _save(self):
        for key, var in self._vars.items():
            val = var.get()
            info = self._sliders.get(key)
            if info and info.get('scale', 1) != 1:
                val = val * info['scale']  # 显示单位(分钟)→存储单位(秒)
            self.cfg[key] = val
        cfg_snapshot = dict(self.cfg)
        auto_changed = bool(cfg_snapshot.get("auto_launch", False)) != self._orig_auto_launch
        on_save = self.on_save
        parent = self.parent

        # 先关闭窗口，让界面立即响应；保存/注册表等放到下一帧执行
        self.win.destroy()

        def _persist():
            save_config(cfg_snapshot)
            if auto_changed:
                _apply_auto_launch(cfg_snapshot.get("auto_launch", False))
            if on_save:
                on_save(cfg_snapshot)

        try:
            parent.after(0, _persist)
        except Exception:
            _persist()

    def _reset_defaults(self):
        for key, var in self._vars.items():
            default = DEFAULT_CONFIG.get(key, var.get())
            info = self._sliders.get(key)
            if info and info.get('scale', 1) != 1 and isinstance(default, (int, float)):
                default = int(default // info['scale'])  # 存储单位(秒)→显示单位(分钟)
            var.set(default)
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
        self.win.configure(bg=t["cream"])
        self.win.attributes('-topmost', True)

        self.win.update_idletasks()
        self._stats_w = 520
        _place_centered(self.win, parent, self._stats_w, 460)

        self._create_ui()
        self._load_data()
        self._fit_height()
        self.win.bind('<Escape>', lambda e: self.win.destroy())

    # ================================================================
    #  UI 构建
    # ================================================================

    def _create_ui(self):
        t = theme()

        bd = tk.Frame(self.win, bg=t["cream"], highlightthickness=4,
                      highlightbackground=t["black"])
        bd.pack(fill='both', expand=True)

        inner = tk.Frame(bd, bg=t["cream"])
        inner.pack(fill='both', expand=True)

        self._scanline_canvas = tk.Canvas(inner, bg=t["cream"], highlightthickness=0, bd=0)
        self._scanline_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._draw_scanline()

        titlebar = tk.Frame(inner, bg=t["red"])
        titlebar.pack(fill='x')

        title_box = tk.Frame(titlebar, bg=t["red"], padx=12, pady=10)
        title_box.pack(side='left', fill='x', expand=True)
        rv = tk.Canvas(title_box, width=12, height=12, bg=t["red"], highlightthickness=0, bd=0)
        rv.pack(side='left', padx=(0, 8))
        rv.create_oval(1, 1, 11, 11, fill=t["white"], outline=t["black"], width=2)

        tk.Label(title_box, text="统计",
                 font=('Microsoft YaHei', 13, 'bold'),
                 bg=t["red"], fg=t["white"], cursor='fleur').pack(side='left', padx=(4, 0))

        close_lbl = tk.Label(titlebar, text="×",
                             font=('JetBrains Mono', 13, 'bold'),
                             bg=t["black"], fg=t["yellow"], width=3, cursor='hand2')
        close_lbl.pack(side='right', fill='y')

        nav = tk.Frame(titlebar, bg=t["red"])
        nav.place(relx=0.5, rely=0.5, anchor='center')

        prev_btn = tk.Label(nav, text="<",
                            font=('JetBrains Mono', 10, 'bold'),
                            bg=t["white"], fg=t["black"], cursor='hand2',
                            highlightthickness=2, highlightbackground=t["black"], padx=6)
        prev_btn.pack(side='left', padx=(0, 6))

        self.date_lbl = tk.Label(nav, text="--",
                                 font=('JetBrains Mono', 10, 'bold'),
                                 bg=t["white"], fg=t["black"], padx=8, pady=2,
                                 highlightthickness=2, highlightbackground=t["black"])
        self.date_lbl.pack(side='left')

        next_btn = tk.Label(nav, text=">",
                            font=('JetBrains Mono', 10, 'bold'),
                            bg=t["white"], fg=t["black"], cursor='hand2',
                            highlightthickness=2, highlightbackground=t["black"], padx=6)
        next_btn.pack(side='left', padx=(6, 0))
        close_lbl.bind('<Button-1>', lambda e: self.win.destroy())
        close_lbl.bind('<Enter>', lambda e: close_lbl.config(bg=t["yellow"], fg=t["black"]))
        close_lbl.bind('<Leave>', lambda e: close_lbl.config(bg=t["black"], fg=t["yellow"]))

        tk.Frame(inner, bg=t["black"], height=4).pack(fill='x')

        # ---- KPI 卡片行 ----
        kpi_row = tk.Frame(inner, bg=t["cream"])
        kpi_row.pack(fill='x', padx=12, pady=(12, 0))

        self.kpi_cards = {}
        kpi_configs = [
            ("tasks", "任务数", t["teal"]),
            ("total", "总时长", t["red"]),
            ("focused", "专注", t["yellow"]),
            ("efficiency", "效率", t["black"]),
        ]
        for key, name, color in kpi_configs:
            self.kpi_cards[key] = self._make_kpi_card(kpi_row, name, color)

        # ---- 本周概览 + 月环 ----
        week_block = tk.Frame(inner, bg=t["cream"])
        week_block.pack(fill='x', padx=18, pady=(10, 0))

        # 左侧：柱状图
        chart_col = tk.Frame(week_block, bg=t["cream"])
        chart_col.pack(side='left', fill='x', expand=True)

        self._make_plate(chart_col, "本周概览")

        self.chart_canvas = tk.Canvas(chart_col, bg=t["cream"], height=78,
                                      highlightthickness=3, highlightbackground=t["black"], bd=0)
        self.chart_canvas.pack(fill='x', pady=(8, 2))

        self.chart_labels = tk.Frame(chart_col, bg=t["cream"])
        self.chart_labels.pack(fill='x')

        # 右侧：月环
        ring_col = tk.Frame(week_block, bg=t["cream"], width=110)
        ring_col.pack(side='right', fill='y')
        ring_col.pack_propagate(False)

        self.ring_canvas = tk.Canvas(ring_col, bg=t["cream"], width=80, height=80,
                                     highlightthickness=0, bd=0)
        self.ring_canvas.pack(pady=(10, 0))

        self.ring_meta = tk.Label(ring_col,
                                  text="目标 100h · --",
                                  font=('JetBrains Mono', 9, 'bold'),
                                  bg=t["cream"], fg=t["muted"])
        self.ring_meta.pack()

        # ---- 任务排行 ----
        rank_section = tk.Frame(inner, bg=t["cream"])
        rank_section.pack(fill='x', padx=18, pady=(10, 0))

        self._make_plate(rank_section, "任务排行")

        self.rank_frame = tk.Frame(rank_section, bg=t["cream"])
        self.rank_frame.pack(fill='x', pady=(6, 0))

        # ---- Footer ----
        footer = tk.Frame(inner, bg=t["cream"])
        footer.pack(fill='x', padx=18, pady=(10, 14))

        tk.Frame(footer, bg=t["rule"], height=1).pack(fill='x', pady=(0, 8))

        self.footer_lbl = tk.Label(footer,
                                   text="日均 --  |  最长连续 -- 天",
                                   font=('JetBrains Mono', 9, 'bold'),
                                   bg=t["cream"], fg=t["muted"])
        self.footer_lbl.pack()

        # 拖拽 — 仅标题栏
        _bind_title_drag(self.win, titlebar)

    def _fit_height(self):
        self.win.update_idletasks()
        vs = _virtual_screen()
        if vs:
            vy, vh = vs[1], vs[3]
        else:
            vy, vh = 0, self.win.winfo_screenheight()
        req_h = min(max(420, self.win.winfo_reqheight() + 2), vh - 80)
        x = self.win.winfo_x()
        y = max(vy + 40, min(self.win.winfo_y(), vy + vh - req_h - 40))
        self.win.geometry(f'{self._stats_w}x{req_h}+{x}+{y}')
        self._draw_scanline()

    def _draw_scanline(self):
        c = self._scanline_canvas
        c.delete('all')
        cw = c.winfo_width() or 520
        ch = c.winfo_height() or 540
        t2 = theme()
        for x in range(18, cw, 64):
            c.create_oval(x, 62, x + 5, 67, fill=t2["yellow"], outline='')
        for x in range(12, cw, 72):
            c.create_line(x, ch - 34, x + 16, ch - 18, fill=t2["teal"], width=2)

    def _make_plate(self, parent, text):
        t2 = theme()
        wrap = tk.Frame(parent, bg=t2["cream"])
        wrap.pack(fill='x')

        tag = tk.Label(wrap, text=text,
                       font=('Microsoft YaHei', 9, 'bold'),
                       bg=t2["yellow"], fg=t2["black"], padx=8, pady=2,
                       highlightthickness=2, highlightbackground=t2["black"])
        tag.pack(side='left')

    def _make_kpi_card(self, parent, name, color):
        t2 = theme()
        card = tk.Frame(parent, bg=t2["cream"], highlightthickness=3,
                        highlightbackground=t2["black"])
        card.pack(side='left', expand=True, fill='x', padx=(0, 6))

        stripe = tk.Canvas(card, bg=t2["cream"], height=5,
                           highlightthickness=0, bd=0)
        stripe.pack(fill='x')
        stripe.create_rectangle(0, 0, 220, 5, fill=color, outline='')

        val = tk.Label(card, text="--",
                       font=('JetBrains Mono', 22, 'bold'),
                       bg=t2["cream"], fg=t2["black"])
        val.pack(pady=(7, 0))

        lb = tk.Label(card, text=name,
                      font=('Microsoft YaHei', 9, 'bold'),
                      bg=t2["cream"], fg=t2["muted"])
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
        h = 78
        n = len(daily_data)
        gap = 7
        bar_w = max(12, (w - gap * (n + 1)) // n)
        max_val = max(max_daily, 1)
        colors = [t2["teal"], t2["red"], t2["yellow"]]

        for i, (ds, sec, is_today, is_future) in enumerate(daily_data):
            x = gap + i * (bar_w + gap)
            bar_h = max(5, (sec / max_val) * (h - 22)) if max_val > 0 else 5
            y = h - 10 - bar_h
            fill = t2["cream"] if is_future else colors[i % len(colors)]
            if is_today:
                fill = t2["red"]
            c.create_rectangle(x, y, x + bar_w, h - 10,
                               fill=fill, outline=t2["black"], width=3)
            if is_future:
                c.create_line(x + 3, y + 3, x + bar_w - 3, h - 13,
                              fill=t2["black"], width=1)

            lbl = dow_labels[i]
            lbl_fg = t2["red"] if is_today else t2["black"]
            tk.Label(self.chart_labels, text=lbl,
                     font=('JetBrains Mono', 8, 'bold'),
                     bg=t2["cream"], fg=lbl_fg
                     ).pack(side='left', expand=True)

    def _draw_ring(self, pct):
        """月环进度：Canvas 弧形。"""
        t2 = theme()
        c = self.ring_canvas
        c.delete('all')

        cx, cy, r = 40, 40, 30
        c.create_oval(cx - r - 7, cy - r - 7, cx + r + 7, cy + r + 7,
                      fill=t2["cream"], outline=t2["black"], width=3)
        c.create_arc(cx - r, cy - r, cx + r, cy + r,
                     start=0, extent=359.9,
                     style='arc', outline=t2["white"], width=8)
        extent = 359.9 * pct / 100
        c.create_arc(cx - r, cy - r, cx + r, cy + r,
                     start=-90, extent=extent,
                     style='arc', outline=t2["red"], width=8)

        pct_text = f"{pct}%" if pct >= 1 else "<1%"
        c.create_text(cx, cy - 3, text=pct_text,
                      font=('JetBrains Mono', 15, 'bold'),
                      fill=t2["black"])
        c.create_text(cx, cy + 14, text="MONTH",
                      font=('JetBrains Mono', 8, 'bold'),
                      fill=t2["muted"])

    def _draw_ranking(self, task_summary, total_sec):
        """彩色进度条排行。"""
        t2 = theme()
        for w in self.rank_frame.winfo_children():
            w.destroy()

        if not task_summary:
            tk.Label(self.rank_frame, text="暂无数据",
                     font=('Microsoft YaHei', 9, 'bold'),
                     bg=t2["white"], fg=t2["muted"]).pack(pady=10)
            return

        sorted_tasks = sorted(task_summary.items(), key=lambda x: x[1], reverse=True)
        max_dur = max(v for _, v in sorted_tasks) if sorted_tasks else 1
        bar_colors = [t2["red"], t2["teal"], t2["yellow"], t2["black"]]

        for idx, (name, dur) in enumerate(sorted_tasks):
            row = tk.Frame(self.rank_frame, bg=t2["white"])
            row.pack(fill='x', pady=3)

            color = bar_colors[idx % len(bar_colors)]

            tk.Label(row, text=name[:14],
                     font=('Microsoft YaHei', 9, 'bold'),
                     bg=t2["white"], fg=t2["black"], anchor='w', width=14
                     ).pack(side='left')

            track = tk.Canvas(row, bg=t2["cream"], highlightthickness=2,
                              highlightbackground=t2["black"], bd=0,
                              height=12, width=140)
            track.pack(side='left', fill='x', expand=True, padx=(6, 6))

            bar_w = max(5, int((dur / max_dur) * 140))
            track.create_rectangle(0, 0, 140, 12, fill=t2["cream"], outline='')
            track.create_rectangle(0, 0, bar_w, 12, fill=color, outline='')

            tk.Label(row, text=self._fmt_duration(dur),
                     font=('JetBrains Mono', 9, 'bold'),
                     bg=t2["white"], fg=t2["black"], anchor='e', width=7
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
        self.win.configure(bg=t["cream"])
        self.win.attributes('-topmost', True)

        self.win.update_idletasks()
        w, h = 420, 400
        _place_centered(self.win, parent, w, h)

        self._create_ui()
        self._refresh_list()

        self.win.bind('<Escape>', lambda e: self.win.destroy())

    def _create_ui(self):
        t = theme()
        bd = tk.Frame(self.win, bg=t["cream"], highlightthickness=4,
                      highlightbackground=t["black"])
        bd.pack(fill='both', expand=True)

        inner = tk.Frame(bd, bg=t["cream"])
        inner.pack(fill='both', expand=True)

        self._scanline_canvas = tk.Canvas(inner, bg=t["cream"], highlightthickness=0, bd=0)
        self._scanline_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._draw_scanline()

        hdr = tk.Frame(inner, bg=t["yellow"])
        hdr.pack(fill='x')

        title_box = tk.Frame(hdr, bg=t["yellow"], padx=12, pady=10)
        title_box.pack(side='left', fill='x', expand=True)
        rv = tk.Canvas(title_box, width=11, height=11, bg=t["yellow"], highlightthickness=0, bd=0)
        rv.pack(side='left', padx=(0, 8))
        rv.create_oval(1, 1, 10, 10, fill=t["black"], outline='')

        tk.Label(title_box, text="任务列表",
                 font=('Microsoft YaHei', 13, 'bold'),
                 bg=t["yellow"], fg=t["black"], anchor='w', cursor='fleur').pack(side='left')

        close_lbl = tk.Label(hdr, text="×", font=('JetBrains Mono', 13, 'bold'),
                             bg=t["red"], fg=t["white"], width=3, cursor='hand2')
        close_lbl.pack(side='right', fill='y')
        close_lbl.bind('<Button-1>', lambda e: self.win.destroy())
        close_lbl.bind('<Enter>', lambda e: close_lbl.config(bg=t["black"], fg=t["yellow"]))
        close_lbl.bind('<Leave>', lambda e: close_lbl.config(bg=t["red"], fg=t["white"]))

        tk.Frame(inner, bg=t["black"], height=4).pack(fill='x')

        list_bg = tk.Frame(inner, bg=t["cream"])
        list_bg.pack(fill='both', expand=True, padx=12, pady=(12, 0))

        self.task_list_canvas = tk.Canvas(list_bg, bg=t["cream"], highlightthickness=0, bd=0)
        self.task_list_scroll = CyberScrollbar(list_bg, self.task_list_canvas.yview)
        self.task_list_frame = tk.Frame(self.task_list_canvas, bg=t["cream"])
        self.task_list_window = self.task_list_canvas.create_window(
            (0, 0), window=self.task_list_frame, anchor='nw', width=376)
        self.task_list_canvas.configure(yscrollcommand=self.task_list_scroll.set)
        self.task_list_frame.bind('<Configure>', self._on_task_list_configure)
        self.task_list_canvas.bind('<Configure>', self._on_task_list_canvas_configure)
        self.task_list_canvas.bind('<MouseWheel>', self._on_task_list_mousewheel)
        self.task_list_frame.bind('<MouseWheel>', self._on_task_list_mousewheel)
        self.task_list_canvas.pack(side='left', fill='both', expand=True)
        self.task_list_scroll.pack(side='right', fill='y', padx=(4, 0))

        add_wrap = tk.Frame(inner, bg=t["cream"], highlightthickness=3,
                            highlightbackground=t["black"])
        add_wrap.pack(fill='x', padx=12, pady=(8, 12))

        add_row = tk.Frame(add_wrap, bg=t["cream"], padx=10, pady=8)
        add_row.pack(fill='x')

        tk.Label(add_row, text=">", font=('JetBrains Mono', 11, 'bold'),
                 bg=t["cream"], fg=t["red"]).pack(side='left')

        self.new_entry = tk.Entry(add_row, font=('Microsoft YaHei', 10, 'bold'),
                                  bg=t["cream"], fg=t["black"],
                                  insertbackground=t["teal"], relief='flat', bd=0)
        self.new_entry.pack(side='left', fill='x', expand=True, padx=(7, 0))
        self.new_entry.insert(0, "输入新任务名")
        self.new_entry.bind('<FocusIn>', self._on_new_entry_focus)
        self.new_entry.bind('<Return>', lambda e: self._add_task())

        _bind_title_drag(self.win, hdr)

    def _draw_scanline(self):
        c = self._scanline_canvas
        c.delete('all')
        cw = c.winfo_width() or 420
        ch = c.winfo_height() or 400
        t2 = theme()
        for x in range(20, cw, 56):
            c.create_oval(x, 18, x + 5, 23, fill=t2["yellow"], outline='')
        for x in range(8, cw, 64):
            c.create_line(x, ch - 34, x + 14, ch - 20, fill=t2["teal"], width=2)

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
            empty = tk.Frame(self.task_list_frame, bg=t["cream"],
                             highlightthickness=3, highlightbackground=t["black"])
            empty.pack(fill='both', expand=True, pady=12)
            tk.Label(empty, text="ADD FIRST TASK", font=('JetBrains Mono', 14, 'bold'),
                     bg=t["cream"], fg=t["red"]).pack(expand=True)
            tk.Label(empty, text="输入任务名后按 ENTER", font=('Microsoft YaHei', 9, 'bold'),
                     bg=t["cream"], fg=t["black"]).pack(pady=(0, 44))
            return

        today_activities = self.db.get_today_activities()
        task_duration = {}
        for act in today_activities:
            task_duration[act.task_id] = task_duration.get(act.task_id, 0) + act.duration

        palette = [t["teal"], t["red"], t["yellow"], t["black"]]
        for idx, task in enumerate(tasks):
            is_current = (self.tracker.current_task
                          and self.tracker.current_task.id == task.id)
            dur = task_duration.get(task.id, 0)
            self._make_task_card(task, is_current, dur, palette[idx % len(palette)])

    def _make_task_card(self, task, is_current, duration, color):
        t = theme()
        card_bg = t["cream"] if is_current else t["white"]
        shadow = tk.Frame(self.task_list_frame, bg=t["black"])
        shadow.pack(fill='x', pady=(0, 10), padx=(5, 1))
        card = tk.Frame(shadow, bg=card_bg,
                        highlightthickness=3, highlightbackground=t["black"])
        card.pack(fill='x', padx=(0, 4), pady=(0, 4))

        row = tk.Frame(card, bg=card_bg, padx=0, pady=0)
        row.pack(fill='x')

        stripe = tk.Canvas(row, width=7, height=58, bg=card_bg,
                           highlightthickness=0, bd=0)
        stripe.pack(side='left', fill='y')
        stripe.create_rectangle(0, 0, 7, 70, fill=t["yellow"] if is_current else color, outline='')
        if is_current:
            stripe.create_rectangle(0, 0, 7, 16, fill=t["red"], outline='')

        info = tk.Frame(row, bg=card_bg, padx=10, pady=8)
        info.pack(side='left', fill='x', expand=True)

        name_lbl = tk.Label(info, text=task.name[:24],
                            font=('Microsoft YaHei', 10, 'bold'),
                            bg=card_bg, fg=t["black"], anchor='w')
        name_lbl.pack(fill='x')
        self.name_labels[task.id] = name_lbl

        pomo_count = duration // max(1, config.get("pomodoro_minutes", 30) * 60)
        meta = "尚未开始" if duration <= 0 else f"{self._fmt_duration(duration)} · {pomo_count} 轮专注"
        tk.Label(info, text=meta,
                 font=('Microsoft YaHei', 8, 'bold'),
                 bg=card_bg, fg=t["muted"], anchor='w').pack(fill='x', pady=(3, 0))

        if is_current:
            tk.Label(row, text="ACTIVE",
                     font=('JetBrains Mono', 8, 'bold'),
                     bg=t["red"], fg=t["white"], padx=6, pady=2).pack(side='left', padx=(0, 8))

        actions = tk.Frame(row, bg=card_bg, padx=8)
        actions.pack(side='right')

        edit_btn = self._make_action_btn(actions, "改", t["black"])
        edit_btn.pack(side='left', padx=(0, 5))
        edit_btn.bind('<Button-1>', lambda e, tsk=task: self._edit_task(tsk, self.name_labels.get(tsk.id)))

        del_btn = self._make_action_btn(actions, "删", t["red"])
        del_btn.pack(side='left')
        del_btn.bind('<Button-1>', lambda e, tsk=task: self._delete_task(tsk))

        for widget in (shadow, card, row, stripe, info, name_lbl, actions, edit_btn, del_btn):
            widget.bind('<MouseWheel>', self._on_task_list_mousewheel, add='+')

    def _make_action_btn(self, parent, text, fg):
        t = theme()
        lbl = tk.Label(parent, text=text,
                       font=('Microsoft YaHei', 9, 'bold'),
                       bg=t["white"], fg=fg,
                       padx=7, pady=3, cursor='hand2',
                       highlightthickness=2, highlightbackground=t["black"])
        lbl.bind('<Enter>', lambda e: lbl.config(bg=t["yellow"], fg=t["black"]))
        lbl.bind('<Leave>', lambda e: lbl.config(bg=t["white"], fg=fg))
        return lbl

    def _edit_task(self, task, label_widget=None):
        t = theme()
        old_name = task.name

        d = tk.Toplevel(self.win)
        d.withdraw()
        d.overrideredirect(True)
        d.configure(bg=t["cream"])
        d.attributes('-topmost', True)

        f = tk.Frame(d, bg=t["cream"], highlightthickness=3,
                     highlightbackground=t["black"], padx=14, pady=12)
        f.pack()

        tk.Label(f, text=f"重命名: {old_name}",
                 font=('Microsoft YaHei', 10, 'bold'),
                 bg=t["cream"], fg=t["black"]).pack(anchor='w', pady=(0, 8))

        e = tk.Entry(f, font=('Microsoft YaHei', 10, 'bold'),
                     bg=t["white"], fg=t["black"],
                     insertbackground=t["teal"], relief='flat', bd=0,
                     highlightthickness=3, highlightbackground=t["black"])
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

        _place_overlay(d, self.win, 260, 80)
        e.focus_set()
        _win_set_topmost(d, True)
        try:
            d.grab_set()
        except Exception:
            pass

    def _delete_task(self, task):
        t = theme()
        d = tk.Toplevel(self.win)
        d.withdraw()
        d.overrideredirect(True)
        d.configure(bg=t["cream"])
        d.attributes('-topmost', True)

        f = tk.Frame(d, bg=t["cream"], highlightthickness=3,
                     highlightbackground=t["black"], padx=16, pady=12)
        f.pack()

        is_current = (self.tracker.current_task
                      and self.tracker.current_task.id == task.id)
        msg = f"删除: {task.name}?"
        if is_current:
            msg += "\n(当前进行中的任务)"

        tk.Label(f, text=msg,
                 font=('Microsoft YaHei', 10, 'bold'),
                 bg=t["cream"], fg=t["black"]).pack(pady=(0, 10))

        btn_row = tk.Frame(f, bg=t["cream"])
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
                           bg=t["red"], fg=t["white"], cursor='hand2',
                           highlightthickness=2, highlightbackground=t["black"], padx=12, pady=4)
        yes_btn.pack(side='left', padx=(0, 10))
        yes_btn.bind('<Button-1>', lambda e: do_delete())
        yes_btn.bind('<Enter>', lambda e: yes_btn.config(bg=t["black"], fg=t["yellow"]))
        yes_btn.bind('<Leave>', lambda e: yes_btn.config(bg=t["red"], fg=t["white"]))

        no_btn = tk.Label(btn_row, text="取消",
                          font=('Microsoft YaHei', 9, 'bold'),
                          bg=t["white"], fg=t["black"], cursor='hand2',
                          highlightthickness=2, highlightbackground=t["black"], padx=12, pady=4)
        no_btn.pack(side='left')
        no_btn.bind('<Button-1>', lambda e: d.destroy())
        no_btn.bind('<Enter>', lambda e: no_btn.config(bg=t["yellow"], fg=t["black"]))
        no_btn.bind('<Leave>', lambda e: no_btn.config(bg=t["white"], fg=t["black"]))

        d.bind('<Escape>', lambda e: d.destroy())

        _place_overlay(d, self.win, 220, 90)
        _win_set_topmost(d, True)
        try:
            d.grab_set()
        except Exception:
            pass

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
    """W / TRACE 主窗口 — Memphis 风格计时面板。"""

    WIDTH = 340
    HEIGHT = 260

    def __init__(self, tracker, silent=False):
        self.tracker = tracker
        self.root = tk.Tk()
        self.root.title("W / TRACE")
        try:
            ico = _asset('worktrace.ico')
            if os.path.exists(ico):
                self.root.iconbitmap(ico)
        except Exception:
            pass
        self.root.overrideredirect(True)

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

        self._menu_window = None
        self._child_windows = []
        self._colon_visible = True
        self._last_completed = 0
        self._tick_pulse_phase = 0
        self._badge_pulse_phase = 0
        self._tick_pulse_dir = 1
        self._badge_pulse_dir = 1
        self._cached_state = None
        # 吸边隐藏状态机
        self._edge_state = 'free'   # 'free' 自由常显 | 'hidden' 吸边隐藏 | 'shown' 唤出
        self._snap_edge = None      # 'left' | 'right'
        self._reveal_strip = None   # 露出条 Toplevel
        self._reveal_canvas = None
        self._strip_w = 6           # 露出条宽度(px)
        self._hidden_geo = None     # (x, y, w, h)，唤出/隐藏时恢复用

        t = self.theme()
        self.root.configure(bg=t["cream"])
        self._build_ui()
        self._bind_drag()

        self._update_loop()
        self._colon_blink_loop()
        self._pulse_loop()

        if silent:
            self.root.withdraw()
        else:
            self.show_window()

        if not self.tracker.today_tasks and self.tracker.running:
            self.root.after(800, self._switch_task)

        # 吸边模式：唤出后鼠标离开窗口即收起；启动时按当前模式设置置顶层级
        self.root.bind('<Leave>', self._on_root_leave, add='+')
        self._apply_window_level()

    def theme(self):
        return {
            "red": "#FF6B6B",
            "teal": "#4ECDC4",
            "yellow": "#FFE66D",
            "black": "#000000",
            "white": "#FFFFFF",
            "cream": "#FFF8DC",
            "muted": "#666666"
        }

    def show_window(self):
        # 吸边隐藏态下先恢复成自由常显，避免唤起后仍是露出条
        if config.get("edge_hide", False) and self._edge_state != 'free':
            try:
                self._hide_reveal_strip()
                self._edge_state = 'free'
                self._snap_edge = None
            except Exception:
                pass
        self.root.deiconify()
        self.root.lift()
        # 一次性提到前台（闪一下 topmost 再取消，不常驻置顶）
        try:
            _win_set_topmost(self.root, True)
            self.root.after(180, lambda: _win_set_topmost(self.root, False))
        except Exception:
            pass
        # 已打开的子窗口(菜单/弹窗)重新提到最上，避免被唤起的主窗盖住
        try:
            if self._menu_window and self._menu_window.winfo_exists():
                _win_set_topmost(self._menu_window, True)
        except Exception:
            pass

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

    # ---------- 吸边隐藏 ----------
    def _apply_window_level(self):
        """主窗口不再使用置顶功能。吸边唤出态仅 lift 抬到前面，不抢层级。"""
        try:
            if config.get("edge_hide", False) and self._edge_state == 'shown':
                self.root.lift()
        except Exception:
            pass

    def _on_drag_drop(self):
        """拖拽松手回调：保存位置，并按吸边开关决定进入隐藏/自由态。"""
        self._save_position()
        if not config.get("edge_hide", False):
            self._apply_window_level()
            return
        # 跟随窗口当前所在显示器判定贴边（多屏）
        mon = _monitor_rect(self.root)
        try:
            ww = self.root.winfo_width()
            x = self.root.winfo_x()
        except Exception:
            return
        if mon:
            mx, _, mw, _ = mon
        else:
            mx, mw = 0, self.root.winfo_screenwidth()
        self._snap_mon = mon
        if x <= mx + 2:
            self._snap_edge = 'left'
            self._enter_hidden()
        elif x >= mx + mw - ww - 2:
            self._snap_edge = 'right'
            self._enter_hidden()
        else:
            self._enter_free()

    def _enter_free(self):
        self._edge_state = 'free'
        self._snap_edge = None
        self._hide_reveal_strip()
        try:
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass
        self._apply_window_level()

    def _enter_hidden(self):
        try:
            self._hidden_geo = (self.root.winfo_x(), self.root.winfo_y(),
                                self.root.winfo_width(), self.root.winfo_height())
        except Exception:
            self._hidden_geo = None
        self._edge_state = 'hidden'
        self.root.withdraw()
        self._show_reveal_strip()

    def _show_reveal_strip(self):
        mon = getattr(self, '_snap_mon', None) or _monitor_rect(self.root)
        if mon:
            mx, _, mw, _ = mon
        else:
            mx, mw = 0, self.root.winfo_screenwidth()
        if self._hidden_geo:
            _, gy, _, gh = self._hidden_geo
        else:
            gy, gh = 80, self.HEIGHT
        sx = mx if self._snap_edge == 'left' else mx + mw - self._strip_w
        if self._reveal_strip is None or not self._reveal_strip.winfo_exists():
            strip = tk.Toplevel(self.root)
            strip.overrideredirect(True)
            strip.attributes('-topmost', True)
            cv = tk.Canvas(strip, width=self._strip_w, height=gh,
                           highlightthickness=0, bd=0)
            cv.pack(fill='both', expand=True)
            strip.bind('<Enter>', lambda e: self._reveal())
            cv.bind('<Enter>', lambda e: self._reveal())
            self._reveal_strip = strip
            self._reveal_canvas = cv
        self._reveal_strip.geometry(f'{self._strip_w}x{gh}+{sx}+{gy}')
        self._reveal_strip.deiconify()
        self._refresh_reveal_strip()

    def _refresh_reveal_strip(self):
        """露出条按高度从下往上填充当前任务进度。"""
        if self._edge_state != 'hidden' or self._reveal_strip is None:
            return
        try:
            if not self._reveal_strip.winfo_exists():
                return
            t = self.theme()
            cv = self._reveal_canvas
            h = self._reveal_strip.winfo_height() or self.HEIGHT
            w = self._strip_w
            _, progress = self._compute_tick_state()
            progress = max(0.0, min(1.0, progress))
            cv.delete('all')
            # 与计时页刻度同义：未完成=淡黄(cream #FFF8DC)底，已完成=青绿（从下往上填充）
            cv.configure(bg=t["cream"])
            cv.create_rectangle(0, 0, w, h, fill=t["cream"], outline=t["black"])
            fill_h = int(h * progress)
            if fill_h > 0:
                cv.create_rectangle(0, h - fill_h, w, h, fill=t["teal"], outline='')
        except Exception:
            pass

    def _reveal(self):
        if self._edge_state == 'shown':
            return
        mon = getattr(self, '_snap_mon', None) or _monitor_rect(self.root)
        if mon:
            mx, _, mw, _ = mon
        else:
            mx, mw = 0, self.root.winfo_screenwidth()
        if self._hidden_geo:
            _, gy, gw, gh = self._hidden_geo
        else:
            gy, gw, gh = 80, self.WIDTH, self.HEIGHT
        x = mx if self._snap_edge == 'left' else mx + mw - gw
        self._edge_state = 'shown'
        self._hide_reveal_strip()
        try:
            self.root.deiconify()
            self.root.geometry(f'{gw}x{gh}+{x}+{gy}')
            self.root.lift()
        except Exception:
            pass

    def _on_root_leave(self, event=None):
        if self._edge_state != 'shown' or not config.get("edge_hide", False):
            return
        # 左键按住=正在拖动，不收起
        if event is not None and (event.state & 0x0100):
            return
        # 鼠标移到子控件也会触发 <Leave>，用指针坐标判断是否真的离开窗口
        try:
            px, py = self.root.winfo_pointerx(), self.root.winfo_pointery()
            x0, y0 = self.root.winfo_rootx(), self.root.winfo_rooty()
            x1 = x0 + self.root.winfo_width()
            y1 = y0 + self.root.winfo_height()
            if x0 <= px <= x1 and y0 <= py <= y1:
                return
        except Exception:
            pass
        self._collapse()

    def _collapse(self):
        if self._edge_state != 'shown':
            return
        self._edge_state = 'hidden'
        self.root.withdraw()
        self._show_reveal_strip()

    def _hide_reveal_strip(self):
        if self._reveal_strip is not None:
            try:
                self._reveal_strip.withdraw()
            except Exception:
                pass

    def _build_ui(self):
        t = self.theme()
        self.panel = tk.Frame(self.root, bg=t["black"], highlightthickness=0)
        self.panel.pack(fill='both', expand=True)

        self.canvas = tk.Canvas(self.panel, width=self.WIDTH, height=self.HEIGHT,
                                bg=t["black"], highlightthickness=0, bd=0)
        self.canvas.pack(fill='both', expand=True)

        self.canvas.config(cursor='fleur')
        self.canvas.bind('<Button-1>', self._handle_canvas_click, add='+')
        self._render_static()

    def _handle_canvas_click(self, event):
        if event.x >= self.WIDTH - 48 and 12 <= event.y <= 34:
            self._toggle_menu(event)
            return "break"
        return None

    def _draw_pattern(self):
        return

    def _render_static(self):
        t = self.theme()
        c = self.canvas
        c.delete('dynamic')
        c.configure(bg=t["black"])

        c.create_rectangle(0, 0, self.WIDTH, self.HEIGHT, fill=t["black"], outline='', tags='dynamic')
        c.create_rectangle(4, 4, self.WIDTH - 5, 40, fill=t["teal"], outline='', tags='dynamic')
        c.create_rectangle(4, 41, self.WIDTH - 5, 43, fill=t["black"], outline='', tags='dynamic')
        c.create_rectangle(4, 44, self.WIDTH - 5, 223, fill=t["white"], outline='', tags='dynamic')
        c.create_rectangle(4, 224, self.WIDTH - 5, 226, fill=t["black"], outline='', tags='dynamic')
        c.create_rectangle(4, 227, self.WIDTH - 5, self.HEIGHT - 5, fill=t["cream"], outline='', tags='dynamic')

        c.create_text(20, 23, text="W / TRACE", anchor='w', fill=t["black"],
                      font=('JetBrains Mono', 14, 'bold'), tags='dynamic')
        for x, color in ((298, t["black"]), (307, t["black"]), (316, t["red"])):
            c.create_rectangle(x, 20, x + 5, 25, fill=color, outline='', tags=('dynamic', 'menu_dots'))
        c.tag_bind('menu_dots', '<Enter>', lambda e: c.config(cursor='hand2'))
        c.tag_bind('menu_dots', '<Leave>', lambda e: c.config(cursor='fleur'))

        status_text, status_color = self._memphis_status_text()
        c.create_text(22, 82, text=">", anchor='w', fill=t["red"],
                      font=('JetBrains Mono', 12, 'bold'), tags='dynamic')
        if self.tracker.current_task:
            status_text = f"{status_text} · {self.tracker.current_task.name}"
        c.create_text(39, 82, text=status_text, anchor='w', fill=status_color,
                      font=('Microsoft YaHei', 12, 'bold'), tags='dynamic')

        self._render_ticks()

        time_str = self._compute_time_str()
        c.create_text(22, 176, text=time_str, anchor='w',
                      fill="#999999",
                      font=('JetBrains Mono', 14, 'bold'), tags=('dynamic', 'time'))

        self._render_badge()

        sessions = self._compute_session_count()
        focused_min = self._compute_focused_minutes()
        state, drift_min = self._compute_badge_state()
        completed, progress = self._compute_tick_state()
        c.create_text(24, 242, text=f"第 {sessions:02d} 轮 · 专注 {focused_min} 分钟",
                      anchor='w', fill=t["black"],
                      font=('JetBrains Mono', 11, 'bold'), tags='dynamic')
        c.create_text(22, 196, text=f"本轮进度 {int(progress * 100):02d}% · 偏离 {drift_min:02d}分",
                      anchor='w', fill=t["red"],
                      font=('Microsoft YaHei', 9, 'bold'), tags='dynamic')

    def _render_ticks(self):
        t = self.theme()
        c = self.canvas
        completed, progress = self._compute_tick_state()
        pulse = self._tick_pulse_phase
        total = 18
        current = max(0, min(total - 1, int(completed / 30 * total)))
        filled = current
        y0 = 110
        h = 36
        x0 = 20
        x1 = 320
        gap = 4
        normal_w = (x1 - x0 - gap * (total - 1)) / total
        current_w = normal_w
        x = x0
        ease = 0.5 - 0.5 * math.cos(math.pi * pulse)
        cursor_color = self._blend(t["white"], t["yellow"], 0.55 + 0.45 * ease)
        for i in range(total):
            is_current = i == current
            w = current_w if is_current else normal_w
            if i < filled:
                c.create_rectangle(x, y0, x + w, y0 + h, fill=t["black"], outline='', tags='dynamic')
                c.create_rectangle(x + 2, y0 + 2, x + w - 2, y0 + h - 2,
                                   fill=t["teal"], outline='', tags='dynamic')
            elif is_current:
                c.create_rectangle(x, y0, x + w, y0 + h, fill=cursor_color, outline='', tags=('dynamic', 'cursor_cell'))
            else:
                c.create_rectangle(x, y0 + 3, x + w, y0 + h - 3,
                                   fill=self._blend(t["white"], t["black"], 0.33), outline='', tags='dynamic')
                c.create_rectangle(x + 2, y0 + 5, x + w - 2, y0 + h - 5,
                                   fill=t["cream"], outline='', tags='dynamic')
            x += w + gap

    def _memphis_status_text(self):
        t = self.theme()
        state, drift_min = self._compute_badge_state()
        if state == 'on':
            return "专注中", t["teal"]
        if state == 'drift':
            return f"偏离 {drift_min:02d}分", t["yellow"]
        if state == 'paused':
            return "暂停", t["red"]
        return "空闲", t["black"]

    def _render_badge(self):
        t = self.theme()
        c = self.canvas
        badge_text = "空闲"
        badge_bg = t["white"]
        badge_fg = t["black"]
        state, drift_min = self._compute_badge_state()
        if state == 'on':
            badge_text = "专注中"
            badge_bg = t["white"]
        elif state == 'drift':
            badge_text = f"偏离 {drift_min:02d}分"
            badge_bg = t["yellow"]
        elif state == 'paused':
            badge_text = "暂停"
            badge_bg = t["red"]
            badge_fg = t["white"]

        x0 = 260
        y0 = 162
        x1 = 319
        y1 = 190
        c.create_rectangle(x0, y0, x1, y1, fill=t["black"], outline='', tags='dynamic')
        c.create_rectangle(x0 + 2, y0 + 2, x1 - 2, y1 - 2, fill=badge_bg, outline='', tags='dynamic')
        c.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=badge_text, fill=badge_fg,
                      font=('Microsoft YaHei', 10, 'bold'), tags='dynamic')

    def _bind_drag(self):
        _bind_full_drag(self.root, self.root, self.panel, on_snap_save=self._on_drag_drop)
        _bind_full_drag(self.root, self.root, self.canvas, on_snap_save=self._on_drag_drop)

    def _toggle_menu(self, event=None):
        if self._menu_window is not None and self._menu_window.winfo_exists():
            self._close_menu()
            return
        self._show_menu()

    def _show_menu(self):
        t = self.theme()
        m = tk.Toplevel(self.root)
        m.withdraw()
        m.overrideredirect(True)
        m.configure(bg=t["white"])

        bd = tk.Frame(m, bg=t["white"], highlightthickness=3, highlightbackground=t["black"])
        bd.pack(fill='both', expand=True)

        items = [
            ("切换任务", self._switch_task),
            None,
            ("今日复盘", self._generate_today_review),
            ("统计", self._open_stats),
            ("任务列表", self._open_task_manager),
            None,
            ("设置", self._open_settings),
            None,
            ("退出", self._do_quit),
        ]

        row_h = 34
        for it in items:
            if it is None:
                sep = tk.Frame(bd, bg=t["black"], height=3)
                sep.pack(fill='x', padx=10, pady=4)
                continue
            label, cb = it
            lab = tk.Label(bd, text=label, font=('Microsoft YaHei', 9, 'bold'),
                           bg=t["white"], fg=t["black"], anchor='w', padx=14, pady=6,
                           relief='flat', cursor='hand2')
            lab.pack(fill='x')

            def on_enter(e, l=lab):
                l.configure(bg=t["yellow"], fg=t["black"])

            def on_leave(e, l=lab):
                l.configure(bg=t["white"], fg=t["black"])

            lab.bind('<Enter>', on_enter)
            lab.bind('<Leave>', on_leave)
            lab.bind('<Button-1>', lambda e, c=cb: self._menu_action(c))

        self.root.update_idletasks()
        m.update_idletasks()
        # 用 canvas 的绝对屏幕坐标定位（overrideredirect 主窗 winfo_x 在多屏下不可靠），
        # 让下拉菜单出现在右上角三个点正下方
        try:
            cx = self.canvas.winfo_rootx()
            cy = self.canvas.winfo_rooty()
        except Exception:
            cx = self.root.winfo_x()
            cy = self.root.winfo_y()
        rx = cx + self.WIDTH - 146
        ry = cy + 38
        total_h = bd.winfo_reqheight() + 8
        vs = _virtual_screen()
        if vs:
            vx, vy, vw, vh = vs
            rx = max(vx, min(rx, vx + vw - 146))
            ry = max(vy, min(ry, vy + vh - total_h))
        m.geometry(f'146x{total_h}+{rx}+{ry}')
        m.deiconify()
        m.update_idletasks()
        # overrideredirect 窗口首次映射常忽略位置落到 (0,0)，映射后再设一次才稳
        m.geometry(f'146x{total_h}+{rx}+{ry}')

        self._menu_window = m
        m.bind('<FocusOut>', lambda e: self._close_menu())
        m.focus_set()
        _win_set_topmost(m, True)

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

    def _toggle_theme(self):
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
        if not config.get("edge_hide", False) and self._edge_state != 'free':
            self._enter_free()
        else:
            self._apply_window_level()
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

    def _compute_tick_state(self):
        unit_sec = max(1, config.get("pomodoro_minutes", 30)) * 60
        if self.tracker.running and self.tracker.current_task:
            cur_seconds = self.tracker.get_current_task_seconds()
            in_unit = cur_seconds - (cur_seconds // unit_sec) * unit_sec
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
            drift_sec = int(time.time() - self.tracker.deviation_start_time) if self.tracker.deviation_start_time else 0
            return 'drift', max(1, drift_sec // 60)
        return 'on', 0

    def _compute_session_count(self):
        return self.tracker.get_today_completed_pomodoros() + 1

    def _compute_focused_minutes(self):
        try:
            activities = self.tracker.db.get_today_activities()
            sec = 0
            for act in activities:
                if not act.is_idle and not act.is_locked and not self.tracker._is_non_work_task(act.task_name):
                    sec += act.duration
            if self.tracker.current_activity and not self.tracker.current_activity.is_idle \
                    and not self.tracker.current_activity.is_locked \
                    and not self.tracker._is_non_work_task(self.tracker.current_activity.task_name):
                from datetime import datetime as _dt
                start = _dt.fromisoformat(self.tracker.current_activity.start_time)
                sec += int((_dt.now() - start).total_seconds())
            return sec // 60
        except Exception:
            return 0

    def _update_loop(self):
        try:
            self._render_static()
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
            if self._edge_state == 'hidden':
                self._refresh_reveal_strip()
        except Exception as e:
            print(f"刷新错误: {e}")
        self.root.after(1000, self._update_loop)

    def _colon_blink_loop(self):
        self._colon_visible = not self._colon_visible
        try:
            self.canvas.itemconfigure('time', text=self._compute_time_str())
        except Exception:
            pass
        self.root.after(1000, self._colon_blink_loop)

    def _pulse_loop(self):
        try:
            self._tick_pulse_phase += 0.033 * self._tick_pulse_dir
            if self._tick_pulse_phase >= 1.0:
                self._tick_pulse_phase = 1.0
                self._tick_pulse_dir = -1
            elif self._tick_pulse_phase <= 0.0:
                self._tick_pulse_phase = 0.0
                self._tick_pulse_dir = 1
            self._update_cursor_color()
        except Exception:
            pass
        self.root.after(33, self._pulse_loop)

    def _update_cursor_color(self):
        t = self.theme()
        ease = 0.5 - 0.5 * math.cos(math.pi * self._tick_pulse_phase)
        color = self._blend(t["white"], t["yellow"], 0.55 + 0.45 * ease)
        try:
            self.canvas.itemconfigure('cursor_cell', fill=color)
        except Exception:
            pass

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
        # AI 内容感知偏离判定
        self.ai_enabled = config.get("ai_enabled", True)
        self.body_send = config.get("body_send", True)
        self.ark = ArkClient(config)
        self._last_relation = 'related'        # 最近一次判定结果（供 UI/日志）

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
        # 休息/娱乐放最后一项（idx>=len(task_ids)，自动不带改/删按钮，也不会成为默认选中行）
        options.append("休息/娱乐")
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

                # 任务偏离提醒：内容感知判定，只有 drift 才计入偏离计时
                if self.current_task and self.current_activity and self.running:
                    relation = self._classify_context(window_info)
                    if relation == 'drift':
                        self.is_deviating = True
                        if self.deviation_start_time == 0:
                            self.deviation_start_time = time.time()
                        elif time.time() - self.deviation_start_time >= self.reminder_interval:
                            # 偏离持续超过容忍阈值，弹窗确认
                            self.deviation_start_time = 0
                            self.last_reminder_time = time.time()
                            self._ask_task_confirmation(window_info)
                    else:
                        # related 或 maybe_drift：视为在容忍范围内，重置偏离计时
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

        # 有当前任务时，窗口变化只更新活动记录的窗口信息；
        # 是否偏离、何时弹窗，统一交给 _track_loop 里带「偏离容忍」计时的判定，
        # 不在此处立即弹窗（否则一换应用就弹，绕过了容忍时长）。
        if self.current_activity:
            self.current_activity.app_name = window_info['app']
            self.current_activity.window_title = window_info['title']
            self.current_activity.url = window_info.get('url', '')
    
    def _classify_context(self, window_info: Dict[str, str]) -> str:
        """内容感知偏离判定，返回 'related' | 'maybe_drift' | 'drift'。
        流程：抓正文/网址 → 火山 AI 判定；AI 关闭或失败时降级为进程名启发式。"""
        # 降级路径：AI 关闭或不可用
        if not self.ai_enabled or not self.ark.available():
            return 'related' if self._is_same_task_context(window_info) else 'drift'

        # 抓取当前窗口内容（网址 + 正文摘要），失败返回空、不抛异常
        url, body = '', ''
        try:
            hwnd = win32gui.GetForegroundWindow() if HAS_WIN32 else 0
            content = ContentReader.read(hwnd, window_info.get('app', ''),
                                         want_body=self.body_send)
            url = content.get('url', '')
            body = content.get('body', '')
            window_info['url'] = url  # 顺带回填活动记录用的 url 字段
        except Exception as e:
            print(f"[偏离判定] 内容抓取异常: {e}")

        keywords = getattr(self.current_task, 'keywords', '') or ''
        title = window_info.get('title', '')
        self._last_grab = {'title': title, 'body': body, 'url': url}

        # 本地关键词预筛：命中足够多高权重关键词直接判 related，跳过 AI（省 token/延迟）
        if self._keyword_prefilter(title, url, body) == 'related':
            self._last_relation = 'related'
            self._learn_keywords(title, body)
            return 'related'

        res = self.ark.classify(
            self.current_task.name, keywords,
            window_info.get('app', ''), title,
            url, body)
        if res is None:
            # AI 失败：降级到进程名启发式，避免误弹
            return 'related' if self._is_same_task_context(window_info) else 'maybe_drift'

        relation, reason = res
        self._last_relation = relation
        if relation == 'drift':
            print(f"[偏离判定] drift: {reason}")
        else:
            # related / maybe_drift 视为在做，强化关键词画像
            self._learn_keywords(title, body)
        return relation

    def _keyword_prefilter(self, title, url, body):
        """内容命中当前任务 >=2 个已学习关键词时判定 related，否则返回 None 交给 AI。
        只做正向短路（related），绝不本地判 drift，避免误弹。"""
        kw = (getattr(self.current_task, 'keywords', '') or '')
        kws = [k for k in kw.split(',') if k]
        if len(kws) < 2:
            return None
        hay = f"{title} {url} {body}".lower()
        hits = sum(1 for k in kws if k.lower() in hay)
        return 'related' if hits >= 2 else None

    def _learn_keywords(self, title, body):
        """内容被确认在做时，从标题+正文摘要抽词累加权重；周期性回写 keywords 字段。"""
        if not self.current_task:
            return
        try:
            terms = extract_terms(f"{title} {(body or '')[:200]}", limit=60)
            if not terms:
                return
            self.db.bump_keywords(self.current_task.id, terms)
            self._learn_count = getattr(self, '_learn_count', 0) + 1
            if self._learn_count % 5 == 0:
                kw = self.db.sync_task_keywords_field(self.current_task.id)
                self.current_task.keywords = kw
        except Exception as e:
            print(f"[关键词学习] {e}")

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
                # 休息/娱乐放最后一项（idx>=len(task_ids)，自动不带改/删按钮）
                options.append("休息/娱乐")
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
                ]
                # 休息状态下「仍在做：休息/娱乐」已覆盖，不再重复追加「休息/娱乐」
                if self.current_task.id != 'rest':
                    options.append("休息/娱乐")

                dialog = ModernDialog(
                    self.panel.root,
                    "任务确认",
                    f"你切换到了：\n{window_info['app']} - {window_info['title']}\n\n还在做'{self.current_task.name}'吗？",
                    options
                )
                result = dialog.show()

                if result and result.startswith("✅"):
                    # 用户确认仍在做：强信号，强化当前内容的关键词画像
                    grab = getattr(self, '_last_grab', None)
                    if grab:
                        self._learn_keywords(grab.get('title', ''), grab.get('body', ''))
                    self._end_activity()
                    self._start_activity(self.current_task, window_info)
                elif result and result.startswith("🔄"):
                    self._end_activity()
                    self._dialog_active = False
                    self._ask_for_task(window_info)
                    return
                elif result == "休息/娱乐" or result == "REST":
                    self._end_activity()
                    self.current_task = Task(id='rest', name='休息/娱乐')
                    self._start_activity(self.current_task, window_info)
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

        if result == "REST" or result == "休息/娱乐":
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
