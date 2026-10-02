# HANDOFF — SystemStatusView

交接文档。记录**当前进度、未完成事项、已验证范围**，以及排查过程中花代价才得到的结论。
安装和使用步骤见 [README.md](README.md)，本文不重复。

最后更新：2026-10-02

---

## 一、一句话状态

代码已写完并全部通过离线验证，**但从未在真实 SSD1306 硬件上跑过** ——
屏幕还没接线、排针 I2C 覆盖层也还没启用。实机验证是唯一剩下的关键步骤。

## 二、待办（按顺序）

| # | 事项 | 命令 / 说明 | 状态 |
| --- | --- | --- | --- |
| 1 | 启用排针 I2C 覆盖层 | `sudo mv /boot/dtbo/rk3588-i2c8-m4.dtbo.disabled /boot/dtbo/rk3588-i2c8-m4.dtbo && sudo reboot` | ☐ |
| 2 | 按表接线 | VCC→引脚1(3.3V)、GND→引脚6、SDA→引脚7、SCL→引脚32 | ☐ |
| 3 | 确认总线可见 | `i2cdetect -y 8`，应出现 `3c` 或 `3d` | ☐ |
| 4 | 实机运行 | `.venv/bin/python main.py -v` | ☐ |
| 5 | 视情况调参数 | `contrast` / `rotate = 180` / `invert`，见 config.toml | ☐ |
| 6 | 装开机自启 | `sudo cp systemd/ssview.service /etc/systemd/system/ && sudo systemctl enable --now ssview` | ☐ |
| 7 | 长时间运行观察 | 观察内存占用与 I2C 是否偶发 NAK | ☐ |

> 步骤 1 需要 `sudo`（当前会话拿不到密码）和**一次重启**，所以必须由人在物理机上做。

## 三、已验证 vs 未验证

### 已验证（有实证）

- **指标采集全部读到真实数据**：温度 7 路（soc 44.4°C 等）、CPU 使用率/频率
  (1520/2168MHz)、内存 (16.4%, 2618/15961MB)、负载、运行时间、网卡信息、风扇 PWM。
- **三页布局渲染正确**：均为 128x64 `mode="1"`，边界值（100% 占用、115°C、
  `255.255.255.255`、365 天运行时间、无网络、无传感器）均不溢出、不报错。
- **文本宽度实测适配**，最坏情况是网络页 `标记6 + 标签34 + IP86 = 125px < 128`，
  且已加自动降级（放不下时 IP 换小一号字体）。
- **配置链路**：加载、校验、网卡别名（`wlP2p33s0`→`WLAN`）、虚拟网卡过滤
  （docker0 / br- 已正确隐藏）均生效。
- **屏幕驱动路径**（用假串口 mock）：设备初始化为 `ssd1306 128x64 mode=1`，
  每页推送 1 帧数据，反色指令 `0xA7` 正确下发，`clear()` / `cleanup()` 正常。
- 静态检查：`compileall` 通过，`pyflakes` 无告警。

### 未验证（**风险点**）

- **真实 I2C 通讯**：从没和真的 SSD1306 通上话。地址、时序、初始化序列都是
  按 luma 库的标准实现走的，理论上没问题，但没实证。
- **对比度 / 旋转 / 反色**在真实面板上的观感 —— 这三个参数可能需要在实机上微调。
- **systemd 服务**：单元文件写好了但没装过、没跑过。
- **长时间运行稳定性**：主循环里如果 I2C 偶发 NAK，目前没有重试逻辑，
  异常会直接让进程退出（靠 systemd 的 `Restart=on-failure` 兜底）。
  如果实机上出现，考虑在外层加 try/except 重连。
- **页面刷新节奏**：`interval = 1.0s` 配合 `page_interval = 5s`，实机上是否流畅未测。

## 四、关键决策与理由

这几条都是排查后确定的，**不要按网上教程改回去**：

1. **用 I2C8_M4（引脚 7/32），不用 I2C7_M3（引脚 3/5）**
   网上教程普遍写引脚 3=SDA、5=SCL，但那是 I2C7_M3。ROCK 5B 的 I2C7 已被板载
   ES8316 音频编解码器以 **M0 引脚组**占用（设备树 `i2c@fec90000` 下挂着
   `es8316@11`，pinctrl 指向 `i2c7m0-xfer` = GPIO1_A0/A1）。要用引脚 3/5
   必须改引脚复用，**代价是板载音频失效**。I2C8_M4 则完全空闲。

2. **不显示「功率」，改显示内存/频率/风扇/负载**
   已与用户确认。ROCK 5B 没有实时电流传感器：`tcpm-source-psy-4-0022` 只是
   USB-C PD 的**协商档位**（20V/1.5A，静态值），`rk8602`/`rk8603` 只有 CPU 大核
   电压没有电流。想要真实功耗必须外接 INA219/INA260。

3. **风扇显示 PWM 占空比而不是转速**
   `pwmfan` 这个 hwmon 只暴露 `pwm1`，没有转速表，**硬件上就读不到 RPM**。

4. **渲染层与硬件层解耦**
   `pages.py` 只依赖 `Snapshot` 数据结构，不碰 I2C；`oled.py` 只管设备发现和
   初始化。好处是 `--preview` 能在无屏幕时导出 PNG —— 本次开发全程靠它验证布局，
   以后改布局也请继续用它。

5. **自动扫描总线与地址**（默认 `bus = "auto"`）
   因为 I2C8 的 `/dev/i2c-N` 编号不保证是 8，且屏幕可能是 0x3C 或 0x3D。
   探测方式是对地址发 SMBus quick 写、看有没有 ACK（SSD1306 只写不读，
   读不到器件 ID，只能这样确认）。

## 五、排查时踩过的坑（省得重来）

- **`i2c-9` / `i2c-10` 上的 `0x30` 不是屏幕**，那是 HDMI DDC 总线上的 HDMI 芯片。
  这两条是 `fde80000.hdmi` / `fdea0000.hdmi` 的 DDC，别被误导。
- **`i2cdetect` 等工具在 `/usr/sbin`，不在普通用户 PATH 里**，要写全路径
  `/usr/sbin/i2cdetect`，或 `sudo`。
- **板载 I2C 器件全是 `UU`**（被内核驱动占用，不是没接）：i2c-0=rk8602/rk8603(PMIC)、
  i2c-1=rk8602、i2c-4=fusb302、i2c-6=hym8563(RTC)、i2c-7=es8316(音频)。
- **`pypi.org` 直连超时**（本机网络环境），必须走镜像。已在 `.venv/pip.conf`
  配好清华源。
- **系统没装 `python3-venv`**，`python3 -m venv` 会因缺 `ensurepip` 失败。
  当前 `.venv` 是 `--without-pip` 建的、pip 由 `get-pip.py` 引导进去的。
  想改成标准做法：`sudo apt install python3-venv` 后重建。
- **中文标签需要 CJK 字体**。用的是
  `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc` 的 **index 2**
  （该 ttc 是字体集合，2 = Noto Sans CJK SC）。数字/英文用 DejaVu Sans。
  少了这个字体中文会变豆腐块。

## 六、文件导航

| 文件 | 职责 |
| --- | --- |
| `main.py` | 入口：CLI 参数、主循环、页面轮播、信号处理、`--preview` |
| `ssview/metrics.py` | 指标采集。`TEMP_LABELS` / `TEMP_SOURCES` 映射表在这 |
| `ssview/pages.py` | 三页布局与绘制。改界面基本只动这里 |
| `ssview/oled.py` | 总线/地址发现，`Screen` 封装 |
| `ssview/config.py` | TOML 加载与校验 |
| `config.toml` | 用户配置 |
| `systemd/ssview.service` | 开机自启（以 `radxa` 用户跑，该用户在 `i2c` 组，不需要 root）|
| `preview.png` | 三页预览图，由 `--preview` 生成 |

## 七、下次可以做的（非本次范围）

- 实机跑通后，把 `preview.png` 换成真实屏幕照片。
- 主循环加 I2C 异常重试/重连，提升长期稳定性。
- 可选：外接 INA219/INA260 后补回「功率」页。
- 可选：更多页面（磁盘占用、Docker 容器状态等）—— 在 `pages.py` 加一个方法、
  在 `config.py` 的 `KNOWN_PAGES` 注册即可。
