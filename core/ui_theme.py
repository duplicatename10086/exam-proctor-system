"""
白蓝主题 —— 母体/子体共用。
参考现代考试系统（Tauri/Web）风格：浅蓝底、白卡片、蓝色主按钮、答题卡色块。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ---------------- 配色 ----------------
BG = "#F2F6FF"          # 页面浅蓝底
BG_SOFT = "#E8F0FE"     # 更浅的蓝（卡片内层）
WHITE = "#FFFFFF"
PRIMARY = "#2563EB"     # 主蓝
PRIMARY_DK = "#1E40AF"  # 深蓝（标题/悬停）
PRIMARY_LT = "#DBEAFE"  # 浅蓝（边框/高亮）
ACCENT = "#3B82F6"      # 亮蓝
TEXT = "#1E293B"        # 主文字
TEXT_SUB = "#64748B"    # 次文字
BORDER = "#D6E2F5"      # 边框
OK = "#16A34A"          # 正确绿
WARN = "#DC2626"        # 警告红
NAV_BG = "#E2E8F0"      # 答题卡未答
NAV_DONE = "#93C5FD"    # 已答
NAV_CUR = "#1E40AF"     # 当前

FONT = "Microsoft YaHei UI"
FONT_ZH = "Microsoft YaHei"


def _f(size, bold=False):
    return (FONT, size, "bold") if bold else (FONT, size)


def setup(root: tk.Tk, dark_titlebar=True):
    """全局样式配置。"""
    root.configure(bg=BG)
    try:
        root.option_add("*Font", _f(10))
        root.option_add("*Background", BG)
        root.option_add("*Foreground", TEXT)
    except Exception:
        pass

    if dark_titlebar:
        try:
            # Windows 深色标题栏（blue)
            import ctypes
            hwnd = root.winfo_id()
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 35, ctypes.byref(ctypes.c_int(1)), 4)  # 1=dark
        except Exception:
            pass

    st = ttk.Style()
    try:
        st.theme_use("clam")
    except Exception:
        pass

    # ---- Frame ----
    st.configure("Card.TFrame", background=WHITE, relief="flat")
    st.configure("BG.TFrame", background=BG)
    st.configure("Header.TFrame", background=PRIMARY)
    st.configure("Nav.TFrame", background=WHITE)

    # ---- Label ----
    st.configure("TLabel", background=BG, foreground=TEXT, font=_f(10))
    st.configure("Card.TLabel", background=WHITE, foreground=TEXT, font=_f(10))
    st.configure("Sub.TLabel", background=WHITE, foreground=TEXT_SUB, font=_f(9))
    st.configure("Header.TLabel", background=PRIMARY, foreground=WHITE, font=_f(11))
    st.configure("Title.TLabel", background=PRIMARY, foreground=WHITE, font=_f(18, True))
    st.configure("SubTitle.TLabel", background=PRIMARY, foreground="#DBEAFE", font=_f(10))
    st.configure("Stem.TLabel", background=WHITE, foreground=TEXT, font=_f(12))
    st.configure("Num.TLabel", background=PRIMARY, foreground=WHITE, font=_f(20, True))
    st.configure("OK.TLabel", background=WHITE, foreground=OK, font=_f(11, True))
    st.configure("Warn.TLabel", background=WHITE, foreground=WARN, font=_f(11, True))

    # ---- Button ----
    st.configure("TButton", font=_f(10), padding=6, relief="flat")
    st.configure("Primary.TButton", background=PRIMARY, foreground=WHITE,
                 font=_f(10, True), padding=8, borderwidth=0)
    st.map("Primary.TButton", background=[("active", PRIMARY_DK), ("disabled", "#B9CCEB")])
    st.configure("Ghost.TButton", background=WHITE, foreground=PRIMARY,
                 font=_f(10), padding=6, borderwidth=1)
    st.map("Ghost.TButton", background=[("active", PRIMARY_LT)])
    st.configure("Nav.TButton", background=NAV_BG, foreground=TEXT,
                 font=_f(10, True), width=3, padding=3, borderwidth=0)
    st.map("Nav.TButton", background=[("active", NAV_DONE)])
    st.configure("NavDone.TButton", background=NAV_DONE, foreground=PRIMARY_DK,
                 font=_f(10, True), width=3, padding=3, borderwidth=0)
    st.configure("NavCur.TButton", background=NAV_CUR, foreground=WHITE,
                 font=_f(10, True), width=3, padding=3, borderwidth=0)
    st.configure("Danger.TButton", background=WARN, foreground=WHITE,
                 font=_f(10, True), padding=8, borderwidth=0)

    # ---- Entry ----
    st.configure("TEntry", fieldbackground=WHITE, foreground=TEXT,
                 bordercolor=BORDER, padding=5, relief="flat")
    st.map("TEntry", bordercolor=[("focus", PRIMARY)])
    st.configure("Card.TEntry", fieldbackground=WHITE, foreground=TEXT,
                 bordercolor=BORDER, padding=5)
    # ---- Text ----
    st.configure("Card.TText", fieldbackground=WHITE, foreground=TEXT,
                 bordercolor=BORDER, padding=6, relief="flat")
    # ---- Notebook ----
    st.configure("TNotebook", background=BG, borderwidth=0)
    st.configure("TNotebook.Tab", background=NAV_BG, foreground=TEXT_SUB,
                 font=_f(10), padding=(14, 7), borderwidth=0)
    st.map("TNotebook.Tab", background=[("selected", WHITE)],
           foreground=[("selected", PRIMARY_DK)],
           font=[("selected", _f(10, True))])
    # ---- Checkbutton / Radiobutton ----
    st.configure("TCheckbutton", background=WHITE, foreground=TEXT, font=_f(10))
    st.configure("TRadiobutton", background=WHITE, foreground=TEXT, font=_f(11))
    st.configure("Card.TRadiobutton", background=WHITE, foreground=TEXT, font=_f(11))
    st.configure("Card.TCheckbutton", background=WHITE, foreground=TEXT, font=_f(11))
    # ---- LabelFrame ----
    st.configure("TLabelframe", background=BG, foreground=PRIMARY_DK,
                 font=_f(10, True), bordercolor=BORDER)
    st.configure("TLabelframe.Label", background=BG, foreground=PRIMARY_DK, font=_f(10, True))
    st.configure("Card.TLabelframe", background=WHITE, foreground=PRIMARY_DK,
                 font=_f(10, True), bordercolor=BORDER)
    st.configure("Card.TLabelframe.Label", background=WHITE, foreground=PRIMARY_DK, font=_f(10, True))
    # ---- Treeview ----
    st.configure("Treeview", background=WHITE, fieldbackground=WHITE,
                 foreground=TEXT, font=_f(10), rowheight=28, borderwidth=0)
    st.configure("Treeview.Heading", background=PRIMARY_LT, foreground=PRIMARY_DK,
                 font=_f(10, True), relief="flat", padding=6)
    st.map("Treeview", background=[("selected", ACCENT)])
    # ---- Scrollbar ----
    st.configure("TScrollbar", background=BG_SOFT, troughcolor=BG,
                 borderwidth=0, arrowcolor=PRIMARY)


def header(root, title, subtitle="", height=72):
    """顶部蓝色标题条。"""
    f = ttk.Frame(root, style="Header.TFrame", height=height)
    f.pack(fill="x", side="top")
    f.pack_propagate(False)
    ttk.Label(f, text=title, style="Title.TLabel").pack(anchor="w", padx=20, pady=(10, 0))
    if subtitle:
        ttk.Label(f, text=subtitle, style="SubTitle.TLabel").pack(anchor="w", padx=21, pady=(2, 8))
    return f


def card(parent, **kw):
    """白色卡片 Frame。"""
    return ttk.Frame(parent, style="Card.TFrame", **kw)
