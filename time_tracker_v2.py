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
if getattr(sys, 'frozen', False):
    # PyInstaller 打包后：exe 同目录存用户数据（config/db/log），_MEIPASS 存打包资源（icons）
    SCRIPT_DIR = os.path.dirname(sys.executable)
    _BUNDLE_DIR = sys._MEIPASS
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    _BUNDLE_DIR = SCRIPT_DIR
DB_PATH = os.path.join(SCRIPT_DIR, "time_tracker.db")
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

# ========== DPI 缩放 ==========
# 全局 UI 缩放系数。1.0 = 100%(96 DPI，界面像素值的设计基准)。
# 混合 DPI 多屏下按窗口所在显示器的真实 DPI 计算，运行时可变。
_SCALE = 1.0


def _enable_dpi_awareness():
    """在创建任何 Tk 窗口之前声明进程 DPI 感知(Per-Monitor V2)。
    否则 Windows 会对本进程做整体位图拉伸，跨不同 DPI 屏就会发虚/错乱。
    逐级降级：Per-Monitor V2 → Per-Monitor → System。"""
    if sys.platform != 'win32':
        return
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4 (Win10 1703+)
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_dpi_awareness()


def _S(v):
    """把设计基准(96 DPI)下的像素值按当前 UI 缩放系数换算成物理像素。"""
    try:
        return int(round(v * _SCALE))
    except Exception:
        return v


def _dpi_scale_for_point(x, y):
    """返回坐标 (x, y) 所在显示器的 DPI 缩放系数(如 225% → 2.25)。失败返回 1.0。"""
    if sys.platform != 'win32':
        return 1.0
    try:
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        user32 = ctypes.windll.user32
        shcore = ctypes.windll.shcore
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        user32.MonitorFromPoint.argtypes = [POINT, ctypes.c_uint]
        MONITOR_DEFAULTTONEAREST = 2
        hmon = user32.MonitorFromPoint(POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
        dpi_x = ctypes.c_uint()
        dpi_y = ctypes.c_uint()
        MDT_EFFECTIVE_DPI = 0
        shcore.GetDpiForMonitor(ctypes.c_void_p(hmon), MDT_EFFECTIVE_DPI,
                                ctypes.byref(dpi_x), ctypes.byref(dpi_y))
        if dpi_x.value > 0:
            return dpi_x.value / 96.0
    except Exception:
        pass
    return 1.0


def _apply_ui_scale(root, x, y):
    """按坐标 (x, y) 所在屏 DPI 设定全局缩放系数，并让 Tk 缩放点号字体。
    返回本次生效的缩放系数。"""
    global _SCALE
    s = _dpi_scale_for_point(x, y)
    _SCALE = s if s and s > 0 else 1.0
    try:
        # tk scaling = 每点像素数 = DPI / 72 = _SCALE * 96 / 72
        root.tk.call('tk', 'scaling', _SCALE * 96.0 / 72.0)
    except Exception:
        pass
    return _SCALE


class ScaledCanvas(tk.Canvas):
    """自动按 _SCALE 缩放的 Canvas：构造时缩放 width/height，绘制时缩放所有
    坐标与 width/height 选项。这样画布内所有 create_* 的像素坐标无需逐行改。"""

    def __init__(self, master=None, **kw):
        for key in ('width', 'height'):
            if key in kw and isinstance(kw[key], (int, float)):
                kw[key] = _S(kw[key])
        super().__init__(master, **kw)

    @staticmethod
    def _scale_coord(v):
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return _S(v)
        if isinstance(v, (list, tuple)):
            return type(v)(ScaledCanvas._scale_coord(i) for i in v)
        return v

    def _create(self, itemType, args, kw):
        if args and isinstance(args[-1], dict):
            coords = args[:-1]
            cnf = args[-1]
        else:
            coords = args
            cnf = None
        coords = tuple(ScaledCanvas._scale_coord(c) for c in coords)
        for d in (kw, cnf):
            if d:
                for key in ('width', 'height'):
                    if key in d and isinstance(d[key], (int, float)) and not isinstance(d[key], bool):
                        d[key] = _S(d[key])
        args = coords + ((cnf,) if cnf is not None else ())
        return super()._create(itemType, args, kw)

# 修复 Windows 控制台编码，并兼容 pythonw.exe 无控制台启动
if sys.platform == 'win32':
    log_path = os.path.join(SCRIPT_DIR, "worktrace_start.log")
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
    "pomodoro_minutes": 30,         # 单个时段时长（分钟）
    "pomodoro_sound": True,         # 完成时段时是否播放提示音
    "rest_per_pomodoro": 5,         # 每个时段对应的建议休息分钟数
    "no_input_threshold": 180,      # 实际无操作判定阈值（秒）
    "auto_end_threshold": 3600,     # 自动结束阈值（秒），连续无操作超过该时间视为今日工作结束
    "auto_start_new_day": False,   # 跨日后检测到操作时自动进入工作态（不弹任务选择）
    "window_x": -1,                 # 窗口位置 X（-1 表示自动）
    "window_y": -1,                 # 窗口位置 Y
    "edge_hide": False,             # 吸边隐藏：拖到屏幕左右边缘自动隐藏成露出条，移到露出条唤出
    "theme": "dark",                # 主题：dark / light
    # AI 内容感知偏离判定
    "ai_enabled": True,             # 是否启用 AI 内容判定（关闭则退回进程名兜底）
    "body_send": True,              # 是否把正文摘要外发给 AI（关闭则只发标题+网址）
    "ark_api_key": "",              # 邀请码（中转服务鉴权用）-- 留空，真实邀请码写入本地 config.json
    "ark_endpoint": "http://localhost:8000/api/v3/chat/completions",  # 中转服务地址
    "ark_model": "ep-20260604101325-f2wcq",
    "invite_prompted": False       # 是否已弹过首次邀请码引导（只弹一次，之后用户去设置里改）
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
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        if enabled:
            if getattr(sys, 'frozen', False):
                # 打包后：直接用 exe 路径
                launch_cmd = f'"{sys.executable}"'
            else:
                # 源码模式：pythonw + script
                script_path = os.path.join(SCRIPT_DIR, "time_tracker_v2.py")
                pythonw_path = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                if not os.path.exists(pythonw_path):
                    pythonw_path = sys.executable
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
    """资源绝对路径。源码模式下用 SCRIPT_DIR/icons，PyInstaller 打包后用 _MEIPASS/icons。"""
    return os.path.join(_BUNDLE_DIR, 'icons', name)


@dataclass
class Task:
    """任务定义"""
    id: str
    name: str
    color: str = "#4A90E2"
    created_at: str = ""
    keywords: str = ""   # 逗号分隔的关键词画像（自动学习累积）
    description: str = ""  # 任务描述（可选，用户填写。给 AI 判定作上下文）
    
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

        # 迁移：为老库的 tasks 表补 keywords / description 列
        cols = [r[1] for r in cursor.execute("PRAGMA table_info(tasks)").fetchall()]
        if 'keywords' not in cols:
            cursor.execute("ALTER TABLE tasks ADD COLUMN keywords TEXT DEFAULT ''")
        if 'description' not in cols:
            cursor.execute("ALTER TABLE tasks ADD COLUMN description TEXT DEFAULT ''")

        conn.commit()
        conn.close()
    
    def save_task(self, task: Task):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO tasks (id, name, color, created_at, is_active, keywords, description)
            VALUES (?, ?, ?, ?, 1, ?, ?)
        ''', (task.id, task.name, task.color, task.created_at or datetime.now().isoformat(),
              getattr(task, 'keywords', '') or '',
              getattr(task, 'description', '') or ''))
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

    def update_task_description(self, task_id: str, description: str):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute('UPDATE tasks SET description = ? WHERE id = ?', (description or '', task_id))
        conn.commit()
        conn.close()

    def delete_task_keyword(self, task_id: str, term: str):
        """从权重表删掉单个关键词，并回写 tasks.keywords。"""
        if not task_id or not term:
            return
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM task_keywords WHERE task_id = ? AND term = ?",
                       (task_id, term))
        conn.commit()
        conn.close()
        self.sync_task_keywords_field(task_id)
    
    def get_tasks(self) -> List[Task]:
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, color, created_at, COALESCE(keywords, ''), COALESCE(description, '') "
                       "FROM tasks WHERE is_active = 1 ORDER BY created_at")
        tasks = [Task(id=r[0], name=r[1], color=r[2], created_at=r[3], keywords=r[4], description=r[5])
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

    def classify(self, task_name, keywords, app, title, url, body, description=''):
        if not self.available():
            return None
        key = f"{task_name}|{app}|{url}|{title}|{(body or '')[:80]}"
        now = time.time()
        hit = self._cache.get(key)
        if hit and now - hit[0] < self._cache_ttl:
            return (hit[1], hit[2])

        sysmsg = (
            "你是工作偏离判定器。判断用户当前浏览的内容是否在做他声明的任务。\n"
            "三类定义：\n"
            "- related：内容和任务直接相关，或明显是完成任务所需的辅助信息。"
            "包括：查资料、看规范/文档、看竞品、和同事讨论该任务、参考同方向的教程/案例。\n"
            "- drift：内容和该任务无关。哪怕是工作性质但与当前任务无关，也算 drift。"
            "娱乐、社交、私事一律算 drift。\n"
            "- maybe_drift：仅用于\"无法确定\"。信息不足、内容模糊、抓不到正文等情况归此。\n"
            "判定倾向：宁漏勿误。用户不想被打扰，拿不准时归 maybe_drift，不要归 drift。\n"
            "只输出严格 JSON：{\"relation\":\"related|maybe_drift|drift\",\"reason\":\"一句话中文\"}。"
            "不要输出多余内容。"
        )
        parts = [f"任务: {task_name}"]
        if description:
            parts.append(f"任务描述: {description}")
        if keywords:
            parts.append(f"已学习关键词: {keywords}")
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
                         "X-Invite-Code": self.api_key},
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
    SNAP = _S(20)

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
    SNAP = _S(20)

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
        w = _S(480)
        row_h = _S(47)
        max_visible_rows = 8
        visible_rows = min(max(len(self.options), 3), max_visible_rows)
        self._options_area_h = max(_S(111), visible_rows * row_h)
        self._needs_option_scroll = len(self.options) > max_visible_rows
        self._dialog_w = w
        self._dialog_max_h = sh - _S(80)
        self._dialog_parent = parent
        _place_centered(self.root, parent, w, _S(420))

        self._create_ui(title)
        self._finalize_size()

    def _finalize_size(self):
        """按实测内容高度确定窗口高度，避免估算 chrome 偏差导致最底行被裁切。"""
        try:
            self.root.update_idletasks()
            req_h = self.bd.winfo_reqheight() + _S(18)
            h = max(_S(350), min(req_h, self._dialog_max_h))
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

        self._scanline_canvas = ScaledCanvas(inner, bg=t["cream"], highlightthickness=0, bd=0)
        self._scanline_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._draw_scanline()

        hdr = tk.Frame(inner, bg=t["yellow"])
        hdr.pack(fill='x')

        title_box = tk.Frame(hdr, bg=t["yellow"], padx=_S(12), pady=_S(10))
        title_box.pack(side='left', fill='x', expand=True)
        rv = ScaledCanvas(title_box, width=12, height=12, bg=t["yellow"], highlightthickness=0, bd=0)
        rv.pack(side='left', padx=(_S(0), _S(8)))
        rv.create_oval(1, 1, 11, 11, fill=t["black"], outline='')

        tk.Label(title_box, text="选择任务",
                 font=('Microsoft YaHei', 13, 'bold'),
                 bg=t["yellow"], fg=t["black"], anchor='w',
                 cursor='fleur').pack(side='left', padx=(_S(4), _S(0)))

        close_lbl = tk.Label(hdr, text="×",
                             font=('JetBrains Mono', 13, 'bold'),
                             bg=t["red"], fg=t["white"], width=3, cursor='hand2')
        close_lbl.pack(side='right', fill='y')
        close_lbl.bind('<Button-1>', lambda e: self._close())
        close_lbl.bind('<Enter>', lambda e: close_lbl.config(bg=t["black"], fg=t["yellow"]))
        close_lbl.bind('<Leave>', lambda e: close_lbl.config(bg=t["red"], fg=t["white"]))

        tk.Frame(inner, bg=t["black"], height=_S(4)).pack(fill='x')

        app, win_title = self._parse_context()
        guide = tk.Frame(inner, bg=t["cream"])
        guide.pack(fill='x', padx=_S(14), pady=(_S(10), _S(6)))
        tk.Label(guide, text="已检测到当前应用与窗口",
                 font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["cream"], fg=t["black"], anchor='w').pack(fill='x')

        readout = tk.Frame(inner, bg=t["cream"], highlightthickness=3,
                           highlightbackground=t["black"])
        readout.pack(fill='x', padx=_S(14), pady=(_S(0), _S(6)))

        top = tk.Frame(readout, bg=t["cream"], padx=_S(10), pady=_S(7))
        top.pack(fill='x')
        tk.Label(top, text="APP",
                 font=('JetBrains Mono', 8, 'bold'),
                 bg=t["cream"], fg=t["red"], width=5, anchor='w').pack(side='left')
        tk.Label(top, text=app[:18] or "W / TRACE",
                 font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["cream"], fg=t["black"], anchor='w').pack(side='left', fill='x', expand=True)

        bottom = tk.Frame(readout, bg=t["cream"], padx=_S(10))
        bottom.pack(fill='x', pady=(_S(0), _S(7)))
        tk.Label(bottom, text="窗口 · " + (win_title[:46] or title),
                 font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["cream"], fg=t["muted"], anchor='w').pack(fill='x')

        if self.options:
            guide = "请选择要切换的任务，或在下方创建新任务。双击任务，或选中后按 Enter 确认。"
        else:
            guide = "暂无任务。在下方输入任务名后按 Enter 创建第一个任务。"
        tk.Label(inner, text=guide,
                 font=('Microsoft YaHei', 8, 'bold'),
                 bg=t["cream"], fg=t["black"], anchor='w').pack(fill='x', padx=_S(14), pady=(_S(0), _S(8)))

        options_shell = tk.Frame(inner, bg=t["cream"])
        options_shell.pack(fill='x', padx=_S(14))

        if self._needs_option_scroll:
            # 任务多于可见上限：固定可视高度 + 滚动条
            options_shell.configure(height=self._options_area_h)
            options_shell.pack_propagate(False)
            options_canvas = ScaledCanvas(options_shell, bg=t["cream"],
                                       highlightthickness=0, bd=0)
            options_scroll = CyberScrollbar(options_shell, options_canvas.yview)
            options_wrap = tk.Frame(options_canvas, bg=t["cream"])
            options_wrap.bind('<Configure>',
                              lambda e: options_canvas.configure(scrollregion=options_canvas.bbox('all')))
            self._options_window = options_canvas.create_window((0, 0), window=options_wrap, anchor='nw', width=418)
            options_canvas.configure(yscrollcommand=options_scroll.set)
            options_canvas.bind('<Configure>', lambda e: options_canvas.itemconfigure(self._options_window, width=e.width - _S(12)))
            options_canvas.pack(side='left', fill='both', expand=True)
            options_scroll.pack(side='right', fill='y', padx=(_S(4), _S(0)))
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
            empty.pack(fill='x', pady=(_S(0), _S(8)))
            tk.Label(empty, text="暂无任务 · 在下方输入新建",
                     font=('Microsoft YaHei', 10, 'bold'),
                     bg=t["cream"], fg=t["red"]).pack(pady=_S(15))

        self.input_wrap = tk.Frame(inner, bg=t["cream"], highlightthickness=3,
                                   highlightbackground=t["black"])
        self.input_wrap.pack(fill='x', padx=_S(14), pady=(_S(8), _S(0)))

        input_row = tk.Frame(self.input_wrap, bg=t["cream"], padx=_S(10), pady=_S(7))
        input_row.pack(fill='x')
        tk.Label(input_row, text=">",
                 font=('JetBrains Mono', 11, 'bold'),
                 bg=t["cream"], fg=t["red"]).pack(side='left')

        self.entry = tk.Entry(input_row, font=('Microsoft YaHei', 10, 'bold'),
                              bg=t["cream"], fg=t["black"],
                              insertbackground=t["teal"],
                              relief='flat', bd=0)
        self.entry.pack(side='left', fill='x', expand=True, padx=(_S(7), _S(0)))
        self.entry.insert(0, "输入新任务名")
        self.entry.bind('<FocusIn>', lambda e: self._on_entry_focus())
        self.entry.bind('<Button-1>', lambda e: self._select_input(), add='+')
        self.entry.bind('<Return>', lambda e: self._confirm())
        self.entry.bind('<Escape>', lambda e: self._close())
        self.entry.bind('<Up>', lambda e: self._entry_move_to_options())
        self.entry.bind('<Down>', lambda e: self._entry_move_to_options())

        help_row = tk.Frame(inner, bg=t["cream"])
        help_row.pack(fill='x', padx=_S(14), pady=(_S(8), _S(12)))

        for txt, clr in [
            ("↑/↓ 选择", t["ink_3"]),
            ("Enter 确认", t["ink_3"]),
            ("输入框新建", t["ink_3"]),
            ("R 休息", t["drift"]),
            ("Esc 取消", t["ink_3"]),
        ]:
            tk.Label(help_row, text=txt,
                     font=('Microsoft YaHei', 8, 'bold'),
                     bg=t["cream"], fg=clr).pack(side='left', padx=(_S(0), _S(10)))

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
        row.pack(fill='x', pady=(_S(0), _S(6)))

        stripe = ScaledCanvas(row, width=5, height=34, bg=bg,
                           highlightthickness=0, bd=0, cursor='hand2')
        stripe.pack(side='left', fill='y')

        key_lbl = tk.Label(row, text=f"[{idx + 1}]",
                           font=('JetBrains Mono', 10, 'bold'),
                           bg=bg, fg=t["black"], cursor='hand2')
        key_lbl.pack(side='left', padx=(_S(10), _S(8)), pady=_S(6))

        name_lbl = tk.Label(row, text=opt[:28],
                            font=('Microsoft YaHei', 10, 'bold'),
                            bg=bg, fg=t["black"], anchor='w', cursor='hand2')
        name_lbl.pack(side='left', fill='x', expand=True, pady=_S(6))

        edit_btn = None
        del_btn = None
        if idx < len(self.task_ids):
            tid = self.task_ids[idx]
            if self.on_task_edit:
                edit_btn = tk.Label(row, text="改", font=('Microsoft YaHei', 8, 'bold'),
                                    bg=t["white"], fg=t["black"], cursor='hand2', padx=_S(5),
                                    highlightthickness=2, highlightbackground=t["black"])
                edit_btn.pack(side='right', padx=(_S(0), _S(3)))
                edit_btn.bind('<Button-1>', lambda e, tid=tid, opt=opt: self._inline_edit(tid, opt))
            if self.on_task_delete:
                del_btn = tk.Label(row, text="删", font=('Microsoft YaHei', 8, 'bold'),
                                   bg=t["white"], fg=t["red"], cursor='hand2', padx=_S(5),
                                   highlightthickness=2, highlightbackground=t["black"])
                del_btn.pack(side='right', padx=(_S(0), _S(8)))
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
                     highlightbackground=t["black"], padx=_S(14), pady=_S(12))
        f.pack()

        tk.Label(f, text=f"重命名: {option_text}",
                 font=('Microsoft YaHei', 10, 'bold'),
                 bg=t["cream"], fg=t["black"]).pack(anchor='w', pady=(_S(0), _S(8)))

        e = tk.Entry(f, font=('Microsoft YaHei', 10, 'bold'),
                     bg=t["white"], fg=t["black"],
                     insertbackground=t["teal"], relief='flat', bd=0,
                     highlightthickness=3, highlightbackground=t["black"])
        e.pack(ipady=_S(6), ipadx=_S(8))
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

        _place_overlay(d, self.root, _S(280), _S(80))
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
            empty.pack(fill='x', pady=(_S(0), _S(8)))
            tk.Label(empty, text="暂无任务 · 在下方输入新建",
                     font=('Microsoft YaHei', 10, 'bold'),
                     bg=t["cream"], fg=t["red"]).pack(pady=_S(15))
        if getattr(self, '_options_canvas', None):
            self._options_canvas.update_idletasks()
            self._options_canvas.configure(scrollregion=self._options_canvas.bbox('all'))
        self._refresh_selection()

    def show(self) -> Optional[str]:
        self.root.lift()
        _win_set_topmost(self.root, True)
        self._parent.wait_window(self.root)
        return self.result


_PRIVACY_POLICY_TEXT = """【WorkTrace 隐私政策】

最后更新：2026-08-03

■ 1. 我们抓什么数据
WorkTrace 在你使用过程中会读取当前活跃窗口的以下信息：
  - 应用程序名（如 chrome.exe、wechat.exe）
  - 窗口标题
  - 网址（仅浏览器窗口，通过 Windows UIA 接口读取）
  - 网页正文摘要（仅浏览器窗口，前 80 字符，可在设置中关闭）

■ 2. 我们存什么数据
以下数据仅保存在你本地电脑的 time_tracker.db：
  - 活动记录：应用名、窗口标题、网址、起止时间、时长
  - 任务列表、关键词
  - 系统统计：今日总专注、休息、空闲时长

■ 3. 我们发送什么到 AI
当"内容感知偏离"开关打开时，以下信息会经中转服务转发到 AI：
  - 当前任务名、关键词
  - 当前窗口的应用名、标题、网址
  - 网页正文摘要（前 80 字符，关闭"正文外发"则不发）

■ 4. 中转服务不存什么
中转服务不存储你的窗口内容、网址或正文，
只统计调用次数和 token 数用于计费和限额。
中转服务位于运营者（你邀请码的发放者）的服务器上。

■ 5. 数据流向
客户端 -> 中转服务（不存内容，只统计）-> AI provider（火山方舟/DeepSeek 等）

■ 6. 你可以怎么关闭
  - 关闭"内容感知偏离"：完全不发任何数据到 AI，仅用进程名启发式判定
  - 关闭"正文外发"：仍发任务名/标题/网址，但不发正文摘要
  - 邀请码弹窗选"不用 AI"：同时关闭以上两个开关
  - 完全卸载：删除 WorkTrace 目录及 time_tracker.db 即可彻底清除本地数据

■ 7. 邀请码用途
邀请码仅用于中转服务的流量鉴权和限额，不绑定你的身份信息。
不同邀请码独立计数，可在管理后台查看用量。

■ 8. 联系方式
如对隐私政策有疑问，请联系你的邀请码发放者。
"""


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
        self._entries = {}      # key -> tk.Entry
        self._orig_auto_launch = bool(cfg.get("auto_launch", False))

        t = self.theme()
        self.win = tk.Toplevel(parent)
        self.win.title("SETTINGS")
        self.win.overrideredirect(True)
        self.win.configure(bg=t["cream"])
        self.win.attributes('-topmost', True)

        self.win.update_idletasks()
        w, h = _S(340), _S(600)
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

        title_box = tk.Frame(hdr, bg=t["yellow"], padx=_S(12), pady=_S(10))
        title_box.pack(side='left', fill='x', expand=True)

        rivet = ScaledCanvas(title_box, width=11, height=11, bg=t["yellow"], highlightthickness=0)
        rivet.pack(side='left', padx=(_S(0), _S(8)))
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

        tk.Frame(bd, bg=t["black"], height=_S(4)).pack(fill='x')

        scroll_wrap = tk.Frame(bd, bg=t["cream"])
        scroll_wrap.pack(fill='both', expand=True, padx=_S(12), pady=(_S(12), _S(0)))

        canvas = ScaledCanvas(scroll_wrap, bg=t["cream"], highlightthickness=0, bd=0)
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
        self._make_slider(content, "专注后休息时间", "rest_per_pomodoro",
                          1, 30, self.cfg.get("rest_per_pomodoro", 5), 1, "分钟",
                          "每完成一个专注单元后建议休息的时长，用于统计对比实际休息是否偏少。")

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
        self._make_slider(content, "自动结束阈值", "auto_end_threshold",
                          30, 180, int(self.cfg.get("auto_end_threshold", 3600) // 60), 5, "分钟",
                          "连续无操作超过该时间后，自动判定今日工作结束并弹出复盘。",
                          scale=60)

        # ---- 启动 ----
        self._make_plate(content, "启动")
        self._make_toggle(content, "专注单元完成提示", "pomodoro_sound")
        self._make_toggle(content, "吸边隐藏", "edge_hide")
        self._make_toggle(content, "关闭到托盘", "minimize_to_tray")
        self._make_toggle(content, "启动后自动记录", "auto_start_track")
        self._make_toggle(content, "跨日自动开始", "auto_start_new_day")
        self._make_toggle(content, "开机自启", "auto_launch")

        # ---- AI 判定 ----
        self._make_plate(content, "AI 判定")
        self._make_toggle(content, "内容感知偏离", "ai_enabled")
        self._make_toggle(content, "正文外发", "body_send")
        self._make_input(content, "邀请码", "ark_api_key",
                        "WorkTrace 通过中转服务调用 AI，请输入你拿到的邀请码（形如 WT-XXXXXX）。"
                        "没有邀请码时关闭右侧的'内容感知偏离'即可继续使用其余功能。")
        self._make_input(content, "中转地址", "ark_endpoint",
                        "中转服务的 HTTP 地址。默认 http://localhost:8000/api/v3/chat/completions。"
                        "运营者换公网穿透地址时改这里，无需重新打包 exe。")

        tk.Frame(bd, bg=t["black"], height=4).pack(fill='x', pady=(_S(8), _S(0)))

        btn_row = tk.Frame(bd, bg=t["cream"])
        btn_row.pack(fill='x', padx=_S(0), pady=_S(0))

        inner_btn = tk.Frame(btn_row, bg=t["cream"], padx=_S(12), pady=_S(12))
        inner_btn.pack(fill='x')

        self._make_metal_btn(inner_btn, "恢复默认", self._reset_defaults,
                             kind='danger').pack(side='left')
        self._make_metal_btn(inner_btn, "隐私政策", self._show_privacy,
                             kind='normal').pack(side='left', padx=(_S(8), _S(0)))

        right_box = tk.Frame(inner_btn, bg=t["cream"])
        right_box.pack(side='right')
        self._make_metal_btn(right_box, "取消",
                             lambda: self.win.destroy(),
                             kind='normal').pack(side='left', padx=(_S(0), _S(8)))
        self._make_metal_btn(right_box, "保存", self._save,
                             kind='primary').pack(side='left')

        # 拖拽 — 仅标题栏
        _bind_title_drag(self.win, hdr)

    # ---------- 视觉组件 ----------
    def _make_plate(self, parent, text):
        t = self.theme()
        row = tk.Frame(parent, bg=t["red"], highlightthickness=3, highlightbackground=t["black"])
        row.pack(fill='x', pady=(_S(12), _S(8)))

        inner = tk.Frame(row, bg=t["red"], padx=_S(8), pady=_S(4))
        inner.pack(fill='x')
        rivet = ScaledCanvas(inner, width=9, height=9, bg=t["red"], highlightthickness=0)
        rivet.pack(side='left', padx=(_S(0), _S(6)))
        rivet.create_oval(1, 1, 8, 8, fill=t["white"], outline=t["black"], width=1)

        tk.Label(inner, text=text, font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["red"], fg=t["white"]).pack(side='left', padx=(_S(0), _S(8)))

    def _show_help_tip(self, widget, text):
        self._hide_help_tip()
        t = self.theme()
        tip = tk.Toplevel(self.win)
        tip.withdraw()
        tip.overrideredirect(True)
        tip.attributes('-topmost', True)
        tip.configure(bg=t["black"])
        box = tk.Frame(tip, bg=t["cream"], highlightthickness=3,
                       highlightbackground=t["black"], padx=_S(8), pady=_S(6))
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
        row.pack(fill='x', pady=_S(5))

        label_box = tk.Frame(row, bg=t["white"], width=_S(90), height=_S(28))
        label_box.pack(side='left')
        label_box.pack_propagate(False)
        tk.Label(label_box, text=label, font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["white"], fg=t["black"], anchor='w').pack(side='left')
        if help_text:
            help_lbl = tk.Label(label_box, text="?", font=('JetBrains Mono', 8, 'bold'),
                                bg=t["yellow"], fg=t["black"], width=2, cursor='question_arrow',
                                highlightthickness=2, highlightbackground=t["black"])
            help_lbl.pack(side='left', padx=(_S(5), _S(0)))
            help_lbl.bind('<Enter>', lambda e, txt=help_text: self._show_help_tip(e.widget, txt))
            help_lbl.bind('<Leave>', lambda e: self._hide_help_tip())

        val_lbl = tk.Label(row, text=f"{current}{unit}", font=('JetBrains Mono', 10, 'bold'),
                           bg=t["white"], fg=t["red"], anchor='e', width=7)
        val_lbl.pack(side='right')

        cw, ch = 132, 24
        c = ScaledCanvas(row, width=cw, height=ch, bg=t["white"], highlightthickness=0, bd=0, cursor='hand2')
        c.pack(side='right', padx=(_S(6), _S(8)))

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
        row.pack(fill='x', pady=_S(6))

        tk.Label(row, text=label, font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["white"], fg=t["black"], anchor='w').pack(side='left')

        var = tk.BooleanVar(value=bool(self.cfg.get(key, False)))
        self._vars[key] = var

        cw, ch = 48, 24
        c = ScaledCanvas(row, width=cw, height=ch, bg=t["white"], highlightthickness=0, bd=0, cursor='hand2')
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

    def _make_input(self, parent, label, key, help_text=None):
        """单行文本输入。存到 self._vars[key]（StringVar），保存时直接落到 cfg[key]。"""
        t = self.theme()
        row = tk.Frame(parent, bg=t["white"])
        row.pack(fill='x', pady=_S(6))

        label_box = tk.Frame(row, bg=t["white"], width=_S(90), height=_S(28))
        label_box.pack(side='left')
        label_box.pack_propagate(False)
        tk.Label(label_box, text=label, font=('Microsoft YaHei', 9, 'bold'),
                 bg=t["white"], fg=t["black"], anchor='w').pack(side='left')
        if help_text:
            help_lbl = tk.Label(label_box, text="?", font=('JetBrains Mono', 8, 'bold'),
                                bg=t["yellow"], fg=t["black"], width=2, cursor='question_arrow',
                                highlightthickness=2, highlightbackground=t["black"])
            help_lbl.pack(side='left', padx=(_S(5), _S(0)))
            help_lbl.bind('<Enter>', lambda e, txt=help_text: self._show_help_tip(e.widget, txt))
            help_lbl.bind('<Leave>', lambda e: self._hide_help_tip())

        var = tk.StringVar(value=str(self.cfg.get(key, "") or ""))
        entry = tk.Entry(row, textvariable=var, font=('JetBrains Mono', 9, 'bold'),
                        bg=t["cream"], fg=t["black"],
                        insertbackground=t["teal"],
                        relief='flat', bd=0,
                        width=22)
        entry.pack(side='right', fill='x', expand=True, padx=(_S(6), _S(8)))
        self._vars[key] = var
        self._entries[key] = entry
        return entry

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
                       bg=bg, fg=fg, padx=_S(14), pady=_S(6), relief='flat', cursor='hand2',
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
        for key, entry in self._entries.items():
            v = self._vars.get(key)
            if v is not None:
                entry.delete(0, 'end')
                entry.insert(0, v.get())

    def _show_privacy(self):
        """弹出隐私政策 Toplevel。"""
        t = self.theme()
        win = tk.Toplevel(self.win)
        win.title("PRIVACY")
        win.overrideredirect(True)
        win.configure(bg=t["cream"])
        win.attributes('-topmost', True)
        win.update_idletasks()
        w, h = _S(440), _S(560)
        _place_centered(win, self.win, w, h)

        bd = tk.Frame(win, bg=t["cream"], highlightthickness=4,
                      highlightbackground=t["black"])
        bd.pack(fill='both', expand=True)

        hdr = tk.Frame(bd, bg=t["yellow"], highlightthickness=0)
        hdr.pack(fill='x')
        title_box = tk.Frame(hdr, bg=t["yellow"], padx=_S(12), pady=_S(10))
        title_box.pack(side='left', fill='x', expand=True)
        tk.Label(title_box, text="隐私政策", font=('JetBrains Mono', 14, 'bold'),
                 bg=t["yellow"], fg=t["black"], anchor='w').pack(side='left')
        close_lbl = tk.Label(hdr, text="×", font=('JetBrains Mono', 13, 'bold'),
                             bg=t["red"], fg=t["white"], width=3, cursor='hand2')
        close_lbl.pack(side='right', fill='y')
        close_lbl.bind('<Button-1>', lambda e: win.destroy())
        close_lbl.bind('<Enter>', lambda e, l=close_lbl: l.config(bg=t["black"], fg=t["yellow"]))
        close_lbl.bind('<Leave>', lambda e, l=close_lbl: l.config(bg=t["red"], fg=t["white"]))

        tk.Frame(bd, bg=t["black"], height=_S(4)).pack(fill='x')

        text_frame = tk.Frame(bd, bg=t["cream"])
        text_frame.pack(fill='both', expand=True, padx=_S(12), pady=_S(12))
        text = tk.Text(text_frame, font=('Microsoft YaHei', 9),
                      bg=t["white"], fg=t["black"],
                      relief='flat', bd=0, wrap='word',
                      padx=_S(10), pady=_S(10),
                      highlightthickness=2, highlightbackground=t["black"])
        sb = tk.Scrollbar(text_frame, command=text.yview)
        text.configure(yscrollcommand=sb.set)
        text.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        text.insert('1.0', _PRIVACY_POLICY_TEXT)
        text.configure(state='disabled')

        btn_row = tk.Frame(bd, bg=t["cream"], padx=_S(12), pady=_S(12))
        btn_row.pack(fill='x')
        self._make_metal_btn(btn_row, "我知道了", win.destroy,
                             kind='primary').pack(side='right')

        win.bind('<Escape>', lambda e: win.destroy())
        _bind_title_drag(win, hdr)
        _win_set_topmost(win, True)


class _DatePickerWindow:
    """简易日历选择器：孟菲斯风格，可切换月份，点击日期回调。
    每个日期下方有色点：蓝色=有数据，灰色=无数据，未来日期无点。
    """

    def __init__(self, parent, current_date_str: str, on_pick, db=None):
        self.on_pick = on_pick
        self.db = db
        t = theme()
        self.win = tk.Toplevel(parent)
        self.win.title("DATE")
        self.win.overrideredirect(True)
        self.win.configure(bg=t["cream"])
        self.win.attributes('-topmost', True)
        self.win.update_idletasks()

        try:
            dt = datetime.strptime(current_date_str, '%Y-%m-%d')
        except Exception:
            dt = datetime.now()
        today_str = datetime.now().strftime('%Y-%m-%d')
        self._today_str = today_str
        self._view_year = dt.year
        self._view_month = dt.month
        # 数据日期集合（用于色点区分）
        self._data_dates = set()
        if self.db is not None:
            try:
                for ds in self.db.get_available_dates(limit=120):
                    self._data_dates.add(ds)
            except Exception:
                pass

        self._create_ui()
        self._draw_month()

        w = _S(224)
        h = _S(264)
        _place_overlay(self.win, parent, w, h)
        self.win.bind('<Escape>', lambda e: self.win.destroy())
        self.win.focus_set()
        _win_set_topmost(self.win, True)

    def _maybe_close(self):
        try:
            if self.win.focus_get() is None:
                self.win.destroy()
        except Exception:
            pass

    def _create_ui(self):
        t = theme()
        bd = tk.Frame(self.win, bg=t["cream"], highlightthickness=4,
                      highlightbackground=t["black"])
        bd.pack(fill='both', expand=True)

        # 标题栏：月份 + prev/next
        titlebar = tk.Frame(bd, bg=t["red"], padx=_S(6), pady=_S(6))
        titlebar.pack(fill='x')

        prev_m = tk.Label(titlebar, text="<",
                          font=('JetBrains Mono', 10, 'bold'),
                          bg=t["white"], fg=t["black"], cursor='hand2',
                          highlightthickness=2, highlightbackground=t["black"], padx=_S(6))
        prev_m.pack(side='left')
        prev_m.bind('<Button-1>', lambda e: self._shift_month(-1))

        self.month_lbl = tk.Label(titlebar, text="",
                                  font=('JetBrains Mono', 10, 'bold'),
                                  bg=t["white"], fg=t["black"], padx=_S(10), pady=_S(2),
                                  highlightthickness=2, highlightbackground=t["black"])
        self.month_lbl.pack(side='left', fill='x', expand=True)

        next_m = tk.Label(titlebar, text=">",
                          font=('JetBrains Mono', 10, 'bold'),
                          bg=t["white"], fg=t["black"], cursor='hand2',
                          highlightthickness=2, highlightbackground=t["black"], padx=_S(6))
        next_m.pack(side='left')
        next_m.bind('<Button-1>', lambda e: self._shift_month(1))

        close_lbl = tk.Label(titlebar, text="×",
                             font=('JetBrains Mono', 11, 'bold'),
                             bg=t["black"], fg=t["yellow"], width=3, cursor='hand2')
        close_lbl.pack(side='right', fill='y')
        close_lbl.bind('<Button-1>', lambda e: self.win.destroy())

        # 星期标签行
        dow_row = tk.Frame(bd, bg=t["cream"])
        dow_row.pack(fill='x', padx=_S(6), pady=(_S(6), _S(2)))
        for i, name in enumerate(['一', '二', '三', '四', '五', '六', '日']):
            tk.Label(dow_row, text=name,
                     font=('Microsoft YaHei', 9, 'bold'),
                     bg=t["cream"], fg=t["muted"], width=2).pack(side='left', expand=True)

        # 日期网格（6 行 7 列） - 每个格子是 Frame，内含日期数字 + 色点
        self.grid_frame = tk.Frame(bd, bg=t["cream"])
        self.grid_frame.pack(fill='both', expand=True, padx=_S(6), pady=(_S(0), _S(6)))
        self._cells = []
        self._dots = []
        for r in range(6):
            row = tk.Frame(self.grid_frame, bg=t["cream"])
            row.pack(fill='x', expand=True)
            for c in range(7):
                cell = tk.Frame(row, bg=t["white"], highlightthickness=1,
                                highlightbackground=t["cream"])
                cell.pack(side='left', expand=True, fill='both', padx=_S(1), pady=_S(1))
                day_lbl = tk.Label(cell, text="",
                                   font=('JetBrains Mono', 9, 'bold'),
                                   bg=t["white"], fg=t["black"], cursor='hand2')
                day_lbl.pack(side='top', pady=(_S(2), _S(0)))
                dot = ScaledCanvas(cell, width=4, height=4, bg=t["white"],
                                   highlightthickness=0, bd=0)
                dot.pack(side='top', pady=(_S(1), _S(2)))
                self._cells.append(cell)
                self._dots.append(dot)
                # 点击整个格子都触发
                cell.bind('<Button-1>', lambda e: None)
                day_lbl.bind('<Button-1>', lambda e, w=cell: None)
                # 实际绑定在 _draw_month 中按日期设置

    def _shift_month(self, delta: int):
        m = self._view_month + delta
        y = self._view_year
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        self._view_year = y
        self._view_month = m
        self._draw_month()

    def _draw_month(self):
        t = theme()
        import calendar as _cal
        self.month_lbl.config(text=f"{self._view_year}-{self._view_month:02d}")
        # 当月 1 号是周几（Mon=0，与 _cal 一致；星期标签从一 开始）
        first_wd = _cal.monthrange(self._view_year, self._view_month)[0]
        days_in_month = _cal.monthrange(self._view_year, self._view_month)[1]
        # 上月尾部天数（用于填充前置空格）
        prev_days = _cal.monthrange(
            self._view_year - (1 if self._view_month == 1 else 0),
            12 if self._view_month == 1 else self._view_month - 1)[1]

        blue = "#2563EB"   # RGB(37,99,235) 与今日复盘日历一致
        gray = "#D1D5DB"   # RGB(209,213,219) 淡灰

        # 清空所有 cell
        for idx, cell in enumerate(self._cells):
            day_lbl = cell.winfo_children()[0] if cell.winfo_children() else None
            dot = self._dots[idx]
            cell.config(bg=t["white"], highlightbackground=t["cream"])
            if day_lbl is not None:
                day_lbl.config(text="", bg=t["white"], fg=t["black"])
                day_lbl.unbind('<Button-1>')
            cell.unbind('<Button-1>')
            dot.config(bg=t["white"])
            dot.delete('all')

        for i in range(42):
            cell = self._cells[i]
            day_lbl = cell.winfo_children()[0] if cell.winfo_children() else None
            dot = self._dots[i]
            if i < first_wd:
                # 上月日期
                day = prev_days - first_wd + i + 1
                cell.config(bg=t["cream"], highlightbackground=t["cream"])
                if day_lbl is not None:
                    day_lbl.config(text=str(day), fg=t["muted"], bg=t["cream"])
                dot.config(bg=t["cream"])
            elif i < first_wd + days_in_month:
                # 当月日期
                day = i - first_wd + 1
                ds = f"{self._view_year}-{self._view_month:02d}-{day:02d}"
                is_today = (ds == self._today_str)
                is_future = (ds > self._today_str)
                has_data = ds in self._data_dates
                cell_bg = t["red"] if is_today else t["white"]
                cell_fg = t["white"] if is_today else t["black"]
                cell.config(bg=cell_bg, highlightbackground=t["cream"])
                if day_lbl is not None:
                    day_lbl.config(text=str(day), fg=cell_fg, bg=cell_bg)
                    day_lbl.bind('<Button-1>', lambda e, d=ds: self._pick(d))
                cell.bind('<Button-1>', lambda e, d=ds: self._pick(d))
                # 色点：未来日期无点，有数据蓝色，无数据灰色
                if not is_future:
                    dot_color = blue if has_data else gray
                    dot.config(bg=cell_bg)
                    dot.create_oval(0, 0, 4, 4, fill=dot_color, outline=dot_color)
                else:
                    dot.config(bg=cell_bg)
            else:
                # 下月日期
                day = i - first_wd - days_in_month + 1
                cell.config(bg=t["cream"], highlightbackground=t["cream"])
                if day_lbl is not None:
                    day_lbl.config(text=str(day), fg=t["muted"], bg=t["cream"])
                dot.config(bg=t["cream"])

    def _pick(self, date_str: str):
        try:
            self.on_pick(date_str)
        finally:
            try:
                self.win.destroy()
            except Exception:
                pass


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
        self._stats_w = _S(520)
        _place_centered(self.win, parent, self._stats_w, _S(460))

        # 历史日期切换状态：默认今日；_available_dates 为最近 60 天所有日期 + DB 里有数据的日期（降序）
        today_dt = datetime.now()
        today_str = today_dt.strftime('%Y-%m-%d')
        recent_dates = [(today_dt - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(60)]
        db_dates = self.db.get_available_dates(limit=60)
        self._available_dates = sorted(set(recent_dates) | set(db_dates), reverse=True)
        self._current_date_str = today_str

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

        self._scanline_canvas = ScaledCanvas(inner, bg=t["cream"], highlightthickness=0, bd=0)
        self._scanline_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._draw_scanline()

        titlebar = tk.Frame(inner, bg=t["red"])
        titlebar.pack(fill='x')

        title_box = tk.Frame(titlebar, bg=t["red"], padx=_S(12), pady=_S(10))
        title_box.pack(side='left', fill='x', expand=True)
        rv = ScaledCanvas(title_box, width=12, height=12, bg=t["red"], highlightthickness=0, bd=0)
        rv.pack(side='left', padx=(_S(0), _S(8)))
        rv.create_oval(1, 1, 11, 11, fill=t["white"], outline=t["black"], width=2)

        tk.Label(title_box, text="统计",
                 font=('Microsoft YaHei', 13, 'bold'),
                 bg=t["red"], fg=t["white"], cursor='fleur').pack(side='left', padx=(_S(4), _S(0)))

        close_lbl = tk.Label(titlebar, text="×",
                             font=('JetBrains Mono', 13, 'bold'),
                             bg=t["black"], fg=t["yellow"], width=3, cursor='hand2')
        close_lbl.pack(side='right', fill='y')

        nav = tk.Frame(titlebar, bg=t["red"])
        nav.place(relx=0.5, rely=0.5, anchor='center')

        prev_btn = tk.Label(nav, text="<",
                            font=('JetBrains Mono', 10, 'bold'),
                            bg=t["white"], fg=t["black"], cursor='hand2',
                            highlightthickness=2, highlightbackground=t["black"], padx=_S(6))
        prev_btn.pack(side='left', padx=(_S(0), _S(6)))

        self.date_lbl = tk.Label(nav, text="--",
                                 font=('JetBrains Mono', 10, 'bold'),
                                 bg=t["white"], fg=t["black"], padx=_S(8), pady=_S(2),
                                 highlightthickness=2, highlightbackground=t["black"],
                                 cursor='hand2')
        self.date_lbl.pack(side='left')
        self.date_lbl.bind('<Button-1>', lambda e: self._open_date_picker())

        next_btn = tk.Label(nav, text=">",
                            font=('JetBrains Mono', 10, 'bold'),
                            bg=t["white"], fg=t["black"], cursor='hand2',
                            highlightthickness=2, highlightbackground=t["black"], padx=_S(6))
        next_btn.pack(side='left', padx=(_S(6), _S(0)))
        close_lbl.bind('<Button-1>', lambda e: self.win.destroy())
        close_lbl.bind('<Enter>', lambda e: close_lbl.config(bg=t["yellow"], fg=t["black"]))
        close_lbl.bind('<Leave>', lambda e: close_lbl.config(bg=t["black"], fg=t["yellow"]))

        # 历史切换：< 往更早日期（昨天/前天），> 往更近今日（明天/后天，上限今日）
        prev_btn.bind('<Button-1>', lambda e: self._shift_date(-1))
        next_btn.bind('<Button-1>', lambda e: self._shift_date(1))
        prev_btn.bind('<Enter>', lambda e: prev_btn.config(bg=t["yellow"], fg=t["black"]))
        prev_btn.bind('<Leave>', lambda e: prev_btn.config(bg=t["white"], fg=t["black"]))
        next_btn.bind('<Enter>', lambda e: next_btn.config(bg=t["yellow"], fg=t["black"]))
        next_btn.bind('<Leave>', lambda e: next_btn.config(bg=t["white"], fg=t["black"]))
        self._prev_btn = prev_btn
        self._next_btn = next_btn

        tk.Frame(inner, bg=t["black"], height=_S(4)).pack(fill='x')

        # ---- KPI 卡片行 ----
        kpi_row = tk.Frame(inner, bg=t["cream"])
        kpi_row.pack(fill='x', padx=_S(12), pady=(_S(12), _S(0)))

        self.kpi_cards = {}
        kpi_configs = [
            ("total", "总记录时长", t["teal"]),
            ("focused", "有效工作时长", t["red"]),
            ("away", "离开/休息时长", t["yellow"]),
            ("efficiency", "有效工作占比", t["black"]),
        ]
        for key, name, color in kpi_configs:
            self.kpi_cards[key] = self._make_kpi_card(kpi_row, name, color)

        # ---- 休息对比（期望 vs 实际）----
        rest_section = tk.Frame(inner, bg=t["cream"])
        rest_section.pack(fill='x', padx=_S(18), pady=(_S(10), _S(0)))

        self._make_plate(rest_section, "休息对比")

        rest_row = tk.Frame(rest_section, bg=t["cream"])
        rest_row.pack(fill='x', pady=(_S(6), _S(0)))

        self.rest_expected_lbl = tk.Label(rest_row, text="期望 --",
                                          font=('JetBrains Mono', 10, 'bold'),
                                          bg=t["cream"], fg=t["black"])
        self.rest_expected_lbl.pack(side='left', padx=(_S(0), _S(12)))

        self.rest_actual_lbl = tk.Label(rest_row, text="实际 --",
                                        font=('JetBrains Mono', 10, 'bold'),
                                        bg=t["cream"], fg=t["black"])
        self.rest_actual_lbl.pack(side='left', padx=(_S(0), _S(12)))

        self.rest_diff_lbl = tk.Label(rest_row, text="偏差 --",
                                      font=('JetBrains Mono', 10, 'bold'),
                                      bg=t["cream"], fg=t["muted"])
        self.rest_diff_lbl.pack(side='left', padx=(_S(0), _S(12)))

        self.rest_status_lbl = tk.Label(rest_row, text="",
                                        font=('Microsoft YaHei', 9, 'bold'),
                                        bg=t["cream"], fg=t["muted"])
        self.rest_status_lbl.pack(side='left')

        # ---- 本周概览 + 月环（已隐藏，用户反馈不易理解）----
        # 保留 chart_canvas / ring_canvas 作为占位（None），避免 _load_data 引用报错
        week_block = tk.Frame(inner, bg=t["cream"])
        # week_block.pack(fill='x', padx=_S(18), pady=(_S(10), _S(0)))  # 隐藏
        self.chart_canvas = None
        self.chart_labels = None
        self.ring_canvas = None
        self.ring_meta = None

        # ---- 任务排行 ----
        rank_section = tk.Frame(inner, bg=t["cream"])
        rank_section.pack(fill='x', padx=_S(18), pady=(_S(10), _S(0)))

        self._make_plate(rank_section, "任务排行")

        self.rank_frame = tk.Frame(rank_section, bg=t["cream"])
        self.rank_frame.pack(fill='x', pady=(_S(6), _S(0)))

        # ---- Footer（已隐藏，用户反馈无参考价值）----
        footer = tk.Frame(inner, bg=t["cream"])
        # footer.pack(fill='x', padx=_S(18), pady=(_S(10), _S(14)))
        self.footer_lbl = None

        # 拖拽 — 仅标题栏
        _bind_title_drag(self.win, titlebar)

    def _fit_height(self):
        self.win.update_idletasks()
        vs = _virtual_screen()
        if vs:
            vy, vh = vs[1], vs[3]
        else:
            vy, vh = 0, self.win.winfo_screenheight()
        req_h = min(max(_S(420), self.win.winfo_reqheight() + _S(2)), vh - _S(80))
        x = self.win.winfo_x()
        y = max(vy + _S(40), min(self.win.winfo_y(), vy + vh - req_h - _S(40)))
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
                       bg=t2["yellow"], fg=t2["black"], padx=_S(8), pady=_S(2),
                       highlightthickness=2, highlightbackground=t2["black"])
        tag.pack(side='left')

    def _make_kpi_card(self, parent, name, color):
        t2 = theme()
        card = tk.Frame(parent, bg=t2["cream"], highlightthickness=3,
                        highlightbackground=t2["black"])
        card.pack(side='left', expand=True, fill='x', padx=(_S(0), _S(6)))

        stripe = tk.Canvas(card, bg=t2["cream"], width=1, height=_S(5),
                           highlightthickness=0, bd=0)
        stripe.pack(fill='x')
        stripe._bar_color = color
        stripe._pct = 100  # 默认整条铺满品牌色
        stripe.bind('<Configure>',
                    lambda e, s=stripe: self._draw_kpi_stripe(s, s._pct))

        val = tk.Label(card, text="--",
                       font=('JetBrains Mono', 22, 'bold'),
                       bg=t2["cream"], fg=t2["black"])
        val.pack(pady=(_S(7), _S(0)))

        lb = tk.Label(card, text=name,
                      font=('Microsoft YaHei', 9, 'bold'),
                      bg=t2["cream"], fg=t2["muted"])
        lb.pack(pady=(_S(0), _S(2)))

        sub = tk.Label(card, text="",
                       font=('JetBrains Mono', 8, 'bold'),
                       bg=t2["cream"], fg=t2["muted"])
        sub.pack(pady=(_S(0), _S(6)))

        val.sub = sub
        val.stripe = stripe
        return val

    def _draw_kpi_stripe(self, stripe, pct):
        """KPI 卡片顶部进度条：pct 0-100，按比例填充。"""
        stripe._pct = pct
        stripe.delete('all')
        t2 = theme()
        total_w = max(stripe.winfo_width(), 1)
        h = _S(5)
        stripe.create_rectangle(0, 0, total_w, h, fill=t2["rule"], outline='')
        bar_w = int(total_w * max(0, min(100, pct)) / 100)
        if bar_w > 0:
            stripe.create_rectangle(0, 0, bar_w, h, fill=stripe._bar_color, outline='')

    # ================================================================
    #  数据加载
    # ================================================================

    def _load_data(self):
        t = theme()

        # 刷新扫描线（窗口可能 resize 过）
        self._draw_scanline()

        # 当前查看的日期（默认今日，可由 prev/next 或日期选择器切换）
        cur_str = self._current_date_str
        cur_date = datetime.strptime(cur_str, '%Y-%m-%d')
        today_str = datetime.now().strftime('%Y-%m-%d')
        is_today_view = (cur_str == today_str)

        # 获取当日活动
        activities = self.db.get_date_activities(cur_str)

        has_data = len(activities) > 0
        date_text = cur_str
        self.date_lbl.config(text=date_text,
                             bg=t["red"] if is_today_view else t["white"],
                             fg=t["white"] if is_today_view else t["black"])

        # ---- KPI ----
        task_summary = {}
        for act in activities:
            if act.task_name not in task_summary:
                task_summary[act.task_name] = 0
            task_summary[act.task_name] += act.duration

        total_sec = sum(task_summary.values())
        work_sec = sum(v for k, v in task_summary.items()
                       if '休息' not in k and '离开' not in k and '锁屏' not in k)

        away_sec = total_sec - work_sec  # 含休息/离开/锁屏（广义）

        self.kpi_cards["total"].config(text=self._fmt_duration(total_sec))
        self.kpi_cards["focused"].config(text=self._fmt_duration(work_sec))
        self.kpi_cards["away"].config(text=self._fmt_duration(away_sec))
        eff_pct = int(work_sec / total_sec * 100) if total_sec > 0 else 0
        eff = f"{eff_pct}%" if total_sec > 0 else "--"
        self.kpi_cards["efficiency"].config(text=eff)

        # 有效工作占比卡片：顶部进度条按 eff_pct 填充
        eff_card = self.kpi_cards["efficiency"]
        if hasattr(eff_card, 'stripe') and eff_card.stripe is not None:
            eff_stripe = eff_card.stripe
            self.win.after(50, lambda: self._draw_kpi_stripe(eff_stripe, eff_pct))

        # ---- 休息对比 ----
        unit_sec = max(1, config.get("pomodoro_minutes", 30)) * 60
        completed_pomos = work_sec // unit_sec
        rest_per_sec = max(1, config.get("rest_per_pomodoro", 5)) * 60
        expected_rest_sec = completed_pomos * rest_per_sec
        actual_rest_sec = total_sec - work_sec  # 含休息/离开/锁屏（广义）
        diff_sec = actual_rest_sec - expected_rest_sec
        threshold = 10 * 60
        if abs(diff_sec) <= threshold:
            rest_status = "正常"
            rest_status_fg = t["black"]
        elif diff_sec < 0:
            rest_status = "偏少，建议主动休息"
            rest_status_fg = t["red"]
        else:
            rest_status = "偏多"
            rest_status_fg = t["muted"]
        self.rest_expected_lbl.config(
            text=f"期望 {self._fmt_duration(expected_rest_sec)}")
        self.rest_actual_lbl.config(
            text=f"实际 {self._fmt_duration(actual_rest_sec)}")
        diff_sign = "+" if diff_sec >= 0 else "-"
        self.rest_diff_lbl.config(
            text=f"偏差 {diff_sign}{self._fmt_duration(abs(diff_sec))}",
            fg=rest_status_fg)
        self.rest_status_lbl.config(text=rest_status, fg=rest_status_fg)

        # ---- 本周柱状图（以 cur_date 所在周计算）----
        weekday = cur_date.weekday()  # Mon=0
        monday = cur_date - timedelta(days=weekday)
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

        if self.chart_canvas is not None:
            self._draw_week_chart(daily_data, max_daily, dow_labels, today_str)

        # ---- 月环（以 cur_date 所在月计算，已隐藏，保留计算以维持 footer 逻辑）----
        if self.ring_canvas is not None:
            month_prefix = cur_date.strftime('%Y-%m')
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

        # ---- Footer（已隐藏）----
        if self.footer_lbl is not None:
            self._update_footer(days, daily_data)

        # ---- prev/next 按钮可用性 ----
        self._refresh_nav_state()

    def _shift_date(self, direction: int):
        """逐日切换：direction = +1 往更早日期（前一日），-1 往更近今日（后一日）。
        不跳过无数据日期，date_lbl 只显示日期本身（不附加"无数据"后缀）。
        """
        try:
            cur = datetime.strptime(self._current_date_str, '%Y-%m-%d')
        except ValueError:
            return
        new_dt = cur + timedelta(days=direction)
        new_str = new_dt.strftime('%Y-%m-%d')
        today_str = datetime.now().strftime('%Y-%m-%d')
        # 不允许切到未来日期
        if new_str > today_str:
            return
        # 限制最早到 60 天前（与 _available_dates 初值保持一致）
        earliest = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
        if new_str < earliest:
            return
        self._current_date_str = new_str
        self._load_data()
        self._fit_height()

    def _refresh_nav_state(self):
        """根据当前日期是否到边界，灰化不可用的方向按钮。"""
        t = theme()
        today_str = datetime.now().strftime('%Y-%m-%d')
        earliest = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
        cur = self._current_date_str
        # prev_btn = "<" 往更早日期，已到最早则灰化
        prev_disabled = (cur <= earliest)
        # next_btn = ">" 往更近今日，已到今日则灰化
        next_disabled = (cur >= today_str)
        self._prev_btn.config(
            fg=t["muted"] if prev_disabled else t["black"],
            cursor='' if prev_disabled else 'hand2')
        self._next_btn.config(
            fg=t["muted"] if next_disabled else t["black"],
            cursor='' if next_disabled else 'hand2')

    def _open_date_picker(self):
        """点击日期标签，弹出日历选择器。"""
        _DatePickerWindow(self.win, self._current_date_str, self._on_date_picked, db=self.db)

    def _on_date_picked(self, date_str: str):
        """日期选择器回调：切到所选日期。"""
        self._current_date_str = date_str
        self._load_data()
        self._fit_height()

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
        """彩色进度条排行。系统任务（休息/离开/锁屏）用灰色 + 系统标签区分。"""
        t2 = theme()
        for w in self.rank_frame.winfo_children():
            w.destroy()

        if not task_summary:
            tk.Label(self.rank_frame, text="暂无数据",
                     font=('Microsoft YaHei', 9, 'bold'),
                     bg=t2["white"], fg=t2["muted"]).pack(pady=_S(10))
            return

        sorted_tasks = sorted(task_summary.items(), key=lambda x: x[1], reverse=True)
        max_dur = max(v for _, v in sorted_tasks) if sorted_tasks else 1
        bar_colors = [t2["red"], t2["teal"], t2["yellow"], t2["black"]]

        for idx, (name, dur) in enumerate(sorted_tasks):
            is_system = any(kw in name for kw in ['休息', '离开', '锁屏'])
            row = tk.Frame(self.rank_frame, bg=t2["white"])
            row.pack(fill='x', pady=_S(3))

            if is_system:
                color = t2["muted"]
                name_fg = t2["muted"]
            else:
                color = bar_colors[idx % len(bar_colors)]
                name_fg = t2["black"]

            # 固定宽度的名称区，保证所有轨道条起点一致
            name_area = tk.Frame(row, bg=t2["white"], width=_S(150), height=_S(20))
            name_area.pack(side='left')
            name_area.pack_propagate(False)

            if is_system:
                sys_tag = tk.Label(name_area, text="系统",
                                   font=('Microsoft YaHei', 7, 'bold'),
                                   bg=t2["muted"], fg=t2["white"],
                                   padx=_S(3))
                sys_tag.pack(side='left', padx=(_S(2), _S(4)))
                name_max = 8
            else:
                name_max = 12

            display_name = name if len(name) <= name_max else (name[:name_max] + '…')
            tk.Label(name_area, text=display_name,
                     font=('Microsoft YaHei', 9, 'bold'),
                     bg=t2["white"], fg=name_fg, anchor='w'
                     ).pack(side='left', fill='x', expand=True)

            track = ScaledCanvas(row, bg=t2["cream"], highlightthickness=2,
                              highlightbackground=t2["black"], bd=0,
                              height=12, width=140)
            track.pack(side='left', fill='x', expand=True, padx=(_S(6), _S(6)))

            bar_w = max(5, int((dur / max_dur) * 140))
            track.create_rectangle(0, 0, 140, 12, fill=t2["cream"], outline='')
            track.create_rectangle(0, 0, bar_w, 12, fill=color, outline='')
            if is_system:
                # 系统任务追加斜线纹理，进一步区分
                for sx in range(0, bar_w, 6):
                    track.create_line(sx, 0, sx + 6, 12,
                                      fill=t2["white"], width=1)

            tk.Label(row, text=self._fmt_duration(dur),
                     font=('JetBrains Mono', 9, 'bold'),
                     bg=t2["white"], fg=name_fg, anchor='e', width=7
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
        w, h = _S(420), _S(400)
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

        self._scanline_canvas = ScaledCanvas(inner, bg=t["cream"], highlightthickness=0, bd=0)
        self._scanline_canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._draw_scanline()

        hdr = tk.Frame(inner, bg=t["yellow"])
        hdr.pack(fill='x')

        title_box = tk.Frame(hdr, bg=t["yellow"], padx=_S(12), pady=_S(10))
        title_box.pack(side='left', fill='x', expand=True)
        rv = ScaledCanvas(title_box, width=11, height=11, bg=t["yellow"], highlightthickness=0, bd=0)
        rv.pack(side='left', padx=(_S(0), _S(8)))
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

        tk.Frame(inner, bg=t["black"], height=_S(4)).pack(fill='x')

        list_bg = tk.Frame(inner, bg=t["cream"])
        list_bg.pack(fill='both', expand=True, padx=_S(12), pady=(_S(12), _S(0)))

        self.task_list_canvas = ScaledCanvas(list_bg, bg=t["cream"], highlightthickness=0, bd=0)
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
        self.task_list_scroll.pack(side='right', fill='y', padx=(_S(4), _S(0)))

        add_wrap = tk.Frame(inner, bg=t["cream"], highlightthickness=3,
                            highlightbackground=t["black"])
        add_wrap.pack(fill='x', padx=_S(12), pady=(_S(8), _S(12)))

        add_row = tk.Frame(add_wrap, bg=t["cream"], padx=_S(10), pady=_S(8))
        add_row.pack(fill='x')

        tk.Label(add_row, text=">", font=('JetBrains Mono', 11, 'bold'),
                 bg=t["cream"], fg=t["red"]).pack(side='left')

        self.new_entry = tk.Entry(add_row, font=('Microsoft YaHei', 10, 'bold'),
                                  bg=t["cream"], fg=t["black"],
                                  insertbackground=t["teal"], relief='flat', bd=0)
        self.new_entry.pack(side='left', fill='x', expand=True, padx=(_S(7), _S(0)))
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
        self.task_list_canvas.itemconfigure(self.task_list_window, width=event.width - _S(14))

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
            empty.pack(fill='both', expand=True, pady=_S(12))
            tk.Label(empty, text="ADD FIRST TASK", font=('JetBrains Mono', 14, 'bold'),
                     bg=t["cream"], fg=t["red"]).pack(expand=True)
            tk.Label(empty, text="输入任务名后按 ENTER", font=('Microsoft YaHei', 9, 'bold'),
                     bg=t["cream"], fg=t["black"]).pack(pady=(_S(0), _S(44)))
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
        shadow.pack(fill='x', pady=(_S(0), _S(10)), padx=(_S(5), _S(1)))
        card = tk.Frame(shadow, bg=card_bg,
                        highlightthickness=3, highlightbackground=t["black"])
        card.pack(fill='x', padx=(_S(0), _S(4)), pady=(_S(0), _S(4)))

        row = tk.Frame(card, bg=card_bg, padx=_S(0), pady=_S(0))
        row.pack(fill='x')

        stripe = ScaledCanvas(row, width=7, height=58, bg=card_bg,
                           highlightthickness=0, bd=0)
        stripe.pack(side='left', fill='y')
        stripe.create_rectangle(0, 0, 7, 70, fill=t["yellow"] if is_current else color, outline='')
        if is_current:
            stripe.create_rectangle(0, 0, 7, 16, fill=t["red"], outline='')

        info = tk.Frame(row, bg=card_bg, padx=_S(10), pady=_S(8))
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
                 bg=card_bg, fg=t["muted"], anchor='w').pack(fill='x', pady=(_S(3), _S(0)))

        if is_current:
            tk.Label(row, text="ACTIVE",
                     font=('JetBrains Mono', 8, 'bold'),
                     bg=t["red"], fg=t["white"], padx=_S(6), pady=_S(2)).pack(side='left', padx=(_S(0), _S(8)))

        actions = tk.Frame(row, bg=card_bg, padx=_S(8))
        actions.pack(side='right')

        kw_btn = tk.Label(actions, text="词",
                          font=('Microsoft YaHei', 8),
                          bg=card_bg, fg=t["muted"],
                          padx=_S(4), pady=_S(1), cursor='hand2')
        kw_btn.pack(side='left', padx=(_S(0), _S(6)))
        kw_btn.bind('<Button-1>', lambda e, tsk=task: self._manage_keywords(tsk))
        kw_btn.bind('<Enter>', lambda e: kw_btn.config(fg=t["black"]))
        kw_btn.bind('<Leave>', lambda e: kw_btn.config(fg=t["muted"]))

        edit_btn = self._make_action_btn(actions, "改", t["black"])
        edit_btn.pack(side='left', padx=(_S(0), _S(5)))
        edit_btn.bind('<Button-1>', lambda e, tsk=task: self._edit_task(tsk, self.name_labels.get(tsk.id)))

        del_btn = self._make_action_btn(actions, "删", t["red"])
        del_btn.pack(side='left')
        del_btn.bind('<Button-1>', lambda e, tsk=task: self._delete_task(tsk))

        for widget in (shadow, card, row, stripe, info, name_lbl, actions, kw_btn, edit_btn, del_btn):
            widget.bind('<MouseWheel>', self._on_task_list_mousewheel, add='+')

    def _make_action_btn(self, parent, text, fg):
        t = theme()
        lbl = tk.Label(parent, text=text,
                       font=('Microsoft YaHei', 9, 'bold'),
                       bg=t["white"], fg=fg,
                       padx=_S(7), pady=_S(3), cursor='hand2',
                       highlightthickness=2, highlightbackground=t["black"])
        lbl.bind('<Enter>', lambda e: lbl.config(bg=t["yellow"], fg=t["black"]))
        lbl.bind('<Leave>', lambda e: lbl.config(bg=t["white"], fg=fg))
        return lbl

    def _edit_task(self, task, label_widget=None):
        t = theme()
        old_name = task.name
        old_desc = getattr(task, 'description', '') or ''

        d = tk.Toplevel(self.win)
        d.withdraw()
        d.overrideredirect(True)
        d.configure(bg=t["cream"])
        d.attributes('-topmost', True)

        f = tk.Frame(d, bg=t["cream"], highlightthickness=3,
                     highlightbackground=t["black"], padx=_S(14), pady=_S(12))
        f.pack()

        tk.Label(f, text=f"编辑: {old_name}",
                 font=('Microsoft YaHei', 10, 'bold'),
                 bg=t["cream"], fg=t["black"]).pack(anchor='w', pady=(_S(0), _S(8)))

        tk.Label(f, text="名称",
                 font=('Microsoft YaHei', 8, 'bold'),
                 bg=t["cream"], fg=t["muted"]).pack(anchor='w')
        e = tk.Entry(f, font=('Microsoft YaHei', 10, 'bold'),
                     bg=t["white"], fg=t["black"],
                     insertbackground=t["teal"], relief='flat', bd=0,
                     highlightthickness=3, highlightbackground=t["black"])
        e.pack(fill='x', ipady=_S(6), ipadx=_S(8), pady=(_S(2), _S(10)))
        e.insert(0, old_name)
        e.select_range(0, 'end')
        e.focus_set()

        tk.Label(f, text="描述（选填，给 AI 判定作上下文）",
                 font=('Microsoft YaHei', 8, 'bold'),
                 bg=t["cream"], fg=t["muted"]).pack(anchor='w')
        desc_e = tk.Entry(f, font=('Microsoft YaHei', 9),
                          bg=t["white"], fg=t["black"],
                          insertbackground=t["teal"], relief='flat', bd=0,
                          highlightthickness=3, highlightbackground=t["black"])
        desc_e.pack(fill='x', ipady=_S(5), ipadx=_S(8), pady=(_S(2), _S(4)))
        desc_e.insert(0, old_desc)

        def do_save(ev=None):
            new_name = e.get().strip()
            new_desc = desc_e.get().strip()
            changed = False
            if new_name and new_name != old_name:
                self.db.rename_task(task.id, new_name)
                task.name = new_name
                for tsk in self.tracker.today_tasks:
                    if tsk.id == task.id:
                        tsk.name = new_name
                changed = True
            if new_desc != old_desc:
                self.db.update_task_description(task.id, new_desc)
                task.description = new_desc
                for tsk in self.tracker.today_tasks:
                    if tsk.id == task.id:
                        tsk.description = new_desc
                # 若正是当前进行中的任务，热更新供 _classify_context 立即生效
                if (self.tracker.current_task
                        and self.tracker.current_task.id == task.id):
                    self.tracker.current_task.description = new_desc
                changed = True
            if changed:
                self._refresh_list()
            d.destroy()

        e.bind('<Return>', do_save)
        desc_e.bind('<Return>', do_save)
        e.bind('<Escape>', lambda ev: d.destroy())
        desc_e.bind('<Escape>', lambda ev: d.destroy())

        _place_overlay(d, self.win, _S(340), _S(170))
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
                     highlightbackground=t["black"], padx=_S(16), pady=_S(12))
        f.pack()

        is_current = (self.tracker.current_task
                      and self.tracker.current_task.id == task.id)
        msg = f"删除: {task.name}?"
        if is_current:
            msg += "\n(当前进行中的任务)"

        tk.Label(f, text=msg,
                 font=('Microsoft YaHei', 10, 'bold'),
                 bg=t["cream"], fg=t["black"]).pack(pady=(_S(0), _S(10)))

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
                           highlightthickness=2, highlightbackground=t["black"], padx=_S(12), pady=_S(4))
        yes_btn.pack(side='left', padx=(_S(0), _S(10)))
        yes_btn.bind('<Button-1>', lambda e: do_delete())
        yes_btn.bind('<Enter>', lambda e: yes_btn.config(bg=t["black"], fg=t["yellow"]))
        yes_btn.bind('<Leave>', lambda e: yes_btn.config(bg=t["red"], fg=t["white"]))

        no_btn = tk.Label(btn_row, text="取消",
                          font=('Microsoft YaHei', 9, 'bold'),
                          bg=t["white"], fg=t["black"], cursor='hand2',
                          highlightthickness=2, highlightbackground=t["black"], padx=_S(12), pady=_S(4))
        no_btn.pack(side='left')
        no_btn.bind('<Button-1>', lambda e: d.destroy())
        no_btn.bind('<Enter>', lambda e: no_btn.config(bg=t["yellow"], fg=t["black"]))
        no_btn.bind('<Leave>', lambda e: no_btn.config(bg=t["white"], fg=t["black"]))

        d.bind('<Escape>', lambda e: d.destroy())

        _place_overlay(d, self.win, _S(220), _S(90))
        _win_set_topmost(d, True)
        try:
            d.grab_set()
        except Exception:
            pass

    def _manage_keywords(self, task):
        """任务关键词管理面板：展示已学关键词+权重，可勾选删除。"""
        t = theme()
        d = tk.Toplevel(self.win)
        d.withdraw()
        d.overrideredirect(True)
        d.configure(bg=t["cream"])
        d.attributes('-topmost', True)

        outer = tk.Frame(d, bg=t["cream"], highlightthickness=3,
                         highlightbackground=t["black"])
        outer.pack(fill='both', expand=True)

        hdr = tk.Frame(outer, bg=t["yellow"], padx=_S(12), pady=_S(8))
        hdr.pack(fill='x')
        tk.Label(hdr, text=f"关键词: {task.name[:16]}",
                 font=('Microsoft YaHei', 10, 'bold'),
                 bg=t["yellow"], fg=t["black"], cursor='fleur').pack(side='left')
        close_lbl = tk.Label(hdr, text="×", font=('JetBrains Mono', 11, 'bold'),
                             bg=t["red"], fg=t["white"], padx=_S(6), cursor='hand2')
        close_lbl.pack(side='right')
        close_lbl.bind('<Button-1>', lambda e: d.destroy())

        body = tk.Frame(outer, bg=t["cream"], padx=_S(10), pady=_S(8))
        body.pack(fill='both', expand=True)

        list_canvas = ScaledCanvas(body, bg=t["cream"], highlightthickness=0, bd=0)
        list_scroll = CyberScrollbar(body, list_canvas.yview)
        list_frame = tk.Frame(list_canvas, bg=t["cream"])
        list_win = list_canvas.create_window((0, 0), window=list_frame, anchor='nw', width=280)
        list_canvas.configure(yscrollcommand=list_scroll.set)
        list_frame.bind('<Configure>',
                        lambda e: list_canvas.configure(scrollregion=list_canvas.bbox('all')))
        list_canvas.bind('<Configure>',
                         lambda e: list_canvas.itemconfigure(list_win, width=e.width - _S(4)))
        list_canvas.pack(side='left', fill='both', expand=True)
        list_scroll.pack(side='right', fill='y', padx=(_S(4), _S(0)))

        def _on_wheel(e):
            list_canvas.yview_scroll(int(-1 * (e.delta / 120)), 'units')
            return 'break'
        list_canvas.bind('<MouseWheel>', _on_wheel)
        list_frame.bind('<MouseWheel>', _on_wheel)

        def _refresh():
            for w in list_frame.winfo_children():
                w.destroy()
            rows = self.db.get_top_keywords(task.id, 60)
            if not rows:
                tk.Label(list_frame, text="尚未学习到关键词",
                         font=('Microsoft YaHei', 9),
                         bg=t["cream"], fg=t["muted"]).pack(pady=_S(20))
                return
            for term, weight in rows:
                row = tk.Frame(list_frame, bg=t["white"],
                               highlightthickness=1, highlightbackground=t["black"])
                row.pack(fill='x', pady=(_S(0), _S(4)))
                tk.Label(row, text=term,
                         font=('Microsoft YaHei', 9),
                         bg=t["white"], fg=t["black"],
                         anchor='w', padx=_S(8), pady=_S(4)).pack(side='left', fill='x', expand=True)
                tk.Label(row, text=f"×{int(weight)}",
                         font=('JetBrains Mono', 8),
                         bg=t["white"], fg=t["muted"], padx=_S(4)).pack(side='left')
                x_btn = tk.Label(row, text="×",
                                 font=('JetBrains Mono', 10, 'bold'),
                                 bg=t["white"], fg=t["red"], padx=_S(8),
                                 cursor='hand2')
                x_btn.pack(side='right')
                x_btn.bind('<Button-1>',
                           lambda e, tm=term: (self.db.delete_task_keyword(task.id, tm),
                                                self._sync_current_task_keywords(task.id),
                                                _refresh()))
                x_btn.bind('<Enter>', lambda e, b=x_btn: b.config(bg=t["red"], fg=t["white"]))
                x_btn.bind('<Leave>', lambda e, b=x_btn: b.config(bg=t["white"], fg=t["red"]))
                row.bind('<MouseWheel>', _on_wheel, add='+')

        _refresh()

        _bind_title_drag(d, hdr)
        _place_overlay(d, self.win, _S(320), _S(360))
        _win_set_topmost(d, True)
        try:
            d.grab_set()
        except Exception:
            pass
        d.bind('<Escape>', lambda e: d.destroy())

    def _sync_current_task_keywords(self, task_id):
        """删词后若正是当前进行任务，热更新 self.tracker.current_task.keywords。"""
        try:
            new_kw = self.db.sync_task_keywords_field(task_id)
            if (self.tracker.current_task
                    and self.tracker.current_task.id == task_id):
                self.tracker.current_task.keywords = new_kw
            for tsk in self.tracker.today_tasks:
                if tsk.id == task_id:
                    tsk.keywords = new_kw
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

    def __init__(self, tracker):
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

        # 先按目标屏 DPI 计算全局缩放,再算窗口尺寸,避免用 _SCALE=1.0 设几何后被截断
        probe_x = cx if cx >= 0 else sw - 100
        probe_y = cy if cy >= 0 else 80
        _apply_ui_scale(self.root, probe_x, probe_y)
        self._last_dpi_scale = _SCALE

        # 多屏 clamp: 用虚拟桌面而非主屏,允许 B 屏的坐标(可能为负)
        vs = _virtual_screen()
        if vs:
            vx, vy, vw, vh = vs
            if cx < 0 or cy < 0:
                cx = vx + vw - _S(self.WIDTH) - _S(24)
                cy = vy + _S(80)
            cx = max(vx, min(cx, vx + vw - _S(self.WIDTH)))
            cy = max(vy, min(cy, vy + vh - _S(self.HEIGHT)))
        else:
            if cx < 0 or cy < 0:
                cx = sw - _S(self.WIDTH) - _S(24)
                cy = _S(80)
            cx = max(0, min(cx, sw - _S(self.WIDTH)))
            cy = max(0, min(cy, sh - _S(self.HEIGHT)))
        self.root.geometry(f'{_S(self.WIDTH)}x{_S(self.HEIGHT)}+{cx}+{cy}')

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
        """拖拽松手回调：保存位置，检测跨屏 DPI 变化并按需重建 UI。"""
        self._save_position()
        # 跨屏 DPI 变化检测：拖到另一块不同缩放比的屏上时重建 UI
        try:
            # overrideredirect 窗口的 winfo_x/y 常不可靠，用 winfo_rootx/y 取真实屏幕坐标
            rx = self.root.winfo_rootx() + 20
            ry = self.root.winfo_rooty() + 20
            new_scale = _dpi_scale_for_point(rx, ry)
            if abs(new_scale - self._last_dpi_scale) > 0.15:
                _apply_ui_scale(self.root, rx, ry)
                self._last_dpi_scale = _SCALE
                self._rebuild_for_dpi()
                print(f"[DPI] 跨屏重建 UI，缩放系数 {self._last_dpi_scale:.2f}")
        except Exception:
            pass
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
            gy, gh = _S(80), _S(self.HEIGHT)
        sx = mx if self._snap_edge == 'left' else mx + mw - _S(self._strip_w)
        if self._reveal_strip is None or not self._reveal_strip.winfo_exists():
            strip = tk.Toplevel(self.root)
            strip.overrideredirect(True)
            strip.attributes('-topmost', True)
            cv = ScaledCanvas(strip, width=self._strip_w, height=gh,
                           highlightthickness=0, bd=0)
            cv.pack(fill='both', expand=True)
            strip.bind('<Enter>', lambda e: self._reveal())
            cv.bind('<Enter>', lambda e: self._reveal())
            self._reveal_strip = strip
            self._reveal_canvas = cv
        self._reveal_strip.geometry(f'{_S(self._strip_w)}x{gh}+{sx}+{gy}')
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
            # ScaledCanvas 的 _create 会再次 _S() 缩放坐标，这里取逻辑像素（不预缩放），
            # 避免与 winfo_height() 返回的物理像素叠加成双重缩放，导致 teal 填充画到 canvas 外。
            phys_h = self._reveal_strip.winfo_height() or _S(self.HEIGHT)
            h = phys_h / _SCALE if _SCALE else phys_h
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
            gy, gw, gh = _S(80), _S(self.WIDTH), _S(self.HEIGHT)
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

    def _rebuild_for_dpi(self):
        """跨屏拖拽到不同 DPI 屏时重建窗口：缩放已通过 _apply_ui_scale 更新，
        此处只需重算窗口尺寸并重建画布，让 ScaledCanvas 取到新的 _SCALE。"""
        try:
            # overrideredirect 窗口 winfo_x/y 不可靠，改用 winfo_rootx/y 取真实屏幕坐标
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
        except Exception:
            return
        # 多屏 clamp：用虚拟桌面而非主屏，避免把 B 屏坐标拉回主屏
        vs = _virtual_screen()
        if vs:
            vx, vy, vw, vh = vs
            x = max(vx, min(x, vx + vw - _S(self.WIDTH)))
            y = max(vy, min(y, vy + vh - _S(self.HEIGHT)))
        else:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            x = max(0, min(x, sw - _S(self.WIDTH)))
            y = max(0, min(y, sh - _S(self.HEIGHT)))
        self.root.geometry(f'{_S(self.WIDTH)}x{_S(self.HEIGHT)}+{x}+{y}')
        if hasattr(self, 'panel') and self.panel:
            try:
                self.panel.destroy()
            except Exception:
                pass
        self._build_ui()

    def _build_ui(self):
        t = self.theme()
        self.panel = tk.Frame(self.root, bg=t["black"], highlightthickness=0)
        self.panel.pack(fill='both', expand=True)

        self.canvas = ScaledCanvas(self.panel, width=self.WIDTH, height=self.HEIGHT,
                                bg=t["black"], highlightthickness=0, bd=0)
        self.canvas.pack(fill='both', expand=True)

        self.canvas.config(cursor='fleur')
        self.canvas.bind('<Button-1>', self._handle_canvas_click, add='+')
        self._render_static()

    def _handle_canvas_click(self, event):
        if event.x >= _S(self.WIDTH) - _S(48) and _S(12) <= event.y <= _S(34):
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

        # 工作状态切换菜单：根据 work_session 状态决定显示哪些项
        ws = self.tracker.work_session
        work_items = []
        if ws == 'pending' or ws == 'ended':
            work_items.append(("开始今日工作", self._start_today_work))
        elif ws == 'active':
            work_items.append(("暂停", self._pause_today_work))
            work_items.append(("结束今日工作", self._end_today_work))
        elif ws == 'paused':
            work_items.append(("继续", self._resume_today_work))
            work_items.append(("结束今日工作", self._end_today_work))

        items = []
        for it in work_items:
            items.append(it)
        items.append(None)
        items.append(("切换任务", self._switch_task))
        items.append(None)
        items.append(("今日复盘", self._generate_today_review))
        items.append(("统计", self._open_stats))
        items.append(("任务列表", self._open_task_manager))
        items.append(None)
        items.append(("设置", self._open_settings))
        items.append(None)
        items.append(("退出", self._do_quit))

        row_h = _S(34)
        for it in items:
            if it is None:
                sep = tk.Frame(bd, bg=t["black"], height=_S(3))
                sep.pack(fill='x', padx=_S(10), pady=_S(4))
                continue
            label, cb = it
            lab = tk.Label(bd, text=label, font=('Microsoft YaHei', 9, 'bold'),
                           bg=t["white"], fg=t["black"], anchor='w', padx=_S(14), pady=_S(6),
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
        rx = cx + _S(self.WIDTH) - _S(146)
        ry = cy + _S(38)
        total_h = bd.winfo_reqheight() + _S(8)
        vs = _virtual_screen()
        if vs:
            vx, vy, vw, vh = vs
            rx = max(vx, min(rx, vx + vw - _S(146)))
            ry = max(vy, min(ry, vy + vh - total_h))
        m.geometry(f'{_S(146)}x{total_h}+{rx}+{ry}')
        m.deiconify()
        m.update_idletasks()
        # overrideredirect 窗口首次映射常忽略位置落到 (0,0)，映射后再设一次才稳
        m.geometry(f'{_S(146)}x{total_h}+{rx}+{ry}')

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

    def _start_today_work(self):
        self.tracker._start_work_day()

    def _end_today_work(self):
        self.tracker._end_work_day()

    def _pause_today_work(self):
        self.tracker._pause_work_day()

    def _resume_today_work(self):
        self.tracker._resume_work_day()

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
        self.tracker.auto_end_threshold = config.get("auto_end_threshold", 3600)
        self.tracker.auto_start_new_day = config.get("auto_start_new_day", False)
        # 邀请码 / endpoint 改动后让 ArkClient 立即生效，无需重启
        self.tracker.ai_enabled = config.get("ai_enabled", True)
        self.tracker.body_send = config.get("body_send", True)
        self.tracker.ark = ArkClient(config)
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

        # 工作日状态机：pending（未开始）/ active（进行中）/ ended（已结束）
        self.work_session = 'pending'
        self.work_date = datetime.now().date()  # 当前工作日所属日期，跨日时触发滚动
        self._idle_continuous_seconds = 0       # active 态下连续无操作累计（用于自动结束判定）

        # 从配置加载参数
        self.check_interval = config["check_interval"]
        self.idle_threshold = config["idle_threshold"]
        self.reminder_interval = config["reminder_interval"]
        self.lock_check_interval = config["lock_check_interval"]
        self.no_input_threshold = config.get("no_input_threshold", 180)
        self.auto_end_threshold = config.get("auto_end_threshold", 3600)
        self.auto_start_new_day = config.get("auto_start_new_day", False)

        # 线程
        self.track_thread = None
        self.lock_check_thread = None
        self.last_reminder_time = 0
        self.deviation_start_time = 0  # 任务偏离开始时间
        # maybe_drift 累计流：连续多次拿不准也提醒一次（避免长时间静默）
        self._maybe_drift_streak = 0
        self.MAYBE_DRIFT_STREAK_LIMIT = 5  # 连续 N 次 maybe_drift 升级弹窗一次

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
        # auto_start_track=True 视作"启动即开始今日工作"；否则保持 pending 等用户主动开始
        self.work_session = 'active'
        # 启动即视为"刚提醒过"，避免 track_loop 第一次循环就因 last_reminder_time=0 立刻补弹
        # _prompt_select_task 关闭后若用户没选任务，也会再刷新一次，给一个完整 reminder_interval 喘息
        self.last_reminder_time = time.time()
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
        self._dialog_active = True
        try:
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
            # 用户关掉弹窗但没选任务：刷新 last_reminder_time，给一个完整 reminder_interval 喘息，
            # 否则 track_loop 会在下个周期立刻补弹第二个"任务确认"对话框
            if not result and not self.current_task:
                self.last_reminder_time = time.time()
        finally:
            self._dialog_active = False

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
                # 跨日滚动：日期变化即重置今日状态机
                if datetime.now().date() != self.work_date:
                    self._day_rollover()
                    time.sleep(self.check_interval)
                    continue

                # ended 态：静默轮询，不判定、不打扰、不写活动
                if self.work_session == 'ended':
                    time.sleep(self.check_interval)
                    continue

                # paused 态：静默轮询，不判定、不打扰、不写活动（用户主动暂停）
                if self.work_session == 'paused':
                    time.sleep(self.check_interval)
                    continue

                # pending 态：等用户主动开始；开启跨日自动开始且检测到操作则进入 active
                if self.work_session == 'pending':
                    if self.auto_start_new_day:
                        idle_time = WindowTracker.get_idle_time()
                        if idle_time < self.idle_threshold and self.today_tasks:
                            self._start_work_day()
                    time.sleep(self.check_interval)
                    continue

                # === 以下为 active 态原有逻辑 ===
                if WindowTracker.is_screen_locked():
                    if self.current_activity and not self.current_activity.is_locked:
                        self._end_activity()
                        self._start_activity(Task(id='locked', name='🔒 锁屏/离开'), is_locked=True)
                    # 锁屏期间也累计连续无操作，触发自动结束
                    self._idle_continuous_seconds += self.check_interval
                    if self._idle_continuous_seconds >= self.auto_end_threshold:
                        self._end_work_day()
                        continue
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
                    # 连续无操作达到自动结束阈值 -> 今日工作结束
                    self._idle_continuous_seconds += self.check_interval
                    if self._idle_continuous_seconds >= self.auto_end_threshold:
                        self._end_work_day()
                        continue
                    time.sleep(self.check_interval)
                    continue
                # 用户回到工位，清空连续无操作累计
                self._idle_continuous_seconds = 0
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
                        self._maybe_drift_streak = 0
                        if self.deviation_start_time == 0:
                            self.deviation_start_time = time.time()
                        elif time.time() - self.deviation_start_time >= self.reminder_interval:
                            # 偏离持续超过容忍阈值，弹窗确认
                            self.deviation_start_time = 0
                            self.last_reminder_time = time.time()
                            self._ask_task_confirmation(window_info)
                    elif relation == 'maybe_drift':
                        # 拿不准不打扰，但连续 N 次也升级弹窗一次，避免长时间静默漂移
                        self._maybe_drift_streak += 1
                        self.deviation_start_time = 0
                        self.is_deviating = False
                        if self._maybe_drift_streak >= self.MAYBE_DRIFT_STREAK_LIMIT:
                            self._maybe_drift_streak = 0
                            self.last_reminder_time = time.time()
                            self._ask_task_confirmation(window_info)
                    else:
                        # related：确认在做，重置所有偏离信号
                        self.deviation_start_time = 0
                        self.is_deviating = False
                        self._maybe_drift_streak = 0
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
                # 非 active 态不写 locked 活动，避免 ended/pending 态污染当日数据
                if self.work_session != 'active':
                    time.sleep(self.lock_check_interval)
                    continue
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
            url, body,
            description=getattr(self.current_task, 'description', '') or '')
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

    def _day_rollover(self):
        """跨日滚动：落库当前活动、清空今日累计统计、重置状态机到 pending。
        无锁、无对话框，纯状态重置。"""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 跨日滚动 -> 重置今日状态")
        if self.current_activity:
            self._end_activity()
        self.today_no_input_seconds = 0
        self.deviation_start_time = 0
        self._maybe_drift_streak = 0
        self.is_deviating = False
        self._idle_continuous_seconds = 0
        self.current_task = None
        self.work_date = datetime.now().date()
        self.work_session = 'pending'
        self.today_tasks = self.db.get_tasks()
        if self.auto_start_new_day and self.today_tasks:
            self._start_work_day()

    def _start_work_day(self):
        """开始今日工作：从 pending/ended 切到 active，弹任务选择。
        若跨日未处理会先走一次 _day_rollover。"""
        today = datetime.now().date()
        if today != self.work_date:
            self._day_rollover()
            return
        if self.work_session == 'active':
            return
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始今日工作")
        self.work_session = 'active'
        self._idle_continuous_seconds = 0
        if not self.today_tasks:
            self._show_task_planning()
        elif not self.current_task and self.panel:
            self.panel.root.after(300, self._prompt_select_task)

    def _end_work_day(self):
        """结束今日工作：落库当前活动、切到 ended、弹今日复盘。
        进入 ended 态后 _track_loop 仅静默轮询，不再做偏离判定或弹任务确认。"""
        if self.work_session == 'ended':
            return
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 今日工作结束")
        if self.current_activity:
            self._end_activity()
        self.work_session = 'ended'
        self.deviation_start_time = 0
        self._maybe_drift_streak = 0
        self.is_deviating = False
        self.current_task = None
        if self.panel:
            self.panel.root.after(300, self.panel._generate_today_review)

    def _pause_work_day(self):
        """暂停今日工作：落库当前活动、切到 paused。
        进入 paused 态后 _track_loop 仅静默轮询，不判定、不打扰、不写活动。"""
        if self.work_session != 'active':
            return
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 今日工作已暂停")
        if self.current_activity:
            self._end_activity()
        self.work_session = 'paused'
        self.deviation_start_time = 0
        self._maybe_drift_streak = 0
        self.is_deviating = False
        self.current_task = None

    def _resume_work_day(self):
        """继续今日工作：从 paused 切回 active，弹任务选择。"""
        if self.work_session != 'paused':
            return
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 今日工作继续")
        self.work_session = 'active'
        self._idle_continuous_seconds = 0
        if self.today_tasks and not self.current_task and self.panel:
            self.panel.root.after(300, self._prompt_select_task)
    
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

    def generate_html_report(self, date_str: str = None) -> str:
        """生成单个 HTML 复盘文件，内嵌所有有数据日期 + 今日的数据，浏览器内 JS 渲染。
        支持日期选择器直接跳转任意日期、prev/next 逐日切换；切到没数据的日期显示"无数据"页面但仍可切换。
        """
        import json as _json

        today_str = datetime.now().strftime('%Y-%m-%d')
        target = date_str or today_str

        available = self.db.get_available_dates(limit=60)
        all_dates = sorted(set(available) | {today_str}, reverse=True)

        review_data = {}
        for ds in all_dates:
            try:
                review_data[ds] = self._collect_review_data(ds)
            except Exception as e:
                print(f"收集复盘数据失败 {ds}: {e}")

        path = os.path.join(SCRIPT_DIR, "worktrace_review.html")
        html_doc = self._build_review_html_doc(target, review_data, all_dates, today_str)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(html_doc)
        return path

    def _collect_review_data(self, date_str: str) -> dict:
        """收集某日期的复盘数据，用于嵌入 JSON。"""
        activities = self.db.get_date_activities(date_str)
        task_summary = self._summarize_activities(activities)
        total_sec = sum(task_summary.values())
        focus_sec = sum(v for k, v in task_summary.items() if not self._is_non_work_task(k))
        away_sec = total_sec - focus_sec
        top_task = max(task_summary.items(), key=lambda x: x[1])[0] if task_summary else "暂无"
        efficiency = int(focus_sec / total_sec * 100) if total_sec else 0

        task_rows = []
        for name, duration in sorted(task_summary.items(), key=lambda x: x[1], reverse=True):
            percent = int(duration / total_sec * 100) if total_sec else 0
            task_rows.append({
                "name": name,
                "duration_str": self._fmt_duration(duration),
                "percent": percent,
            })

        timeline_rows = []
        now = datetime.now()
        for act in activities:
            try:
                start = datetime.fromisoformat(act.start_time)
                end = datetime.fromisoformat(act.end_time) if act.end_time else now
            except Exception:
                continue
            timeline_rows.append({
                "start": start.strftime('%H:%M'),
                "end": end.strftime('%H:%M'),
                "task_name": act.task_name,
                "duration_str": self._fmt_duration(act.duration),
                "app_name": act.app_name or "",
                "title": act.window_title or act.app_name or "",
            })

        # 休息对比：期望 = 完成专注单元数 × rest_per_pomodoro；实际 = away_sec（广义非工作）
        unit_sec = max(1, config.get("pomodoro_minutes", 30)) * 60
        completed_pomos = focus_sec // unit_sec
        rest_per_sec = max(1, config.get("rest_per_pomodoro", 5)) * 60
        expected_rest_sec = completed_pomos * rest_per_sec
        rest_diff_sec = away_sec - expected_rest_sec
        rest_threshold = 10 * 60
        if abs(rest_diff_sec) <= rest_threshold:
            rest_status = "正常"
        elif rest_diff_sec < 0:
            rest_status = "偏少，建议主动休息"
        else:
            rest_status = "偏多"

        return {
            "date": date_str,
            "has_data": bool(activities),
            "total_sec": total_sec,
            "focus_sec": focus_sec,
            "away_sec": away_sec,
            "total_str": self._fmt_duration(total_sec),
            "focus_str": self._fmt_duration(focus_sec),
            "away_str": self._fmt_duration(away_sec),
            "top_task": top_task,
            "efficiency": efficiency,
            "task_rows": task_rows,
            "timeline_rows": timeline_rows,
            "completed_pomos": completed_pomos,
            "rest_expected_sec": expected_rest_sec,
            "rest_expected_str": self._fmt_duration(expected_rest_sec),
            "rest_diff_sec": rest_diff_sec,
            "rest_diff_str": ("+" if rest_diff_sec >= 0 else "-") + self._fmt_duration(abs(rest_diff_sec)),
            "rest_status": rest_status,
        }

    def _build_review_html_doc(self, target_date: str, review_data: dict,
                                all_dates: list, today_str: str) -> str:
        """构建单 HTML 文件，内嵌 JSON 数据，JS 渲染。"""
        import json as _json

        now = datetime.now()
        generated_at = now.strftime('%Y-%m-%d %H:%M')

        data_json = _json.dumps(review_data, ensure_ascii=False).replace('</', r'<\/')
        dates_json = _json.dumps(all_dates, ensure_ascii=False)

        html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>WorkTrace 工作复盘</title>
<style>
body {{ margin:0; background:#f4f6fb; color:#111827; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif; }}
.container {{ max-width:1080px; margin:0 auto; padding:32px; }}
.hero {{ background:linear-gradient(135deg,#111827,#374151); color:white; border-radius:28px; padding:32px; box-shadow:0 24px 60px rgba(17,24,39,.22); }}
.hero p {{ color:#d1d5db; margin:8px 0 0; }}
.nav-row {{ display:flex; align-items:center; gap:12px; margin:18px 0 0; flex-wrap:wrap; }}
.nav-btn {{ display:inline-block; padding:10px 18px; background:rgba(255,255,255,.12); color:#fff; border:1px solid rgba(255,255,255,.25); border-radius:14px; text-decoration:none; font-weight:600; transition:background .2s; cursor:pointer; font-size:14px; font-family:inherit; }}
.nav-btn:hover:not(:disabled) {{ background:rgba(255,255,255,.22); }}
.nav-btn:disabled {{ opacity:.35; cursor:not-allowed; }}
.date-picker {{ padding:8px 12px; background:rgba(255,255,255,.18); color:#fff; border:1px solid rgba(255,255,255,.25); border-radius:14px; font-weight:600; font-size:14px; font-family:inherit; flex:0 0 auto; cursor:pointer; }}
.date-picker:hover {{ background:rgba(255,255,255,.28); }}
.cal-popup {{ position:absolute; z-index:999; background:white; color:#111827; border-radius:16px; box-shadow:0 18px 48px rgba(15,23,42,.25); padding:14px; width:260px; font-size:13px; }}
.cal-popup.hidden {{ display:none; }}
.cal-header {{ display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; }}
.cal-header button {{ background:#f3f4f6; border:0; border-radius:8px; padding:4px 10px; cursor:pointer; font-weight:700; color:#111827; font-family:inherit; }}
.cal-header button:hover {{ background:#e5e7eb; }}
.cal-header .title {{ font-weight:700; }}
.cal-grid {{ display:grid; grid-template-columns:repeat(7,1fr); gap:2px; }}
.cal-dow {{ text-align:center; color:#9ca3af; font-size:11px; padding:4px 0; }}
.cal-cell {{ text-align:center; padding:6px 0; cursor:pointer; border-radius:8px; position:relative; font-weight:600; }}
.cal-cell:hover {{ background:#eff6ff; }}
.cal-cell.muted {{ color:#d1d5db; cursor:default; }}
.cal-cell.muted:hover {{ background:transparent; }}
.cal-cell.today {{ background:#2563eb; color:white; }}
.cal-cell.today:hover {{ background:#1d4ed8; }}
.cal-dot {{ width:4px; height:4px; border-radius:50%; margin:2px auto 0; }}
.cal-dot.blue {{ background:#2563eb; }}
.cal-dot.gray {{ background:#d1d5db; }}
.cal-dot.none {{ background:transparent; }}
.grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin:22px 0; }}
.card {{ background:white; border-radius:22px; padding:22px; box-shadow:0 14px 36px rgba(15,23,42,.08); }}
.card .label {{ color:#6b7280; font-size:13px; }}
.card .value {{ font-size:28px; font-weight:800; margin-top:8px; }}
.rest-compare {{ display:grid; grid-template-columns:repeat(5,1fr); gap:12px; margin-top:8px; }}
.rest-item {{ background:white; border-radius:18px; padding:16px; box-shadow:0 8px 24px rgba(15,23,42,.06); text-align:center; }}
.rest-item .label {{ color:#6b7280; font-size:12px; }}
.rest-item .value {{ font-size:20px; font-weight:800; margin-top:6px; }}
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
.empty {{ text-align:center; padding:60px 20px; color:#9ca3af; }}
.empty h2 {{ font-size:18px; margin:0 0 8px; color:#6b7280; }}
.empty p {{ margin:6px 0; }}
@media (max-width:800px) {{ .grid,.diagnosis,.rest-compare {{ grid-template-columns:1fr; }} .timeline-item {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<div class="container">
  <div class="hero">
    <h1 id="title">WorkTrace 工作复盘</h1>
    <p id="meta"></p>
    <div class="nav-row">
      <button class="nav-btn" id="prev-btn" type="button">← <span id="prev-label">前一天</span></button>
      <button class="date-picker" id="date-picker-btn" type="button">{target_date}</button>
      <button class="nav-btn" id="next-btn" type="button"><span id="next-label">后一天</span> -></button>
    </div>
    <div id="cal-popup" class="cal-popup hidden"></div>
  </div>
  <div id="content"></div>
</div>
<script>
const REVIEW_DATA = {data_json};
const ALL_DATES = {dates_json};
const TODAY = "{today_str}";
const GENERATED_AT = "{generated_at}";

function escapeHtml(s) {{
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
}}

function pad(n) {{ return n < 10 ? '0' + n : '' + n; }}
function fmtDate(y, m, d) {{ return y + '-' + pad(m) + '-' + pad(d); }}
function parseDate(s) {{
  const p = s.split('-').map(x => parseInt(x, 10));
  return {{ y: p[0], m: p[1], d: p[2] }};
}}
function shiftDate(s, deltaDays) {{
  const {{ y, m, d }} = parseDate(s);
  const dt = new Date(y, m - 1, d);
  dt.setDate(dt.getDate() + deltaDays);
  return fmtDate(dt.getFullYear(), dt.getMonth() + 1, dt.getDate());
}}
const EARLIEST = shiftDate(TODAY, -60);

function findAdjacent(date) {{
  const older = shiftDate(date, -1);
  const newer = shiftDate(date, +1);
  return {{
    older: (older >= EARLIEST) ? older : null,
    newer: (newer <= TODAY) ? newer : null,
  }};
}}

const DATA_DATE_SET = new Set(ALL_DATES);
let calViewYear = null, calViewMonth = null;
let calOpen = false;

function renderCalendar(viewDate) {{
  const {{ y, m }} = parseDate(viewDate);
  calViewYear = y; calViewMonth = m;
  const popup = document.getElementById('cal-popup');
  const monthNames = ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];
  const firstDay = new Date(y, m - 1, 1);
  const firstWd = (firstDay.getDay() + 6) % 7;  // 周一=0
  const daysInMonth = new Date(y, m, 0).getDate();
  const prevMonthDays = new Date(y, m - 1, 0).getDate();

  let html = '<div class="cal-header">';
  html += '<button id="cal-prev" type="button">&lt;</button>';
  html += '<span class="title">' + y + '年' + monthNames[m - 1] + '</span>';
  html += '<button id="cal-next" type="button">&gt;</button>';
  html += '</div>';
  html += '<div class="cal-grid">';
  ['一','二','三','四','五','六','日'].forEach(d => {{ html += '<div class="cal-dow">' + d + '</div>'; }});
  for (let i = 0; i < 42; i++) {{
    let day, ds, isCur;
    if (i < firstWd) {{
      day = prevMonthDays - firstWd + i + 1;
      const pm = m === 1 ? 12 : m - 1;
      const py = m === 1 ? y - 1 : y;
      ds = fmtDate(py, pm, day);
      isCur = false;
    }} else if (i < firstWd + daysInMonth) {{
      day = i - firstWd + 1;
      ds = fmtDate(y, m, day);
      isCur = true;
    }} else {{
      day = i - firstWd - daysInMonth + 1;
      const nm = m === 12 ? 1 : m + 1;
      const ny = m === 12 ? y + 1 : y;
      ds = fmtDate(ny, nm, day);
      isCur = false;
    }}
    if (!isCur) {{
      html += '<div class="cal-cell muted">' + day + '</div>';
    }} else {{
      const isToday = (ds === TODAY);
      const isFuture = (ds > TODAY);
      const hasData = DATA_DATE_SET.has(ds);
      let dotClass = 'none';
      if (!isFuture) dotClass = hasData ? 'blue' : 'gray';
      const cls = 'cal-cell' + (isToday ? ' today' : '');
      html += '<div class="' + cls + '" data-date="' + ds + '">' + day + '<div class="cal-dot ' + dotClass + '"></div></div>';
    }}
  }}
  html += '</div>';
  popup.innerHTML = html;
  document.getElementById('cal-prev').onclick = function() {{
    const nm = calViewMonth === 1 ? 12 : calViewMonth - 1;
    const ny = calViewMonth === 1 ? calViewYear - 1 : calViewYear;
    renderCalendar(fmtDate(ny, nm, 1));
  }};
  document.getElementById('cal-next').onclick = function() {{
    const nm = calViewMonth === 12 ? 1 : calViewMonth + 1;
    const ny = calViewMonth === 12 ? calViewYear + 1 : calViewYear;
    renderCalendar(fmtDate(ny, nm, 1));
  }};
  Array.prototype.forEach.call(popup.querySelectorAll('.cal-cell[data-date]'), function(el) {{
    el.onclick = function() {{
      const ds = el.getAttribute('data-date');
      render(ds);
      closeCalendar();
    }};
  }});
}}

function openCalendar(currentDate) {{
  const popup = document.getElementById('cal-popup');
  const btn = document.getElementById('date-picker-btn');
  renderCalendar(currentDate);
  const rect = btn.getBoundingClientRect();
  popup.style.left = rect.left + 'px';
  popup.style.top = (rect.bottom + 6) + 'px';
  popup.classList.remove('hidden');
  calOpen = true;
}}

function closeCalendar() {{
  document.getElementById('cal-popup').classList.add('hidden');
  calOpen = false;
}}

function render(date) {{
  currentDate = date;
  const data = REVIEW_DATA[date];
  const title = document.getElementById('title');
  const meta = document.getElementById('meta');
  const content = document.getElementById('content');
  const datePickerBtn = document.getElementById('date-picker-btn');
  const prevBtn = document.getElementById('prev-btn');
  const prevLabel = document.getElementById('prev-label');
  const nextBtn = document.getElementById('next-btn');
  const nextLabel = document.getElementById('next-label');

  datePickerBtn.textContent = date;
  // 如果日历开着，刷新它的月份到当前日期
  if (calOpen) renderCalendar(date);

  const isToday = (date === TODAY);
  title.textContent = isToday ? "WorkTrace 今日复盘" : "WorkTrace 工作复盘";

  const adj = findAdjacent(date);
  if (adj.older) {{
    prevBtn.disabled = false;
    prevBtn.onclick = () => render(adj.older);
    prevLabel.textContent = adj.older;
  }} else {{
    prevBtn.disabled = true;
    prevBtn.onclick = null;
    prevLabel.textContent = '已到最早';
  }}
  if (adj.newer) {{
    nextBtn.disabled = false;
    nextBtn.onclick = () => render(adj.newer);
    nextLabel.textContent = adj.newer === TODAY ? '今日复盘' : adj.newer;
  }} else {{
    nextBtn.disabled = true;
    nextBtn.onclick = null;
    nextLabel.textContent = '已是最新';
  }}

  const parts = date.split('-');
  const dateText = parts[0] + '年' + parseInt(parts[1], 10) + '月' + parseInt(parts[2], 10) + '日';
  meta.textContent = dateText + ' 复盘 · ' + GENERATED_AT + ' 生成 · 本地报告';

  if (!data || !data.has_data) {{
    content.innerHTML =
      '<div class="section empty">' +
      '<h2>无数据</h2>' +
      '<p>' + escapeHtml(date) + ' 没有工作轨迹记录。</p>' +
      '<p style="margin-top:8px;font-size:13px;">可通过上方日期选择器或前后按钮切换其他日期。</p>' +
      '</div>';
    return;
  }}

  let diagnosis, suggestion;
  if (data.away_sec > data.focus_sec) {{
    diagnosis = date + ' 离开、休息或未归类时间偏多，需要检查是否有漏记任务。';
    suggestion = '次日可以先列出 3 个重点任务，减少未归类时间。';
  }} else if (data.task_rows.length >= 6) {{
    diagnosis = date + ' 任务切换较多，可能存在碎片化工作。';
    suggestion = '次日可以把相近任务合并，优先保证大块时间。';
  }} else {{
    diagnosis = date + ' 主要投入在“' + data.top_task + '”，整体记录比较集中。';
    suggestion = '次日可以继续保持，结束工作后及时生成复盘。';
  }}

  const taskRowsHtml = data.task_rows.length ? data.task_rows.map(function(r) {{
    return '<div class="task-row">' +
      '<div class="task-main"><span>' + escapeHtml(r.name) + '</span><strong>' + escapeHtml(r.duration_str) + '</strong></div>' +
      '<div class="bar"><div style="width:' + r.percent + '%"></div></div>' +
      '</div>';
  }}).join('') : '<p>暂无任务记录</p>';

  const timelineHtml = data.timeline_rows.length ? data.timeline_rows.map(function(r) {{
    return '<div class="timeline-item">' +
      '<div class="time">' + escapeHtml(r.start) + ' - ' + escapeHtml(r.end) + '</div>' +
      '<div class="content">' +
      '<strong>' + escapeHtml(r.task_name) + '</strong>' +
      '<span>' + escapeHtml(r.duration_str) + ' · ' + escapeHtml(r.app_name) + '</span>' +
      '<p>' + escapeHtml(r.title) + '</p>' +
      '</div></div>';
  }}).join('') : '<p>暂无时间线记录</p>';

  const restStatusColor = data.rest_status === '正常' ? '#10b981'
    : (data.rest_status.indexOf('偏少') >= 0 ? '#ef4444' : '#f59e0b');

  content.innerHTML =
    '<div class="grid">' +
    '<div class="card"><div class="label">总记录时长</div><div class="value">' + escapeHtml(data.total_str) + '</div></div>' +
    '<div class="card"><div class="label">有效工作时长</div><div class="value">' + escapeHtml(data.focus_str) + '</div></div>' +
    '<div class="card"><div class="label">离开/休息时长</div><div class="value">' + escapeHtml(data.away_str) + '</div></div>' +
    '<div class="card"><div class="label">有效工作占比</div><div class="value">' + data.efficiency + '%</div></div>' +
    '</div>' +
    '<div class="section"><h2>休息对比</h2>' +
    '<div class="rest-compare">' +
    '<div class="rest-item"><div class="label">完成专注单元</div><div class="value">' + data.completed_pomos + ' 轮</div></div>' +
    '<div class="rest-item"><div class="label">期望休息</div><div class="value">' + escapeHtml(data.rest_expected_str) + '</div></div>' +
    '<div class="rest-item"><div class="label">实际休息</div><div class="value">' + escapeHtml(data.away_str) + '</div></div>' +
    '<div class="rest-item"><div class="label">偏差</div><div class="value">' + escapeHtml(data.rest_diff_str) + '</div></div>' +
    '<div class="rest-item"><div class="label">状态</div><div class="value" style="color:' + restStatusColor + '">' + escapeHtml(data.rest_status) + '</div></div>' +
    '</div></div>' +
    '<div class="section"><h2>汇总与诊断</h2><div class="diagnosis">' +
    '<div class="note"><strong>一句话总结</strong><br>' + escapeHtml(date) + ' 主要投入在“' + escapeHtml(data.top_task) + '”，有效工作时间 ' + escapeHtml(data.focus_str) + '。</div>' +
    '<div class="note"><strong>AI 诊断占位</strong><br>' + escapeHtml(diagnosis) + '<br><br><strong>次日建议：</strong>' + escapeHtml(suggestion) + '</div>' +
    '</div></div>' +
    '<div class="section"><h2>任务时间排行</h2>' + taskRowsHtml + '</div>' +
    '<div class="section"><h2>时间线</h2>' + timelineHtml + '</div>';
}}

let currentDate = "{target_date}";
document.getElementById('date-picker-btn').addEventListener('click', function(e) {{
  e.stopPropagation();
  if (calOpen) closeCalendar(); else openCalendar(currentDate);
}});

document.addEventListener('click', function(e) {{
  if (!calOpen) return;
  const popup = document.getElementById('cal-popup');
  if (!popup.contains(e.target) && e.target.id !== 'date-picker-btn') {{
    closeCalendar();
  }}
}});

document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape' && calOpen) closeCalendar();
}});

render(currentDate);
</script>
</body>
</html>"""
        return html_doc

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

    def get_date_completed_pomodoros(self, date_str: str) -> int:
        """指定日期完成的专注单元数 = 当日有效工作时长 ÷ 专注单元时长"""
        unit = max(1, config.get("pomodoro_minutes", 30)) * 60
        focus_total = 0
        try:
            activities = self.db.get_date_activities(date_str)
            for act in activities:
                if not act.is_idle and not act.is_locked and not self._is_non_work_task(act.task_name):
                    focus_total += act.duration
        except Exception:
            pass
        return focus_total // unit

    def compute_rest_comparison(self, date_str: str) -> dict:
        """计算指定日期的休息对比数据。
        - 期望休息 = 完成的专注单元数 × rest_per_pomodoro 分钟
        - 实际休息 = 任务名含「休息/离开/锁屏」的活动时长合计（广义）
        - 偏差阈值 ±10 分钟判定为"正常"
        """
        activities = self.db.get_date_activities(date_str)
        task_summary = self._summarize_activities(activities)
        actual_rest_sec = sum(v for k, v in task_summary.items() if self._is_non_work_task(k))
        completed_pomos = self.get_date_completed_pomodoros(date_str)
        rest_per = max(1, config.get("rest_per_pomodoro", 5)) * 60
        expected_rest_sec = completed_pomos * rest_per
        diff_sec = actual_rest_sec - expected_rest_sec
        threshold = 10 * 60
        if abs(diff_sec) <= threshold:
            status = "正常"
        elif diff_sec < 0:
            status = "偏少"
        else:
            status = "偏多"
        return {
            "completed_pomos": completed_pomos,
            "expected_rest_sec": expected_rest_sec,
            "actual_rest_sec": actual_rest_sec,
            "diff_sec": diff_sec,
            "status": status,
        }

    def get_no_input_seconds(self) -> int:
        return self.today_no_input_seconds


def _prompt_invite_code(parent, tracker, on_done=None):
    """启动时若 ark_api_key 为空，弹出 Memphis 风格 Modal 让用户输入邀请码。
    用户选"不用 AI"则同步关闭 ai_enabled；选"确定"则保存邀请码并重建 ArkClient。
    on_done(no_ai: bool) 在动作完成后回调（用于继续后续启动流程）。"""
    t = theme()
    win = tk.Toplevel(parent)
    win.title("邀请码")
    win.overrideredirect(True)
    win.configure(bg=t["cream"])
    win.attributes('-topmost', True)
    win.update_idletasks()
    w, h = _S(360), _S(260)
    _place_centered(win, parent, w, h)

    bd = tk.Frame(win, bg=t["cream"], highlightthickness=4,
                  highlightbackground=t["black"])
    bd.pack(fill='both', expand=True)

    # 标题栏 - 与 SettingsWindow 一致
    hdr = tk.Frame(bd, bg=t["yellow"], highlightthickness=0)
    hdr.pack(fill='x')
    title_box = tk.Frame(hdr, bg=t["yellow"], padx=_S(12), pady=_S(10))
    title_box.pack(side='left', fill='x', expand=True)
    rivet = ScaledCanvas(title_box, width=11, height=11, bg=t["yellow"], highlightthickness=0)
    rivet.pack(side='left', padx=(_S(0), _S(8)))
    rivet.create_oval(1, 1, 10, 10, fill=t["black"], outline='')
    tk.Label(title_box, text="邀请码", font=('JetBrains Mono', 14, 'bold'),
             bg=t["yellow"], fg=t["black"], anchor='w').pack(side='left')

    result = {"done": False, "no_ai": False}

    def _close(no_ai: bool):
        if result["done"]:
            return
        result["done"] = True
        result["no_ai"] = no_ai
        try:
            win.destroy()
        except Exception:
            pass
        if on_done:
            on_done(no_ai)

    close_lbl = tk.Label(hdr, text="×", font=('JetBrains Mono', 13, 'bold'),
                         bg=t["red"], fg=t["white"], width=3, cursor='hand2')
    close_lbl.pack(side='right', fill='y')
    close_lbl.bind('<Button-1>', lambda e: _close(True))
    close_lbl.bind('<Enter>', lambda e, l=close_lbl: l.config(bg=t["black"], fg=t["yellow"]))
    close_lbl.bind('<Leave>', lambda e, l=close_lbl: l.config(bg=t["red"], fg=t["white"]))

    tk.Frame(bd, bg=t["black"], height=_S(4)).pack(fill='x')

    # 正文
    body = tk.Frame(bd, bg=t["cream"], padx=_S(18), pady=_S(16))
    body.pack(fill='both', expand=True)
    tk.Label(body, text="输入你拿到的邀请码以启用 AI 内容感知偏离判定。",
             font=('Microsoft YaHei', 9, 'bold'),
             bg=t["cream"], fg=t["black"], wraplength=_S(300), justify='left',
             anchor='w').pack(fill='x', pady=(_S(0), _S(4)))
    tk.Label(body, text="没有邀请码？也可选\"不用 AI\"继续使用其余功能。\n之后可在 设置 → AI 判定 中随时输入或修改邀请码。",
             font=('Microsoft YaHei', 8),
             bg=t["cream"], fg=t["ink_3"], wraplength=_S(300), justify='left',
             anchor='w').pack(fill='x', pady=(_S(0), _S(10)))

    var = tk.StringVar(value="")
    entry = tk.Entry(body, textvariable=var, font=('JetBrains Mono', 10, 'bold'),
                    bg=t["white"], fg=t["black"],
                    insertbackground=t["teal"],
                    relief='flat', bd=0,
                    highlightthickness=2, highlightbackground=t["black"])
    entry.pack(fill='x', ipady=_S(6), pady=(_S(0), _S(8)))
    entry.focus_set()

    def _confirm():
        code = var.get().strip()
        if not code:
            return
        config["ark_api_key"] = code
        config["invite_prompted"] = True
        save_config(config)
        tracker.ark = ArkClient(config)
        tracker.ai_enabled = True
        _close(False)

    entry.bind('<Return>', lambda e: _confirm())
    entry.bind('<Escape>', lambda e: _close(True))

    # 底部按钮
    btn_row = tk.Frame(bd, bg=t["cream"], padx=_S(12), pady=_S(12))
    btn_row.pack(fill='x')
    right = tk.Frame(btn_row, bg=t["cream"])
    right.pack(side='right')

    skip_btn = tk.Label(btn_row, text="不用 AI", font=('Microsoft YaHei', 9, 'bold'),
                        bg=t["white"], fg=t["black"], padx=_S(14), pady=_S(6),
                        relief='flat', cursor='hand2',
                        highlightthickness=3, highlightbackground=t["black"])
    skip_btn.pack(side='left')
    skip_btn.bind('<Button-1>', lambda e: _close_no_ai())
    skip_btn.bind('<Enter>', lambda e, b=skip_btn: b.config(bg=t["yellow"], fg=t["black"]))
    skip_btn.bind('<Leave>', lambda e, b=skip_btn: b.config(bg=t["white"], fg=t["black"]))

    def _close_no_ai():
        config["ai_enabled"] = False
        config["body_send"] = False
        config["invite_prompted"] = True
        save_config(config)
        tracker.ai_enabled = False
        tracker.body_send = False
        _close(True)

    ok_btn = tk.Label(right, text="确定", font=('Microsoft YaHei', 9, 'bold'),
                      bg=t["teal"], fg=t["black"], padx=_S(14), pady=_S(6),
                      relief='flat', cursor='hand2',
                      highlightthickness=3, highlightbackground=t["black"])
    ok_btn.pack(side='left')
    ok_btn.bind('<Button-1>', lambda e: _confirm())
    ok_btn.bind('<Enter>', lambda e, b=ok_btn: b.config(bg=t["yellow"], fg=t["black"]))
    ok_btn.bind('<Leave>', lambda e, b=ok_btn: b.config(bg=t["teal"], fg=t["black"]))

    _bind_title_drag(win, hdr)
    _win_set_topmost(win, True)
    return win


def main():
    print("=" * 50)
    print("[WorkTrace 工作轨迹] - 本地记录与复盘")
    print("=" * 50)

    tracker = TimeTracker()

    # 启动控制面板（主线程）
    panel = ControlPanel(tracker)

    # 给 tracker 设置 panel 引用，用于线程安全的 UI 调度
    tracker.panel = panel

    # 首次启动 / 邀请码缺失且从未引导过时，弹一次引导窗
    if not config.get("ark_api_key") and not config.get("invite_prompted"):
        def _show_invite():
            _prompt_invite_code(panel.root, tracker)
        panel.root.after(600, _show_invite)

    # 启动后自动记录（在 panel 创建后，确保 UI 可用）
    if config.get("auto_start_track", True):
        tracker.start()

    panel.run()


if __name__ == '__main__':
    main()
