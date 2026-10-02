"""SSD1306 屏幕的发现与初始化。

排针上的 I2C 总线在 ROCK 5B 上默认未启用，而且总线号会随覆盖层配置变化，
所以这里默认自动扫描 /dev/i2c-*，找到 SSD1306 再交给 luma 驱动。
"""

from __future__ import annotations

import glob
import re
from dataclasses import dataclass

from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306
from smbus2 import SMBus

# SSD1306 常见的两个从机地址
CANDIDATE_ADDRESSES = (0x3C, 0x3D)

WIDTH = 128
HEIGHT = 64

I2C_DEV_GLOB = "/dev/i2c-*"


class DisplayNotFound(RuntimeError):
    """没有找到可用的 SSD1306。"""


@dataclass(frozen=True)
class Endpoint:
    """一条总线上发现的一个屏幕地址。"""

    bus: int
    address: int

    def __str__(self) -> str:
        return f"/dev/i2c-{self.bus} 0x{self.address:02X}"


def available_buses() -> list[int]:
    buses = []
    for path in glob.glob(I2C_DEV_GLOB):
        m = re.fullmatch(r"/dev/i2c-(\d+)", path)
        if m:
            buses.append(int(m.group(1)))
    return sorted(buses)


def probe(endpoint: Endpoint) -> bool:
    """探测从机是否应答。

    SSD1306 只写不读，没法读取器件 ID 确认型号，所以退而求其次：
    地址上有 ACK 就认为屏幕在。
    """
    try:
        with SMBus(endpoint.bus) as bus:
            bus.write_quick(endpoint.address)
        return True
    except (OSError, PermissionError):
        return False


def discover(bus: int | str = "auto", address: int | str = "auto") -> Endpoint:
    """定位屏幕。

    bus/address 传 "auto" 时自动扫描；传具体值则只验证那一个组合。
    """
    buses = available_buses()
    if not buses:
        raise DisplayNotFound(
            "系统里没有任何 /dev/i2c-* 设备，请确认 i2c-dev 模块已加载。"
        )

    if bus == "auto":
        scan_buses = buses
    else:
        scan_buses = [int(bus)]
        if int(bus) not in buses:
            raise DisplayNotFound(
                f"指定的 /dev/i2c-{bus} 不存在。可用总线：{buses}"
            )

    if address == "auto":
        scan_addrs = list(CANDIDATE_ADDRESSES)
    else:
        scan_addrs = [int(address, 0) if isinstance(address, str) else int(address)]

    for b in scan_buses:
        for a in scan_addrs:
            if probe(Endpoint(b, a)):
                return Endpoint(b, a)

    hint = (
        "未发现 SSD1306。请检查：\n"
        "  1. 接线：VCC->3.3V(引脚1)  GND->GND(引脚6)  SDA->引脚7  SCL->引脚32\n"
        "  2. 排针 I2C 覆盖层是否已启用（默认关闭），执行后重启：\n"
        "       sudo mv /boot/dtbo/rk3588-i2c8-m4.dtbo.disabled /boot/dtbo/rk3588-i2c8-m4.dtbo\n"
        f"  3. 手动扫描确认：i2cdetect -y <总线号>   当前可用总线：{buses}"
    )
    raise DisplayNotFound(hint)


class Screen:
    """对 luma 设备的一层薄封装，方便统一处理清理与亮度。"""

    def __init__(self, endpoint: Endpoint, contrast: int = 128, rotate: int = 0,
                 invert: bool = False):
        self.endpoint = endpoint
        self.rotate = int(rotate) % 360
        self.invert = bool(invert)

        serial = i2c(port=endpoint.bus, address=endpoint.address)
        # 屏幕装反时旋转 180 度。硬件方向翻转用 180，不用 luma 的水平/垂直镜像，
        # 这样画面内容和像素排列都不受影响。
        self._device = ssd1306(serial, width=WIDTH, height=HEIGHT,
                               rotate=2 if self.rotate == 180 else 0)
        self._device.contrast = max(0, min(255, int(contrast)))
        # 0xA6 = 正常显示，0xA7 = 反色
        self._device.command(0xA7 if self.invert else 0xA6)

    @property
    def device(self):
        return self._device

    @property
    def size(self) -> tuple[int, int]:
        return WIDTH, HEIGHT

    def show(self, image) -> None:
        self._device.display(image)

    def clear(self) -> None:
        self._device.clear()

    def close(self) -> None:
        try:
            self._device.cleanup()
        except Exception:
            pass
