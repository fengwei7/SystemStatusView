"""系统指标采集。

数据全部来自 /proc、/sys 与 psutil，不依赖外部命令，适合长期驻留运行。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

HWMON_ROOT = Path("/sys/class/hwmon")
PROC_ROUTE = Path("/proc/net/route")

# 容器/虚拟网桥的网卡名前缀，默认不显示，否则屏幕上全是它们
VIRTUAL_PREFIXES = ("docker", "br-", "veth", "virbr", "tun", "tap", "vmnet", "dummy")

# psutil 报出的传感器名 -> 界面短名。ROCK 5B 上就是这几个。
TEMP_LABELS = {
    "soc_thermal": "SOC",
    "bigcore0_thermal": "BIG0",
    "bigcore1_thermal": "BIG1",
    "littlecore_thermal": "LITT",
    "center_thermal": "CTR",
    "gpu_thermal": "GPU",
    "npu_thermal": "NPU",
}

# app.temp_source 的取值 -> 实际传感器名
TEMP_SOURCES = {
    "soc": "soc_thermal",
    "bigcore0": "bigcore0_thermal",
    "bigcore1": "bigcore1_thermal",
    "littlecore": "littlecore_thermal",
    "gpu": "gpu_thermal",
    "npu": "npu_thermal",
    "center": "center_thermal",
}


@dataclass
class NetIface:
    """一块网卡的地址与链路状态。"""

    name: str
    label: str
    address: str | None
    is_up: bool
    speed_mbps: int | None = None
    is_default: bool = False


@dataclass
class Snapshot:
    """某一时刻的完整系统状态。"""

    cpu_percent: float = 0.0
    cpu_percore: list[float] = field(default_factory=list)
    cpu_freq_mhz: float | None = None
    cpu_freq_max_mhz: float | None = None

    temp_soc: float | None = None
    temps: dict[str, float] = field(default_factory=dict)

    fan_duty: int | None = None

    mem_percent: float = 0.0
    mem_used_mb: float = 0.0
    mem_total_mb: float = 0.0
    swap_percent: float = 0.0

    load: tuple[float, float, float] = (0.0, 0.0, 0.0)
    uptime_s: float = 0.0

    net: list[NetIface] = field(default_factory=list)
    time: float = 0.0


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _find_hwmon(name: str) -> Path | None:
    """按 hwmon 的 name 字段定位目录，因为 hwmon 编号不保证固定。"""
    for entry in HWMON_ROOT.glob("hwmon*"):
        if _read_text(entry / "name") == name:
            return entry
    return None


def default_interface() -> str | None:
    """从内核路由表里找出默认出口网卡。"""
    try:
        with PROC_ROUTE.open() as fh:
            next(fh, None)  # 表头
            for line in fh:
                parts = line.split()
                # 目标是 0.0.0.0 且带 RTF_GATEWAY(0x2) 的就是默认路由
                if len(parts) > 3 and parts[1] == "00000000":
                    if int(parts[3], 16) & 0x2:
                        return parts[0]
    except (OSError, ValueError):
        pass
    return None


def format_uptime(seconds: float) -> str:
    """把秒数压成最多 6 个字符，例如 3d04h / 12h30m / 45m。"""
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d{hours:02d}h"
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


class Collector:
    """按需采集系统指标。

    温度、网卡、风扇这些 sysfs 读取比 CPU 采样慢，用 TTL 缓存减少开销，
    保证 CPU 使用率仍然按每次调用的间隔平滑计算。
    """

    def __init__(self, temp_source: str = "soc", interface_filter: list[str] | None = None,
                 aliases: dict[str, str] | None = None, hide_virtual: bool = True,
                 slow_ttl: float = 2.0):
        self.temp_source = temp_source
        self.interface_filter = interface_filter or []
        self.aliases = aliases or {}
        self.hide_virtual = hide_virtual
        self.slow_ttl = slow_ttl

        self._fan_path = None
        self._fan_checked = False

        self._slow_cache: dict = {}
        self._slow_at = 0.0

        # 首次调用返回 0，这里先垫一次，让第一次采样就有意义
        psutil.cpu_percent(interval=None)

    # ---- 单项采集 ----

    def _temps(self) -> dict[str, float]:
        try:
            raw = psutil.sensors_temperatures()
        except Exception:
            return {}
        out: dict[str, float] = {}
        for name, entries in raw.items():
            if not entries:
                continue
            out[name] = entries[0].current
        return out

    def _fan_duty(self) -> int | None:
        """板载风扇是 pwmfan，只有占空比没有转速表，所以显示 PWM 百分比。"""
        if not self._fan_checked:
            self._fan_checked = True
            self._fan_path = _find_hwmon("pwmfan")
        if self._fan_path is None:
            return None
        raw = _read_text(self._fan_path / "pwm1")
        if raw is None:
            return None
        try:
            return round(int(raw) / 255 * 100)
        except ValueError:
            return None

    def _network(self) -> list[NetIface]:
        try:
            addrs = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
        except Exception:
            return []

        primary = default_interface()
        result: list[NetIface] = []

        for name in sorted(addrs):
            if name == "lo":
                continue
            if self.interface_filter and name not in self.interface_filter:
                continue
            if self.hide_virtual and not self.interface_filter:
                if name.startswith(VIRTUAL_PREFIXES):
                    continue

            ipv4 = next(
                (a.address for a in addrs[name] if a.family.name == "AF_INET"),
                None,
            )
            stat = stats.get(name)
            is_up = bool(stat and stat.isup)

            # 没 IP 又没起来的网卡直接跳过；没 IP 但在线的保留，显示为“未连接”
            if ipv4 is None and not is_up:
                continue

            result.append(
                NetIface(
                    name=name,
                    label=self.aliases.get(name, name),
                    address=ipv4,
                    is_up=is_up,
                    speed_mbps=stat.speed if stat else None,
                    is_default=(name == primary),
                )
            )

        # 有 IP 的排前面，默认出口网卡排最前
        result.sort(key=lambda i: (not i.is_default, i.address is None, i.name))
        return result

    def _cpu_freq(self) -> tuple[float | None, float | None]:
        try:
            freq = psutil.cpu_freq()
        except Exception:
            return None, None
        if freq is None:
            return None, None
        return freq.current or None, freq.max or None

    # ---- 对外接口 ----

    def sample(self, force_slow: bool = False) -> Snapshot:
        snap = Snapshot(time=time.time())

        snap.cpu_percent = psutil.cpu_percent(interval=None)
        snap.cpu_percore = psutil.cpu_percent(interval=None, percpu=True)

        try:
            mem = psutil.virtual_memory()
            snap.mem_percent = mem.percent
            snap.mem_used_mb = mem.used / 1024 / 1024
            snap.mem_total_mb = mem.total / 1024 / 1024
            snap.swap_percent = psutil.swap_memory().percent
        except Exception:
            pass

        try:
            snap.load = psutil.getloadavg()
        except Exception:
            pass

        try:
            snap.uptime_s = time.time() - psutil.boot_time()
        except Exception:
            pass

        now = time.monotonic()
        if force_slow or (now - self._slow_at) >= self.slow_ttl:
            freq_now, freq_max = self._cpu_freq()
            self._slow_cache = {
                "temps": self._temps(),
                "fan": self._fan_duty(),
                "net": self._network(),
                "freq": freq_now,
                "freq_max": freq_max,
            }
            self._slow_at = now

        cache = self._slow_cache
        temps = cache.get("temps", {})
        snap.temps = temps
        snap.fan_duty = cache.get("fan")
        snap.net = cache.get("net", [])
        snap.cpu_freq_mhz = cache.get("freq")
        snap.cpu_freq_max_mhz = cache.get("freq_max")

        if self.temp_source == "max":
            snap.temp_soc = max(temps.values()) if temps else None
        else:
            key = TEMP_SOURCES.get(self.temp_source, "soc_thermal")
            snap.temp_soc = temps.get(key)

        return snap
