#!/usr/bin/env python3
"""ROCK 5B 系统状态屏。

把 CPU 温度/使用率、内存、网络 IP、CPU 频率、风扇等基础信息
显示在 128x64 的 SSD1306 I2C 屏幕上。
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

from ssview import __version__
from ssview.config import KNOWN_PAGES, Config
from ssview.metrics import Collector
from ssview.oled import DisplayNotFound, Screen, available_buses, discover
from ssview.pages import Renderer

PREVIEW_SCALE = 4  # 预览 PNG 放大倍数，方便在电脑上看清单个像素


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ssview",
        description="在 SSD1306 128x64 I2C 屏幕上显示 ROCK 5B 系统状态",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  ./main.py                        正常显示，页面自动轮播\n"
            "  ./main.py --page overview        只显示概览页，不轮播\n"
            "  ./main.py --bus 8                指定 I2C 总线 8\n"
            "  ./main.py --preview out.png      无屏幕时导出预览图\n"
            "  ./main.py --list-buses           列出可用 I2C 总线\n"
        ),
    )
    p.add_argument("-c", "--config", type=Path, default=None,
                   help="配置文件路径（默认 ./config.toml）")
    p.add_argument("-b", "--bus", default=None,
                   help="I2C 总线号，默认读配置（auto = 自动扫描）")
    p.add_argument("-a", "--address", default=None,
                   help="屏幕地址，如 0x3c，默认读配置（auto = 自动尝试）")
    p.add_argument("-p", "--page", choices=KNOWN_PAGES, default=None,
                   help="固定显示某一页，不轮播")
    p.add_argument("--preview", type=Path, metavar="PNG",
                   help="渲染成 PNG 预览图，不连接屏幕")
    p.add_argument("--list-buses", action="store_true",
                   help="列出当前可用的 I2C 总线后退出")
    p.add_argument("--contrast", type=int, default=None,
                   help="亮度 0-255，默认读配置")
    p.add_argument("-v", "--verbose", action="store_true", help="输出详细信息")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def make_collector(cfg: Config) -> Collector:
    return Collector(
        temp_source=cfg.app.temp_source,
        interface_filter=cfg.network.interfaces,
        aliases=cfg.network.aliases,
        hide_virtual=cfg.network.hide_virtual,
    )


def run_preview(cfg: Config, pages: list[str], out: Path) -> int:
    """无屏幕也能验证布局：把每页渲染成放大的 PNG。"""
    from PIL import Image

    collector = make_collector(cfg)
    renderer = Renderer(title=cfg.app.title)

    # 先睡一下让 CPU 使用率有真实的采样窗口
    collector.sample()
    time.sleep(0.6)
    snap = collector.sample(force_slow=True)

    tiles = []
    for name in pages:
        img = renderer.render(name, snap)
        tile = img.convert("L").resize(
            (img.width * PREVIEW_SCALE, img.height * PREVIEW_SCALE), Image.NEAREST
        )
        tiles.append((name, tile))

    gap = 8
    width = sum(t.width for _, t in tiles) + gap * (len(tiles) + 1)
    height = tiles[0][1].height + gap * 2
    sheet = Image.new("L", (width, height), 40)

    x = gap
    for name, tile in tiles:
        sheet.paste(tile, (x, gap))
        print(f"  页面 {name}")
        x += tile.width + gap

    sheet.save(out)
    print(f"\n预览已保存：{out}  （{len(tiles)} 页，放大 {PREVIEW_SCALE}x）")
    return 0


def run_display(cfg: Config, args: argparse.Namespace) -> int:
    bus = args.bus if args.bus is not None else cfg.display.bus
    address = args.address if args.address is not None else cfg.display.address
    contrast = args.contrast if args.contrast is not None else cfg.display.contrast

    print("正在扫描 I2C 总线寻找 SSD1306 ...", flush=True)
    endpoint = discover(bus=bus, address=address)
    print(f"已找到屏幕：{endpoint}")

    screen = Screen(endpoint, contrast=contrast, rotate=cfg.display.rotate,
                    invert=cfg.display.invert)
    collector = make_collector(cfg)
    renderer = Renderer(title=cfg.app.title)

    # 固定单页时不做轮播
    if args.page:
        pages = [args.page]
        page_interval = None
    else:
        pages = list(cfg.display.pages)
        page_interval = cfg.display.page_interval

    print(f"页面：{', '.join(pages)}"
          + (f"（每 {page_interval:g}s 切换）" if page_interval else "（固定）"))

    running = True

    def stop(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    # CPU 使用率是两次采样之间的平均值，第一次没有参考值
    collector.sample()

    page_index = 0
    page_deadline = time.monotonic() + (page_interval or 0)

    try:
        while running:
            loop_start = time.monotonic()

            snap = collector.sample()
            renderer_page = pages[page_index]
            screen.show(renderer.render(renderer_page, snap))

            if args.verbose:
                print(f"[{time.strftime('%H:%M:%S')}] {renderer_page}  "
                      f"CPU {snap.cpu_percent:.0f}%  "
                      f"{snap.temp_soc and f'{snap.temp_soc:.1f}°C'}  "
                      f"MEM {snap.mem_percent:.0f}%")

            if page_interval:
                now = time.monotonic()
                if now >= page_deadline:
                    page_index = (page_index + 1) % len(pages)
                    page_deadline = now + page_interval

            # 减去本轮耗时，保证刷新节奏稳定
            elapsed = time.monotonic() - loop_start
            time.sleep(max(0.0, cfg.app.interval - elapsed))
    finally:
        screen.clear()
        screen.close()
        print("\n已退出，屏幕已清空。")

    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_buses:
        buses = available_buses()
        print(f"可用 I2C 总线：{buses if buses else '（无）'}")
        from ssview.oled import CANDIDATE_ADDRESSES, Endpoint, probe

        for bus in buses:
            found = [f"0x{a:02X}" for a in CANDIDATE_ADDRESSES
                     if probe(Endpoint(bus, a))]
            print(f"  /dev/i2c-{bus}: 屏幕地址 {found if found else '未发现'}")
        return 0

    cfg = Config.load(args.config)
    problems = cfg.validate()
    if problems:
        for problem in problems:
            print(f"配置错误：{problem}", file=sys.stderr)
        return 2

    if args.preview:
        pages = [args.page] if args.page else list(cfg.display.pages)
        return run_preview(cfg, pages, args.preview)

    try:
        return run_display(cfg, args)
    except DisplayNotFound as exc:
        print(f"\n找不到屏幕：\n{exc}\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
