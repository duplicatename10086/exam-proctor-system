"""
强防作弊模块（子体用）。

设计原则（重要）：
  1. 所有能力在考生端"明示启用"：启动时弹窗告知将锁定键盘/网络并全程记录；
     可查看"已启用防护清单"，不做真正的静默行为。
  2. 崩溃/正常退出一律释放键盘钩子、恢复网络、恢复光标（atexit + try/finally）。
  3. 全程写事件日志，考生做的每一次切屏/按键尝试都有记录进报告。
  4. 断网只针对普通题；实操题可申请恢复（由母体配置允许）。

包含：UAC 提权、隐藏 PowerShell 调用、网络禁用/恢复、反虚拟机、
      全局键盘钩子 + 快捷键屏蔽、全屏置顶。
"""
from __future__ import annotations

import atexit
import ctypes
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from typing import Callable, List, Optional

IS_WIN = platform.system() == "Windows"

# ------------------------- 日志回调 -------------------------
_log_cb: Optional[Callable[[str, str], None]] = None
def set_log_callback(cb: Callable[[str, str], None]):
    global _log_cb
    _log_cb = cb

def _log(kind: str, detail: str):
    if _log_cb:
        try:
            _log_cb(kind, detail)
        except Exception:
            pass


# =====================================================================
# 1. 隐藏 PowerShell（等价原软件行为；这里仍走 subprocess，但记录命令行）
# =====================================================================
def powershell(script: str, hidden: bool = True) -> tuple[int, str, str]:
    """执行 PowerShell 脚本。hidden=True 时窗口隐藏；绕过执行策略以便脚本能跑。"""
    cmd = [
        "powershell.exe", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
    ]
    if hidden:
        cmd += ["-WindowStyle", "Hidden"]
    cmd += ["-Command", script]
    _log("powershell", "执行脚本: " + script[:120].replace("\n", " "))
    if not IS_WIN:
        return 0, "", ""
    creation = 0x08000000 if hidden else 0  # CREATE_NO_WINDOW
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           creationflags=creation, timeout=30)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        _log("powershell_err", str(e))
        return -1, "", str(e)


def open_url(url: str):
    """用默认浏览器打开 URL（等价 rundll32 url.dll,FileProtocolHandler）。"""
    _log("open_url", url)
    if IS_WIN:
        os.startfile(url)  # type: ignore[attr-defined]
    else:
        import webbrowser
        webbrowser.open(url)


# =====================================================================
# 2. UAC 提权
# =====================================================================
def is_admin() -> bool:
    if not IS_WIN:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def ensure_admin() -> bool:
    """若非管理员，弹出 UAC 提权并重启程序；返回是否已经管理员。"""
    if not IS_WIN or is_admin():
        return True
    _log("uac", "请求管理员权限")
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable,
            " ".join('"%s"' % a for a in sys.argv), None, 1)
        if int(rc) > 32:
            sys.exit(0)
    except Exception as e:
        _log("uac_cancel", str(e))
    return False


# =====================================================================
# 3. 网络禁用 / 恢复（原生 PowerShell）
# =====================================================================
_PS_DISABLE = r"""
$ErrorActionPreference='SilentlyContinue'
$a = Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -and $_.Name -notlike '*Loopback*' }
$a | Disable-NetAdapter -Confirm:$false
($a | Measure-Object).Count
"""
_PS_ENABLE = r"""
$ErrorActionPreference='SilentlyContinue'
Get-NetAdapter | Where-Object { $_.Status -eq 'Disabled' } | Enable-NetAdapter -Confirm:$false
(Get-NetAdapter | Where-Object { $_.Status -eq 'Disabled' } | Measure-Object).Count
"""
_PS_NETSTATUS = r"""
$up = (Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | Measure-Object).Count
$rt = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Measure-Object).Count
$ok = $false
if ($up -gt 0 -or $rt -gt 0) { $ok = $true }
if (-not $ok) {
  foreach ($ip in @('223.5.5.5','119.29.29.29','114.114.114.114')) {
    if (Test-Connection -ComputerName $ip -Count 1 -Quiet) { $ok=$true; break }
  }
}
if ($ok) { 'UP' } else { 'DOWN' }
"""


class NetworkLock:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.locked = False
        if enabled:
            atexit.register(self.restore)

    def disable(self) -> bool:
        if not self.enabled or not IS_WIN:
            return False
        rc, out, err = powershell(_PS_DISABLE)
        self.locked = (out.strip() not in ("", "0"))
        _log("network_disable", "已禁用 %s 个网卡" % (out.strip() or "0"))
        return self.locked

    def restore(self) -> bool:
        if not IS_WIN:
            return False
        rc, out, err = powershell(_PS_ENABLE)
        self.locked = False
        _log("network_restore", "剩余禁用网卡 %s 个" % (out.strip() or "0"))
        return True

    def status(self) -> str:
        rc, out, err = powershell(_PS_NETSTATUS)
        return out.strip() or "UNKNOWN"


# =====================================================================
# 4. 反虚拟机检测
# =====================================================================
def detect_vm() -> dict:
    """返回虚拟机检测结果 + 主机硬件摘要（等价原软件脚本）。"""
    result = {"suspected": False, "signs": [], "hardware": ""}
    if not IS_WIN:
        result["signs"].append("non-windows")
        return result
    rc, out, err = powershell(r"""
$signs=@()
foreach($k in @('SOFTWARE\VMware, Inc.\VMware Tools','SOFTWARE\Oracle\VirtualBox Guest Additions')){
  if(Test-Path $k){$signs += $k}
}
$g = Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name
$c = Get-CimInstance Win32_ComputerSystem
"SIGNS:" + ($signs -join ',')
"GPU:" + ($g -join ',')
"MODEL:" + $c.Model
"MAKER:" + $c.Manufacturer
""")
    for line in out.splitlines():
        if line.startswith("SIGNS:"):
            s = line[6:].strip()
            if s:
                result["signs"] = s.split(",")
                result["suspected"] = True
        elif line.startswith("GPU:") or line.startswith("MODEL:") or line.startswith("MAKER:"):
            result["hardware"] += line + " | "
    _log("anti_vm", "虚拟机迹象=%s 硬件=%s" % (result["signs"], result["hardware"]))
    return result


# =====================================================================
# 5. 全局键盘钩子 + 快捷键屏蔽
# =====================================================================
WH_KEYBOARD_LL = 13
VK_MAP = {
    "ESC": 0x1B, "TAB": 0x09, "LWIN": 0x5B, "RWIN": 0x5C,
    "CTRL": 0x11, "ALT": 0x12, "SHIFT": 0x10, "F1": 0x70,
    "DELETE": 0x2E, "MENU": 0x5D, "PRINTSCREEN": 0x2C,
}
BLOCK_KEYS = set(VK_MAP.values())


class KeyboardGuard:
    """低级键盘钩子：屏蔽切换窗口/系统快捷键，记录违规按键。"""
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.hooked = False
        self._hook = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._user32 = ctypes.windll.user32 if IS_WIN else None
        self._on_violation: Optional[Callable[[str], None]] = None
        atexit.register(self.unhook)

    def set_violation_callback(self, cb: Callable[[str], None]):
        self._on_violation = cb

    def _low_level_proc(self, nCode, wParam, lParam):
        if nCode >= 0 and wParam in (0x0100, 0x0104):  # KEYDOWN / SYSKEYDOWN
            kb = ctypes.cast(lParam, ctypes.POINTER(wintypes.KBDLLHOOKSTRUCT)).contents
            vk = kb.vkCode
            # Alt+Tab / Win / Ctrl+Esc / Alt+Esc / Alt+F4 / F1 等
            alt = kb.flags & 0x20 != 0 or self._key_down(0x12)
            ctrl = self._key_down(0x11)
            block = (
                vk in (VK_MAP["LWIN"], VK_MAP["RWIN"], VK_MAP["F1"], VK_MAP["PRINTSCREEN"]) or
                (alt and vk == VK_MAP["TAB"]) or
                (alt and vk in (VK_MAP["ESC"], VK_MAP["F4"])) or
                (ctrl and vk == VK_MAP["ESC"]) or
                (vk in BLOCK_KEYS and alt)
            )
            if block:
                if self._on_violation:
                    try:
                        self._on_violation("VK=0x%02X" % vk)
                    except Exception:
                        pass
                return 1  # 吞掉按键
        return self._user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

    def _key_down(self, vk: int) -> bool:
        return bool(self._user32.GetAsyncKeyState(vk) & 0x8000)

    def hook(self) -> bool:
        if not self.enabled or not IS_WIN:
            return False
        if self.hooked:
            return True
        # 钩子回调必须在有消息循环的线程里
        self._thread = threading.Thread(target=self._hook_thread, daemon=True)
        self._thread.start()
        for _ in range(50):
            if self.hooked or self._stop.is_set():
                break
            time.sleep(0.02)
        return self.hooked

    def _hook_thread(self):
        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_int,
                                      wintypes.WPARAM, wintypes.LPARAM)
        cb = HOOKPROC(self._low_level_proc)
        self._callback_ref = cb
        self._hook = self._user32.SetWindowsHookExW(WH_KEYBOARD_LL, cb, None, 0)
        self.hooked = bool(self._hook)
        _log("keyboard_hook", "已安装=%s" % self.hooked)
        msg = wintypes.MSG()
        while not self._stop.is_set():
            r = self._user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 0x0001)
            if r:
                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.01)
        if self._hook:
            self._user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        self.hooked = False

    def unhook(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self.hooked = False
        _log("keyboard_hook", "已卸载")


# =====================================================================
# 6. 全屏置顶 + 任务栏隐藏（tkinter 窗口适配）
# =====================================================================
def make_fullscreen(win, topmost: bool = True):
    """win 为 tkinter.Toplevel / Tk。"""
    try:
        win.attributes("-fullscreen", True)
        if topmost:
            win.attributes("-topmost", True)
        win.resizable(False, False)
        if IS_WIN:
            # 隐藏任务栏（SHAppBarMessage 简化：用 ShowWindow）
            h = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
            if h:
                ctypes.windll.user32.ShowWindow(h, 0)
    except Exception as e:
        _log("fullscreen_err", str(e))


def restore_taskbar():
    if not IS_WIN:
        return
    try:
        h = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
        if h:
            ctypes.windll.user32.ShowWindow(h, 5)
    except Exception:
        pass
    atexit.register(restore_taskbar)


def protect_process():
    """设置进程为关键进程（防止被任务管理器结束），仅管理员有效。"""
    if not IS_WIN or not is_admin():
        return False
    try:
        ctypes.windll.ntdll.RtlSetProcessIsCritical(1, None, 0)
        atexit.register(lambda: ctypes.windll.ntdll.RtlSetProcessIsCritical(0, None, 0))
        _log("critical", "进程已设为关键进程")
        return True
    except Exception as e:
        _log("critical_err", str(e))
        return False


# =====================================================================
# 7. 本地 AI 服务检测（防「用本地大模型查答案」）
# =====================================================================
# 常见的本地大模型服务端口（llama.cpp / Ollama / LM Studio / vLLM 等）
AI_PORTS = {8088, 11434, 1234, 8000, 8001, 5000, 5001, 7860, 11435}
# 常见本地 AI 服务进程名关键字
AI_PROCESS_KEYWORDS = (
    "llama-server", "llama.exe", "ollama", "ollama_llama_server",
    "lm studio", "vllm", "koboldcpp", "text-generation-webui",
)


def detect_local_ai() -> dict:
    """
    检测本机是否运行着本地 AI 模型服务。
    返回 {"suspected": bool, "signs": [...]}。
    检测维度：监听端口 + 进程名 + OpenAI 兼容 API 探测。
    """
    signs = []
    # --- 1) 端口监听 ---
    listening = {}
    if IS_WIN:
        try:
            out = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True, text=True, timeout=6).stdout
            for line in out.splitlines():
                if "LISTENING" not in line:
                    continue
                parts = line.split()
                if len(parts) < 5:
                    continue
                local = parts[1]
                pid = parts[-1]
                # 提取端口号
                m = re.search(r":(\d+)$", local)
                if m and int(m.group(1)) in AI_PORTS:
                    listening[m.group(1)] = pid
        except Exception:
            pass
    for port, pid in listening.items():
        signs.append("本地AI端口监听:%s(PID %s)" % (port, pid))

    # --- 2) 进程名 ---
    found_procs = set()
    if IS_WIN:
        try:
            out = subprocess.run(
                ["tasklist"], capture_output=True, text=True, timeout=8).stdout
            low = out.lower()
            for kw in AI_PROCESS_KEYWORDS:
                if kw.lower() in low:
                    found_procs.add(kw)
        except Exception:
            pass
    for kw in found_procs:
        signs.append("AI进程关键字:%s" % kw)

    # --- 3) 对命中的端口做 OpenAI API 探测（确认是真模型服务）---
    api_confirmed = []
    for port in listening:
        try:
            import urllib.request
            req = urllib.request.Request("http://127.0.0.1:%s/v1/models" % port,
                                         headers={"User-Agent": "examd"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    api_confirmed.append(port)
        except Exception:
            pass
    for port in api_confirmed:
        signs.append("OpenAI兼容API确认:端口%s" % port)

    result = {"suspected": bool(signs), "signs": signs}
    if signs:
        _log("local_ai_detected", "; ".join(signs))
    return result


class LocalAIWatcher:
    """考试期间周期性检测本地 AI 服务，发现则回调告警。"""
    def __init__(self, on_detect: Optional[Callable[[dict], None]] = None,
                 interval: float = 12.0):
        self.interval = interval
        self.on_detect = on_detect
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._reported = set()   # 去重：同一迹象只报一次

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        _log("local_ai_watcher", "本地AI监测已启动(间隔%ss)" % self.interval)

    def stop(self):
        self._stop.set()
        _log("local_ai_watcher", "本地AI监测已停止")

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                r = detect_local_ai()
                if r["suspected"]:
                    # 只对新增迹象告警
                    new = [s for s in r["signs"] if s not in self._reported]
                    for s in new:
                        self._reported.add(s)
                    if new and self.on_detect:
                        self.on_detect({"suspected": True, "signs": new})
            except Exception:
                pass
