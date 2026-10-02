"""配置加载。

配置来自 TOML 文件，缺省字段用本模块中的默认值补齐。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"

# 可通过 --page 选择的页面名
KNOWN_PAGES = ("overview", "thermal", "network")


@dataclass
class DisplayConfig:
    bus: int | str = "auto"
    address: int | str = "auto"
    contrast: int = 128
    rotate: int = 0
    invert: bool = False
    pages: list[str] = field(default_factory=lambda: ["overview", "thermal", "network"])
    page_interval: float = 5.0

    @property
    def rotate180(self) -> bool:
        return int(self.rotate) % 360 == 180


@dataclass
class AppConfig:
    title: str = "ROCK 5B"
    interval: float = 1.0
    temp_source: str = "soc"


@dataclass
class NetworkConfig:
    interfaces: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    hide_virtual: bool = True

    def label_for(self, iface: str) -> str:
        return self.aliases.get(iface, iface)


@dataclass
class Config:
    display: DisplayConfig = field(default_factory=DisplayConfig)
    app: AppConfig = field(default_factory=AppConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = Path(path) if path else DEFAULT_CONFIG_PATH
        if not path.exists():
            return cls()

        with path.open("rb") as fh:
            raw = tomllib.load(fh)

        return cls(
            display=DisplayConfig(**raw.get("display", {})),
            app=AppConfig(**raw.get("app", {})),
            network=NetworkConfig(
                interfaces=raw.get("network", {}).get("interfaces", []),
                aliases=raw.get("network", {}).get("aliases", {}),
                hide_virtual=raw.get("network", {}).get("hide_virtual", True),
            ),
        )

    def validate(self) -> list[str]:
        """返回配置问题列表，空列表表示没有问题。"""
        problems = []
        unknown = [p for p in self.display.pages if p not in KNOWN_PAGES]
        if unknown:
            problems.append(
                f"未知页面 {unknown}，可选：{', '.join(KNOWN_PAGES)}"
            )
        if not self.display.pages:
            problems.append("display.pages 不能为空")
        if self.display.page_interval <= 0:
            problems.append("display.page_interval 必须大于 0")
        if self.app.interval <= 0:
            problems.append("app.interval 必须大于 0")
        return problems
