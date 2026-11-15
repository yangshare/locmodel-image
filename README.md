# LocModel Image · ZCode 本地画图插件

一个 [ZCode](https://zcode.ai) 插件：把你本机 **LocModel** 应用内置的画图引擎接入 ZCode 会话，直接用自然语言出图、改图。

> 非官方插件。LocModel 是 NEXORA SOFTWARE 的闭源软件，本插件只是调用它在本地暴露的 HTTP 接口，不涉及任何云服务。

## 原理

LocModel 的画图内核是 stable-diffusion.cpp 的 `sd-server.exe`（llama.cpp 同源技术栈），画图模式运行时会在 `http://127.0.0.1:<动态端口>` 提供 A1111 兼容 API。本插件会：

1. 自动发现引擎端口：`LOCMODEL_PORT` 环境变量 → 进程扫描（`tasklist` + `netstat` 找 `sd-server.exe`）→ 常见端口探测
2. 通过 A1111 兼容接口（`/sdapi/v1/txt2img`、`/sdapi/v1/img2img`、`/sdapi/v1/loras`）提交生成任务
3. 把返回的 base64 图片保存到工作区 `generated-images/locmodel/` 并给出文件路径

## 提供的 MCP 工具

| 工具 | 用途 |
|---|---|
| `locmodel_status` | 检查引擎是否在运行，返回端口、当前模型、能力 |
| `locmodel_txt2img` | 文生图（prompt 必填；尺寸/步数/cfg/seed/batch 可选） |
| `locmodel_img2img` | 图生图/重绘（本地底图 + prompt + denoising_strength） |
| `locmodel_loras` | 列出引擎当前可用的 LoRA |

## 前置要求

- Windows + 已安装 [LocModel](https://locmodel.com/)，且画图模式可用（用的时候打开 LocModel → 画图模式选一个模型加载即可）
- Python 3.8+（`python` 在 PATH 中；插件 MCP server 为零第三方依赖的纯标准库实现）

## 安装（ZCode 插件市场）

1. 打开 ZCode → **插件市场** → **添加** → **添加插件市场**
2. 粘贴本仓库地址：`https://github.com/yangshare/locmodel-image`
3. 在该市场（`locmodel-image-market`）下找到 **LocModel 本地画图** → **安装**

## 使用示例

装好后在新任务里直接说：

- 用 LocModel 画一张：雪山脚下的温泉小镇，黄昏，暖色灯光
- 把 generated-images/locmodel/xxx.png 重绘成赛博朋克风格
- 列一下现在引擎里有哪些 LoRA

出图默认 1024×1024（跟随引擎当前设置）；Z-Image Turbo 这类少步模型用 `steps: 8`，普通 SD/FLUX/Qwen 模型用 20 左右。

## 已验证

- 2026-11，LocModel v3.1.0：中文提示词 txt2img / img2img 实测通过；引擎返回标准 A1111 格式（`images` base64 数组 + `info` JSON）
- 实测模型组合：`qwen_image_2.1-Q6_K.gguf` + `Qwen3VL-8B-Instruct-Q4_K_M.gguf`（文本编码器）+ Comfy-Org 分片 VAE

## 排错

- **没有找到画图引擎**：先打开 LocModel 进入画图模式并加载一个画图模型（`sd-server` 只在画图模式运行时存在），然后重试
- **引擎没有响应**：引擎重启后端口可能变化，工具会自动重新发现；也可设置环境变量 `LOCMODEL_PORT` 指定
- **接口 4xx**：LocModel 的 sd-server 是其自定义分支，个别参数可能与标准 A1111 有出入，先用最小 payload（只有 prompt）重试

## License

[MIT](LICENSE)
