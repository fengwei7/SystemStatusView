# SystemStatusView

在 128x64 的 SSD1306 I2C OLED 屏幕上显示 ROCK 5B 的系统状态。

> **接手这个项目？** 先看 [HANDOFF.md](HANDOFF.md)，里面有当前进度、待办事项、
> 已验证/未验证范围，以及排查时踩过的坑。

显示内容分三页自动轮播（也可固定一页）：

| 页面 | 内容 |
| --- | --- |
| `overview` | CPU 温度、CPU 使用率、内存使用率、主 IP、负载、运行时间 |
| `thermal` | 7 路温度传感器（SOC/BIG0/BIG1/LITT/CTR/GPU/NPU）、风扇 PWM 占空比、CPU 频率 |
| `network` | 各网卡名称与 IPv4 地址、CPU 当前频率 |

---

## 一、接线

ROCK 5B 排针上用 **I2C8_M4**：SDA 在引脚 7，SCL 在引脚 32。

| SSD1306 | ROCK 5B 排针 | 说明 |
| --- | --- | --- |
| VCC | 引脚 1 | 3.3V。别接 5V，排针 IO 是 3.3V 电平 |
| GND | 引脚 6 | 地 |
| SDA | 引脚 7 | I2C8_SDA_M4 |
| SCL | 引脚 32 | I2C8_SCL_M4 |

> 网上不少教程写「引脚 3=SDA / 引脚 5=SCL」，那是 I2C7_M3。ROCK 5B 的 I2C7
> 已经被板载 ES8316 音频编解码器占用，要走那组必须改引脚复用、会**禁用板载音频**，
> 所以这里不用它。

## 二、启用排针 I2C（**必做，默认是关闭的**）

ROCK 5B 出厂时排针上的 I2C 覆盖层是禁用状态，不开的话屏幕**不会有任何反应**。

```bash
sudo mv /boot/dtbo/rk3588-i2c8-m4.dtbo.disabled /boot/dtbo/rk3588-i2c8-m4.dtbo
sudo reboot
```

也可以走图形化的 `rsetup` → `Overlays` → `Enable I2C8-M4`。

重启后确认屏幕出现在总线上（I2C8 的编号不固定，通常是 8）：

```bash
i2cdetect -y 8        # 应看到 3c 或 3d
```

## 三、安装

虚拟环境已经建好（`.venv/`），依赖装在 `/home/radxa/Applications/SystemStatusView/.venv`：

```bash
cd /home/radxa/Applications/SystemStatusView
.venv/bin/pip install -r requirements.txt
```

> 系统里没装 `python3-venv`，所以 `.venv` 是用 `--without-pip` 建的、pip 由
> `get-pip.py` 引导进去。想换成标准做法可以 `sudo apt install python3-venv`
> 后重建虚拟环境。
>
> pip 已配置走清华镜像（`.venv/pip.conf`），因为 `pypi.org` 在这台机器上直连超时。

## 四、运行

```bash
cd /home/radxa/Applications/SystemStatusView
.venv/bin/python main.py
```

常用参数：

```bash
.venv/bin/python main.py --list-buses      # 列出可用 I2C 总线并逐个探测屏幕
.venv/bin/python main.py --page overview   # 固定只显示概览页，不轮播
.venv/bin/python main.py --bus 8           # 手动指定总线
.venv/bin/python main.py --preview out.png # 不接屏幕，导出各页预览图
.venv/bin/python main.py -v                # 顺带把每次刷新打到终端
```

**还没接屏幕时**，用 `--preview` 就能看布局效果：

```bash
.venv/bin/python main.py --preview preview.png
```

## 五、配置

改 `config.toml` 即可，无需动代码。

```toml
[display]
bus = "auto"            # "auto" 自动扫描，或填总线号如 8
address = "auto"        # "auto" 自动尝试 0x3C/0x3D
contrast = 128          # 亮度 0-255
rotate = 0              # 屏幕装反了就改 180
invert = false          # 黑白反转
pages = ["overview", "thermal", "network"]   # 想只留一页就删掉其余
page_interval = 5       # 每页停留秒数

[app]
title = "ROCK 5B"       # 顶栏标题，可以写中文
interval = 1.0          # 刷新间隔（秒）
temp_source = "soc"     # soc / max / bigcore0 / bigcore1 / littlecore / gpu / npu

[network]
interfaces = []         # 留空=自动；填了则只显示列出的网卡
hide_virtual = true     # 隐藏 docker0 / br- / veth 这些容器网卡
[network.aliases]
enP4p65s0 = "ETH"
wlP2p33s0 = "WLAN"
```

## 六、开机自启

```bash
sudo cp systemd/ssview.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ssview
journalctl -u ssview -f      # 看日志
```

服务以 `radxa` 用户运行即可 —— 该用户已在 `i2c` 组里，`/dev/i2c-*` 是
`root:i2c 0660`，不需要 root 权限。

---

## 关于「功率」

ROCK 5B **没有实时电流传感器**，所以本方案显示的是别的指标（内存、频率、风扇、
负载等）。板子上能读到的只有两类电压/功率相关信息，都不是真实功耗：

- `/sys/class/power_supply/tcpm-source-psy-*/` 是 USB-C PD 的**协商档位**
  （本机为 20V / 1.5A，即 30W 合同值），是个静态值，不随负载变化；
- `rk8602`/`rk8603` 是 CPU 大核的供电调压器，只暴露电压（如 0.675V），没有电流。

想要真实功耗，需要外接 INA219 / INA260 这类电流传感器到 I2C 总线，再自行读取。

## 故障排查

| 现象 | 原因与处理 |
| --- | --- |
| 提示「未发现 SSD1306」 | 排针 I2C 覆盖层没开（见第二节）；或接线松了；先 `i2cdetect -y 8` 确认能看到 `3c` |
| `i2cdetect` 显示 `UU` | 该地址已被内核驱动占用，换地址或换总线 |
| 屏幕全黑但有数据 | 调 `contrast`；有些模块需要 `invert = true` |
| 画面上下颠倒 | `rotate = 180` |
| 中文显示成方块 | 缺 `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`，装 `fonts-noto-cjk` |
| `Permission denied` 打开 I2C | 把当前用户加进 `i2c` 组：`sudo usermod -aG i2c $USER`，重新登录 |
