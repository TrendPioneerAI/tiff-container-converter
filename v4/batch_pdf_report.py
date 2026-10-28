from __future__ import annotations

import html
import os
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ACCENT = colors.HexColor("#10A37F")
INK = colors.HexColor("#17202A")
MUTED = colors.HexColor("#667085")
BORDER = colors.HexColor("#E4E7EC")
SURFACE = colors.HexColor("#F7F8FA")
SUCCESS = colors.HexColor("#067647")
SUCCESS_BG = colors.HexColor("#ECFDF3")
ERROR = colors.HexColor("#B42318")
ERROR_BG = colors.HexColor("#FEF3F2")


def register_fonts() -> tuple[str, str]:
    candidates = [
        (Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\msyhbd.ttc")),
        (Path(r"C:\Windows\Fonts\simhei.ttf"), Path(r"C:\Windows\Fonts\simhei.ttf")),
    ]
    for regular, bold in candidates:
        if regular.is_file() and bold.is_file():
            try:
                if "BTFC-CN" not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont("BTFC-CN", str(regular), subfontIndex=0))
                if "BTFC-CN-Bold" not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont("BTFC-CN-Bold", str(bold), subfontIndex=0))
                return "BTFC-CN", "BTFC-CN-Bold"
            except Exception:
                continue
    raise RuntimeError("未找到可嵌入PDF的中文字体（需要微软雅黑或黑体）")


def _escaped(value: object) -> str:
    return html.escape(str(value or ""))


def _path_text(value: object) -> str:
    text = str(value or "-")
    # ParagraphStyle.splitLongWords handles narrow cells without inserting
    # visible or copy-affecting characters into the recorded path.
    return _escaped(text)


def _hash_text(value: object) -> str:
    text = str(value or "-")
    if len(text) > 36:
        text = text[:32] + "<br/>" + text[32:]
    return text


def generate_batch_pdf(summary: dict, output_path: Path) -> dict:
    regular_font, bold_font = register_fonts()
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "TitleCN",
        parent=styles["Title"],
        fontName=bold_font,
        fontSize=22,
        leading=30,
        textColor=INK,
        spaceAfter=3 * mm,
    )
    subtitle = ParagraphStyle(
        "SubtitleCN",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=9.5,
        leading=15,
        textColor=MUTED,
    )
    heading = ParagraphStyle(
        "HeadingCN",
        parent=styles["Heading2"],
        fontName=bold_font,
        fontSize=13,
        leading=19,
        textColor=INK,
        spaceBefore=5 * mm,
        spaceAfter=2.5 * mm,
    )
    body = ParagraphStyle(
        "BodyCN",
        parent=styles["BodyText"],
        fontName=regular_font,
        fontSize=9,
        leading=15,
        textColor=INK,
        wordWrap="CJK",
    )
    small = ParagraphStyle(
        "SmallCN",
        parent=body,
        fontSize=7.7,
        leading=11,
        textColor=MUTED,
    )
    cell = ParagraphStyle(
        "CellCN",
        parent=body,
        fontSize=7.8,
        leading=11,
        wordWrap="CJK",
    )
    cell_center = ParagraphStyle("CellCenterCN", parent=cell, alignment=TA_CENTER)
    cell_error = ParagraphStyle("CellErrorCN", parent=cell, textColor=ERROR)
    hash_style = ParagraphStyle(
        "Hash",
        parent=cell,
        fontName="Courier",
        fontSize=6.4,
        leading=8.5,
        textColor=colors.HexColor("#344054"),
    )

    total = int(summary.get("total", 0))
    passed = int(summary.get("passed", 0))
    failed = int(summary.get("failed", 0))
    overall_status = "全部成功" if failed == 0 else "部分失败"
    mode_text = "验证后原位替换" if summary.get("replace_original") else "输出到指定文件夹"
    direction_text = "普通 TIFF 转 BigTIFF" if summary.get("direction") == "to_big" else "BigTIFF 转普通 TIFF"

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title="TIFF 双向转换批次报告",
        author="TIFF 双向转换工具",
        subject="转换结果与完整性验证",
    )

    page_counter = {"count": 0}

    def header_footer(canvas, document) -> None:
        page_counter["count"] = max(page_counter["count"], canvas.getPageNumber())
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(18 * mm, 13 * mm, width - 18 * mm, 13 * mm)
        canvas.setFont(regular_font, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 8.5 * mm, "TIFF 双向转换 - 批次验证报告")
        canvas.drawRightString(width - 18 * mm, 8.5 * mm, f"第 {document.page} 页")
        canvas.restoreState()

    story = [
        Table(
            [["", Paragraph(direction_text, title)]],
            colWidths=[4 * mm, 166 * mm],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, 0), ACCENT),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (1, 0), (1, 0), 4 * mm),
                    ("RIGHTPADDING", (1, 0), (1, 0), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 1 * mm),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1 * mm),
                ]
            ),
        ),
        Paragraph("批次转换与完整性验证报告", subtitle),
        Spacer(1, 5 * mm),
    ]

    status_color = SUCCESS if failed == 0 else ERROR
    status_bg = SUCCESS_BG if failed == 0 else ERROR_BG
    summary_data = [
        [
            Paragraph("批次结论", small),
            Paragraph(f"<b>{overall_status}</b>", ParagraphStyle("Status", parent=body, textColor=status_color)),
            Paragraph("执行方式", small),
            Paragraph(mode_text, body),
        ],
        [
            Paragraph("文件总数", small),
            Paragraph(str(total), body),
            Paragraph("成功 / 失败", small),
            Paragraph(f"<font color='#067647'>{passed}</font> / <font color='#B42318'>{failed}</font>", body),
        ],
        [
            Paragraph("开始时间", small),
            Paragraph(_escaped(summary.get("started_at", "-")), body),
            Paragraph("结束时间", small),
            Paragraph(_escaped(summary.get("finished_at", "-")), body),
        ],
        [
            Paragraph("转换方向", small),
            Paragraph(direction_text, body),
            Paragraph("软件版本", small),
            Paragraph(_escaped(summary.get("tool", {}).get("version", "-")), body),
        ],
    ]
    summary_table = Table(summary_data, colWidths=[25 * mm, 57 * mm, 25 * mm, 63 * mm])
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SURFACE),
                ("BACKGROUND", (0, 0), (1, 0), status_bg),
                ("BOX", (0, 0), (-1, -1), 0.7, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 2.2 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2 * mm),
            ]
        )
    )
    story.extend([summary_table, Paragraph("处理范围", heading)])
    story.append(
        Paragraph(
            "输入位置：" + _path_text(summary.get("input_scope", "多文件选择")) + "<br/>"
            "输出/报告位置：" + _path_text(summary.get("output_scope", output_path.parent)) + "<br/>"
            "验证规则：每个文件均比较图像块SHA-256与非结构性元数据SHA-256；只有验证通过的文件才标记为成功。",
            body,
        )
    )

    story.append(Paragraph("文件处理结果", heading))
    result_rows = [
        [
            Paragraph("序号", cell_center),
            Paragraph("文件", cell_center),
            Paragraph("结果", cell_center),
            Paragraph("处理方式", cell_center),
            Paragraph("像素", cell_center),
            Paragraph("元数据", cell_center),
            Paragraph("耗时", cell_center),
        ]
    ]
    for index, result in enumerate(summary.get("results", []), start=1):
        validation = result.get("validation", {})
        report = result.get("report", {})
        result_rows.append(
            [
                Paragraph(str(index), cell_center),
                Paragraph(_escaped(Path(result.get("input", "-")).name), cell),
                Paragraph("成功" if result.get("status") == "PASS" else "失败", cell_center),
                Paragraph("替换原文件" if result.get("replaced_original") else "生成副本", cell_center),
                Paragraph("一致" if validation.get("raw_image_payload_identical") else "-", cell_center),
                Paragraph("一致" if validation.get("non_structural_metadata_identical") else "-", cell_center),
                Paragraph(f"{float(report.get('elapsed_seconds', 0)):.2f}s", cell_center),
            ]
        )
    results_table = LongTable(
        result_rows,
        colWidths=[12 * mm, 50 * mm, 18 * mm, 26 * mm, 20 * mm, 20 * mm, 20 * mm],
        repeatRows=1,
    )
    result_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAECF0")),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK),
        ("FONTNAME", (0, 0), (-1, 0), bold_font),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1.7 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 1.7 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 1.7 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.7 * mm),
    ]
    for row_index, result in enumerate(summary.get("results", []), start=1):
        bg = SUCCESS_BG if result.get("status") == "PASS" else ERROR_BG
        fg = SUCCESS if result.get("status") == "PASS" else ERROR
        result_style.extend(
            [
                ("BACKGROUND", (2, row_index), (2, row_index), bg),
                ("TEXTCOLOR", (2, row_index), (2, row_index), fg),
            ]
        )
    results_table.setStyle(TableStyle(result_style))
    story.append(results_table)

    for result in summary.get("results", []):
        for warning in result.get("warnings", []):
            story.append(Paragraph("提示：" + _path_text(warning), small))

    failures = [result for result in summary.get("results", []) if result.get("status") != "PASS"]
    if failures:
        story.append(Paragraph("异常与失败记录", heading))
        failure_rows = [
            [Paragraph("序号", cell_center), Paragraph("输入文件", cell_center), Paragraph("异常原因", cell_center)]
        ]
        for index, result in enumerate(failures, start=1):
            failure_rows.append(
                [
                    Paragraph(str(index), cell_center),
                    Paragraph(_path_text(result.get("input", "-")), cell),
                    Paragraph(_escaped(result.get("error", "未知错误")), cell_error),
                ]
            )
        failure_table = LongTable(failure_rows, colWidths=[12 * mm, 67 * mm, 87 * mm], repeatRows=1)
        failure_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), ERROR_BG),
                    ("TEXTCOLOR", (0, 0), (-1, 0), ERROR),
                    ("FONTNAME", (0, 0), (-1, 0), bold_font),
                    ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2 * mm),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2 * mm),
                    ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
                ]
            )
        )
        story.append(failure_table)

    successes = [result for result in summary.get("results", []) if result.get("status") == "PASS"]
    if successes:
        story.extend([PageBreak(), Paragraph("完整性校验明细", heading)])
        story.append(
            Paragraph(
                "下表记录转换前后均一致的图像块与非结构性元数据SHA-256。哈希值相同表示已经写入TIFF的像素、匀色结果和业务标签没有被重新计算或修改。",
                body,
            )
        )
        hash_rows = [
            [Paragraph("序号", cell_center), Paragraph("文件", cell_center), Paragraph("图像块SHA-256", cell_center), Paragraph("元数据SHA-256", cell_center)]
        ]
        for index, result in enumerate(successes, start=1):
            validation = result.get("validation", {})
            hash_rows.append(
                [
                    Paragraph(str(index), cell_center),
                    Paragraph(_escaped(Path(result.get("input", "-")).name), cell),
                    Paragraph(_hash_text(validation.get("raw_image_payload_sha256_output")), hash_style),
                    Paragraph(_hash_text(validation.get("non_structural_metadata_sha256_output")), hash_style),
                ]
            )
        hash_table = LongTable(hash_rows, colWidths=[12 * mm, 48 * mm, 53 * mm, 53 * mm], repeatRows=1)
        hash_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAECF0")),
                    ("FONTNAME", (0, 0), (-1, 0), bold_font),
                    ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 1.5 * mm),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 1.5 * mm),
                    ("TOPPADDING", (0, 0), (-1, -1), 1.7 * mm),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1.7 * mm),
                ]
            )
        )
        story.append(hash_table)

    story.extend(
        [
            Paragraph("结构变化说明", heading),
            Paragraph(
                "两种容器的文件头、IFD结构及图像块偏移需要重新生成；普通 TIFF 使用32位偏移，BigTIFF使用64位偏移。"
                "工具直接复制图像块，不重新采样、调色或压缩；不能恢复输入文件中已经缺失的标签。"
                "原位替换会保留原文件备份；异常文件记录在报告中，继续处理其他文件。",
                body,
            ),
        ]
    )

    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    if page_counter["count"] < 1 or not output_path.is_file():
        raise RuntimeError("PDF报告未生成有效页面")
    return {
        "path": str(output_path),
        "pages": page_counter["count"],
        "size_bytes": output_path.stat().st_size,
    }
