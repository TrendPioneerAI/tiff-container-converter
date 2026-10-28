# TIFF 容器转换工具：历史版本

这里保存三个历史版本的源码，适合在 Windows 10/11 64 位环境重新运行和打包。三个目录互相独立：

| 目录 | 历史功能 |
| --- | --- |
| v1 | BigTIFF 转 Classic TIFF，单文件，JSON 验证报告 |
| v2 | 批量转换、文件夹扫描、离线许可证 |
| v3 | 批量转换、原位替换选项、每批一份 PDF 报告 |

这是原始历史版本的整理快照；各自源码和功能保持原状。Classic TIFF → BigTIFF 的反向转换与免授权新版不在此历史快照内。

## 下载并运行

从 GitHub 下载源码 ZIP 或克隆仓库，安装 64 位 Python 3.12（含 tkinter），在仓库根目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe v1\BigTIFF_to_ClassicTIFF.py
.\.venv\Scripts\python.exe v2\bigtiff_to_classic.py
.\.venv\Scripts\python.exe v3\bigtiff_to_classic.py
```

其中 V2 和 V3 是原始授权版，需要可验证的许可证。源码没有包含以前的私钥和客户许可证，历史许可证若要继续使用，须由使用者自行安全保存；若从源码建立新签发体系，见 [授权重建](docs/license-rebuild.md)。这些材料不能依靠公开仓库恢复原来的私钥。

PDF 报告依赖 Windows 的微软雅黑或黑体字体。应用程序输出路径和原始输入影像由使用者选择，不应上传到公开仓库。

## 打包

安装上面的依赖后，在相应版本目录执行：

```powershell
..\.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed --onedir --contents-directory runtime --name TIFFConverter BigTIFF_to_ClassicTIFF.py
```

V2/V3 将最后的脚本名改为 `bigtiff_to_classic.py`。输出 `dist\TIFFConverter` 整个目录需一同分发；目标机器不需要另装 Python。

## 测试与边界

运行 `.\.venv\Scripts\python.exe tests\smoke.py` 会生成合成小样本，并对三个版本执行转换和字节级验证，不需要用户航摄影像。

历史版本直接复制受支持 TIFF 的图像块并验证非结构性标签；转换会重建文件头与偏移，不能保证整文件逐字节不变。受限指针结构或超出 Classic TIFF 可表达范围的输入应停止。V3 的原位替换是历史实现，网络盘、突然断电、权限及异常回滚等场景没有全面验证；生产影像建议先选择输出副本并保留独立备份。

## 开源许可

本仓库自身源码以 MIT 许可证发布；第三方 Python 依赖使用各自许可证。仓库不包含徕卡软件或厂商原始影像。
