---
name: locmodel-image
description: Draw or edit images with the user's local LocModel app (stable-diffusion.cpp engine). Use when the user asks to draw / generate / repaint an image locally via LocModel, mentions LocModel 画图, or wants txt2img/img2img without cloud services. Saves PNG/JPG into the workspace and returns file paths.
---

# LocModel 本地画图

本插件通过 MCP 工具调用本机 LocModel 应用内置的画图引擎（stable-diffusion.cpp 的 sd-server，地址 http://127.0.0.1:<自动发现端口>，A1111 兼容 API）。

## 何时用哪个工具

- `locmodel_status` — 不确定引擎是否在跑时先调它；返回端口与能力信息。
- `locmodel_txt2img` — 文生图。**必传 prompt**；其余可选。
- `locmodel_img2img` — 图生图/重绘。必传 prompt + image_path（本地底图路径）。
- `locmodel_loras` — 列出可用 LoRA，名字可直接出现在 prompt 里（A1111 风格 `<lora:名字:0.8>`，是否生效取决于引擎）。

## 参数要点

- 提示词支持中文（LocModel 默认模型 Z-Image Turbo 对中文友好）。
- `steps`：Z-Image Turbo 这类少步模型用 8 即可；换成普通 SD/FLUX 模型时要用 20-30。
- 尺寸：width/height 不传则用引擎当前设置。常用比例 1:1→1024x1024，9:16→768x1344，16:9→1344x768。
- `seed`：不传则随机；要复现就固定种子并保持其他参数一致。
- img2img 的 `denoising_strength`：0.3 附近轻微修饰，0.6 默认，0.8+ 大改。

## 生成结果

图片保存在 `<工作区>/generated-images/locmodel/`（可用环境变量 `LOCMODEL_OUT_DIR` 改），工具返回完整文件路径，把路径交给用户即可。

## 排错

- 实测兼容性（2026-11，LocModel v3.1.0）：`/sdapi/v1/txt2img`、`/sdapi/v1/img2img`、`/sdapi/v1/loras`、`/sdcpp/v1/capabilities` 均真实可用，响应为标准 A1111 格式（`images` base64 数组 + `info` JSON 字符串），中文提示词直接可用。引擎端口是动态的（实测某次为 8790），进程扫描可自动发现。
- 引擎状态可查：`/sdcpp/v1/capabilities` 返回当前加载的模型名（`model.name`）、运行时、采样器列表和 `limits`（宽高 64-4096，batch ≤ 8）。
- 报"没有找到 LocModel 画图引擎"：请用户打开 LocModel → 进入画图模式并选一个画图模型加载（sd-server 只在画图模式运行时存在），然后重试。
- 报"画图引擎没有响应"：引擎可能刚重启换了端口，工具会自动重新发现；仍失败则让用户检查 LocModel 是否还在画图模式。
- 端口非默认：设置环境变量 `LOCMODEL_PORT` 后重试。
- 接口报错 4xx：LocModel 的 sd-server 是其自定义分支，个别参数名可能与标准 A1111 有出入；先去掉可选参数用最小 payload（只有 prompt）重试，再把完整报错给用户看。
- 出图是空的：确认画图模型已在 LocModel 里下载/加载完成。
