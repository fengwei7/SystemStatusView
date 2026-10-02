"""页面绘制。

屏幕只有 128x64 且是单色，所以布局按固定像素网格来排：
顶栏 14px，下面 4 行每行 12px。

Render 出来的都是 PIL 的 1 位图，既能直接推给屏幕，也能导出 PNG 预览。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .metrics import TEMP_LABELS, Snapshot, format_uptime

WIDTH, HEIGHT = 128, 64

DEJAVU_DIR = Path("/usr/share/fonts/truetype/dejavu")
# Noto Sans CJK 是个 ttc 集合，索引 2 是简体中文那一个
CJK_TTC = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
CJK_INDEX_SC = 2

HEADER_H = 14
ROW_H = 12
ROW_TOP = 15

# 温度页的显示顺序，照着板子上的核心排
TEMP_ORDER = ["SOC", "BIG0", "BIG1", "LITT", "CTR", "GPU", "NPU"]


def _load(path: Path, size: int, index: int = 0) -> ImageFont.FreeTypeFont | None:
    if not path.exists():
        return None
    try:
        return ImageFont.truetype(str(path), size, index=index)
    except OSError:
        return None


class Renderer:
    """把一个 Snapshot 画成 1 位黑白图。"""

    def __init__(self, title: str = "ROCK 5B"):
        self.title = title

        # 顶栏可能同时有中文和数字，用 CJK 字体统一处理
        self.f_header = _load(CJK_TTC, 11, CJK_INDEX_SC) or _load(
            DEJAVU_DIR / "DejaVuSans-Bold.ttf", 10
        )
        self.f_label = _load(DEJAVU_DIR / "DejaVuSans-Bold.ttf", 10)
        self.f_value = _load(DEJAVU_DIR / "DejaVuSans.ttf", 10)
        self.f_ip = _load(DEJAVU_DIR / "DejaVuSans.ttf", 12)
        self.f_tiny = _load(DEJAVU_DIR / "DejaVuSans.ttf", 9)

        for name in ("f_label", "f_value", "f_ip", "f_tiny"):
            if getattr(self, name) is None:
                setattr(self, name, ImageFont.load_default())

    # ---- 基础元件 ----

    def _new_image(self) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        img = Image.new("1", (WIDTH, HEIGHT), 0)
        return img, ImageDraw.Draw(img)

    def _header(self, draw: ImageDraw.ImageDraw, left: str, right: str = "") -> None:
        """顶部反白标题栏。"""
        draw.rectangle([0, 0, WIDTH - 1, HEADER_H - 1], fill=1)
        draw.text((3, 1), left, font=self.f_header, fill=0)
        if right:
            w = draw.textlength(right, font=self.f_header)
            draw.text((WIDTH - 3 - w, 1), right, font=self.f_header, fill=0)

    def _bar(self, draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int,
             percent: float) -> None:
        percent = max(0.0, min(100.0, percent))
        draw.rectangle([x, y, x + w - 1, y + h - 1], outline=1)
        inner = int((w - 2) * percent / 100)
        if inner > 0:
            draw.rectangle([x + 1, y + 1, x + inner, y + h - 2], fill=1)

    def _metric_row(self, draw: ImageDraw.ImageDraw, y: int, label: str,
                    percent: float) -> None:
        """一行「标签 + 进度条 + 百分比」，CPU 和内存共用。"""
        draw.text((2, y), label, font=self.f_label, fill=1)
        pct = f"{percent:.0f}%"
        pw = draw.textlength(pct, font=self.f_value)
        bar_x = 32
        bar_w = WIDTH - bar_x - pw - 6
        self._bar(draw, bar_x, y + 1, bar_w, 9, percent)
        draw.text((WIDTH - 2 - pw, y), pct, font=self.f_value, fill=1)

    # ---- 页面 ----

    def overview(self, snap: Snapshot) -> Image.Image:
        img, draw = self._new_image()

        temp = f"{snap.temp_soc:.1f}°C" if snap.temp_soc is not None else "--.-°C"
        self._header(draw, self.title, temp)

        self._metric_row(draw, ROW_TOP, "CPU", snap.cpu_percent)
        self._metric_row(draw, ROW_TOP + ROW_H, "MEM", snap.mem_percent)

        # 主 IP 居中
        y = ROW_TOP + ROW_H * 2 + 1
        ip = self._primary_ip(snap)
        if ip:
            w = draw.textlength(ip, font=self.f_ip)
            draw.text(((WIDTH - w) / 2, y - 1), ip, font=self.f_ip, fill=1)
        else:
            self._centered(draw, "无网络连接", y, self.f_value)

        # 页脚：负载 + 运行时间
        y = HEIGHT - ROW_H - 1
        draw.line([0, y, WIDTH - 1, y], fill=1)
        draw.text((2, y + 1), f"LOAD {snap.load[0]:.2f}", font=self.f_tiny, fill=1)
        up = f"UP {format_uptime(snap.uptime_s)}"
        uw = draw.textlength(up, font=self.f_tiny)
        draw.text((WIDTH - 2 - uw, y + 1), up, font=self.f_tiny, fill=1)

        return img

    def _centered(self, draw: ImageDraw.ImageDraw, text: str, y: int,
                  font: ImageFont.FreeTypeFont) -> None:
        w = draw.textlength(text, font=font)
        draw.text(((WIDTH - w) / 2, y), text, font=font, fill=1)

    def _primary_ip(self, snap: Snapshot) -> str | None:
        # 优先默认出口网卡，没有就退回第一个有地址的
        for iface in snap.net:
            if iface.is_default and iface.address:
                return iface.address
        for iface in snap.net:
            if iface.address:
                return iface.address
        return None

    def thermal(self, snap: Snapshot) -> Image.Image:
        img, draw = self._new_image()

        fan = f"FAN {snap.fan_duty}%" if snap.fan_duty is not None else ""
        self._header(draw, "温度 / 风扇", fan)

        # 按 TEMP_ORDER 排序，未知传感器接在后面
        entries: list[tuple[str, float]] = []
        for short in TEMP_ORDER:
            for full, value in snap.temps.items():
                if TEMP_LABELS.get(full) == short:
                    entries.append((short, value))
        known = {s for s, _ in entries}
        for full, value in snap.temps.items():
            short = TEMP_LABELS.get(full, full[:6].upper())
            if short not in known:
                entries.append((short, value))

        if not entries:
            self._centered(draw, "无温度传感器", 28, self.f_value)
            return img

        # 两列网格，值统一左对齐，避免右边那列的标签贴上来
        half = (len(entries) + 1) // 2
        for index, (label, value) in enumerate(entries):
            col, row = divmod(index, half)
            x = 2 + col * 64
            y = ROW_TOP + row * ROW_H
            draw.text((x, y), label, font=self.f_label, fill=1)
            draw.text((x + 30, y), f"{value:.1f}", font=self.f_value, fill=1)

        return img

    def network(self, snap: Snapshot) -> Image.Image:
        img, draw = self._new_image()

        freq = f"{snap.cpu_freq_mhz:.0f}MHz" if snap.cpu_freq_mhz else ""
        self._header(draw, "网络", freq)

        ifaces = snap.net[:4]
        if not ifaces:
            self._centered(draw, "无已连接网卡", 28, self.f_value)
            return img

        for row, iface in enumerate(ifaces):
            y = ROW_TOP + row * ROW_H
            # 默认出口网卡左边点一个实心小方块做标记
            x = 2
            if iface.is_default:
                draw.rectangle([0, y + 4, 2, y + 6], fill=1)
                x = 6
            draw.text((x, y), iface.label[:5], font=self.f_label, fill=1)

            value = iface.address or "未连接"
            # 标签加地址实在放不下就换小一号字体，别让它们挤在一起
            if x + draw.textlength(iface.label[:5], font=self.f_label) + \
                    draw.textlength(value, font=self.f_value) > WIDTH - 4:
                font = self.f_tiny
            else:
                font = self.f_value
            w = draw.textlength(value, font=font)
            draw.text((WIDTH - 2 - w, y), value, font=font, fill=1)

        return img

    # ---- 分发 ----

    def render(self, page: str, snap: Snapshot) -> Image.Image:
        fn = getattr(self, page, None)
        if page.startswith("_") or not callable(fn):
            raise ValueError(f"未知页面：{page}")
        return fn(snap)
