"""
pdf_generator.py - Generación de colillas de pago y reportes en PDF
"""
import os
from datetime import datetime
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle, Paragraph,
                                 Spacer, HRFlowable, Image)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY

from paths import get_reports_dir

# ─── PALETA DE COLORES ───────────────────────────────────────────────────────
NAVY     = colors.HexColor("#0F172A")
INDIGO   = colors.HexColor("#4F46E5")
INDIGO_L = colors.HexColor("#EEF2FF")
SLATE    = colors.HexColor("#475569")
SILVER   = colors.HexColor("#F1F5F9")
WHITE    = colors.white
EMERALD  = colors.HexColor("#10B981")
RED_SOFT = colors.HexColor("#FEF2F2")
RED      = colors.HexColor("#DC2626")
GOLD     = colors.HexColor("#F59E0B")


def fmt_currency(value):
    try:
        return f"$ {float(value):,.0f}".replace(",", ".")
    except Exception:
        return "$ 0"


def generar_colilla(nomina: dict, output_path: str = None, empresa_id: int = None) -> str:
    """Genera una colilla de pago individual en PDF."""
    if not output_path:
        # Sufijo por tipo para no sobrescribir otras colillas del mismo período
        q = nomina.get("quincena", 0) or 0
        sufijo = {1: "Q1", 2: "Q2", 98: "Prestacion", 99: "Contrato"}.get(q, "")
        filename = f"colilla_{nomina['empleado_nombre'].replace(' ','_')}_{nomina['periodo']}{'_'+sufijo if sufijo else ''}.pdf"
        output_path = os.path.join(get_reports_dir(empresa_id), filename)

    doc = SimpleDocTemplate(output_path, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=1.5*cm, bottomMargin=1.2*cm)

    styles = getSampleStyleSheet()
    story = []

    # ── Estilo custom ──────────────────────────────────────────────────────
    title_style = ParagraphStyle("Title", parent=styles["Normal"],
                                  fontName="Helvetica-Bold", fontSize=17,
                                  textColor=WHITE, alignment=TA_CENTER, leading=20)
    sub_style   = ParagraphStyle("Sub", parent=styles["Normal"],
                                  fontName="Helvetica", fontSize=10,
                                  textColor=colors.HexColor("#C7D2FE"), alignment=TA_CENTER)
    label_style = ParagraphStyle("Label", parent=styles["Normal"],
                                  fontName="Helvetica-Bold", fontSize=9,
                                  textColor=SLATE)
    value_style = ParagraphStyle("Value", parent=styles["Normal"],
                                  fontName="Helvetica", fontSize=10,
                                  textColor=NAVY)
    section_style = ParagraphStyle("Section", parent=styles["Normal"],
                                    fontName="Helvetica-Bold", fontSize=11,
                                    textColor=INDIGO, spaceAfter=4, spaceBefore=6)

    # ── HEADER CON LOGO ─────────────────────────────────────────────────────
    # Preparar contenido del header
    header_content = []
    
    # Añadir logo si existe
    logo_path = nomina.get('logo_path')
    if logo_path and os.path.exists(logo_path):
        try:
            logo_img = Image(logo_path, width=2*cm, height=2*cm)
            header_content.append([logo_img, Paragraph(nomina.get("empresa_nombre", "EMPRESA"), title_style)])
        except:
            header_content.append([Paragraph("", title_style), Paragraph(nomina.get("empresa_nombre", "EMPRESA"), title_style)])
    else:
        header_content.append([Paragraph("", title_style), Paragraph(nomina.get("empresa_nombre", "EMPRESA"), title_style)])

    header_table = Table(header_content, colWidths=[2*cm, 15*cm])
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), NAVY),
        ("ROUNDEDCORNERS", [8, 8, 8, 8]),
        ("TOPPADDING", (0,0), (-1,-1), 10),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING", (0,0), (-1,-1), 20),
        ("RIGHTPADDING", (0,0), (-1,-1), 20),
    ]))

    sub_header = Table([[
        Paragraph(f"NIT: {nomina.get('nit','')}  &nbsp;&nbsp;&nbsp;  {nomina.get('direccion','')}  &nbsp;&nbsp;&nbsp;  {nomina.get('telefono','')}", sub_style),
    ]], colWidths=[17*cm])
    sub_header.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), INDIGO),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))

    colilla_tag = Table([[
        Paragraph("COLILLA DE PAGO", ParagraphStyle("tag", parent=styles["Normal"],
            fontName="Helvetica-Bold", fontSize=13, textColor=INDIGO, alignment=TA_CENTER)),
        Paragraph(f"Período: {nomina.get('periodo','')}", ParagraphStyle("per", parent=styles["Normal"],
            fontName="Helvetica", fontSize=11, textColor=SLATE, alignment=TA_RIGHT)),
    ]], colWidths=[8.5*cm, 8.5*cm])
    colilla_tag.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), INDIGO_L),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING", (0,0), (-1,-1), 14),
        ("RIGHTPADDING", (0,0), (-1,-1), 14),
        ("BOX", (0,0), (-1,-1), 1, INDIGO),
    ]))

    story.extend([header_table, sub_header, Spacer(1, 0.25*cm), colilla_tag, Spacer(1, 0.3*cm)])

    # ── DATOS EMPLEADO ─────────────────────────────────────────────────────
    story.append(Paragraph("● Información del Empleado", section_style))
    emp_data = [
        [Paragraph("Nombre:", label_style), Paragraph(nomina.get("empleado_nombre",""), value_style),
         Paragraph("Documento:", label_style), Paragraph(nomina.get("documento",""), value_style)],
        [Paragraph("Cargo:", label_style), Paragraph(nomina.get("cargo",""), value_style),
         Paragraph("Edad:", label_style), Paragraph(f"{nomina.get('edad', '—')} años" if nomina.get('edad') else "—", value_style)],
        [Paragraph({"98":"Tarifa Diaria:","99":"Valor Contrato:"}.get(str(nomina.get("quincena") or 0),"Salario Base:"), label_style),
         Paragraph(fmt_currency(nomina.get("salario_base",0)), value_style),
         Paragraph("Fecha Ingreso:", label_style), Paragraph(nomina.get("fecha_ingreso","—") or "—", value_style)],
    ]
    
    # Información bancaria si está disponible
    if nomina.get("tipo_cuenta") or nomina.get("numero_cuenta"):
        emp_data.append([
            Paragraph("Tipo Cuenta:", label_style), Paragraph(nomina.get("tipo_cuenta","") or "—", value_style),
            Paragraph("N° Cuenta:", label_style), Paragraph(nomina.get("numero_cuenta","") or "—", value_style)
        ])
    
    emp_table = Table(emp_data, colWidths=[3.5*cm, 5*cm, 3.5*cm, 5*cm])
    emp_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), SILVER),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [SILVER, WHITE]),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("BOX", (0,0), (-1,-1), 1, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0,0), (-1,-1), 0.5, colors.HexColor("#E2E8F0")),
    ]))
    story.extend([emp_table, Spacer(1, 0.3*cm)])

    # ── DEVENGADOS ─────────────────────────────────────────────────────────
    story.append(Paragraph("● Devengados", section_style))
    # quincena 98 = pago por prestación de servicios (salario_base = tarifa diaria)
    # quincena 99 = liquidación de contrato (salario_base = valor total del contrato)
    quincena_tipo = nomina.get("quincena", 0) or 0
    if quincena_tipo == 98:
        concepto_lbl = "Pago por días trabajados"
        concepto_det = f"{nomina.get('dias_trabajados',0)} días × {fmt_currency(nomina.get('salario_base',0))}/día"
        concepto_val = float(nomina.get("salario_base",0)) * int(nomina.get("dias_trabajados",0))
    elif quincena_tipo == 99:
        concepto_lbl = "Liquidación de contrato"
        concepto_det = "Valor total"
        concepto_val = float(nomina.get("salario_base",0))
    else:
        concepto_lbl = "Salario proporcional"
        concepto_det = f"{nomina.get('dias_trabajados',30)} días"
        concepto_val = (float(nomina.get("salario_base",0)) / 30) * int(nomina.get("dias_trabajados",30))

    s_hdr_w = ParagraphStyle("dhw", fontName="Helvetica-Bold", fontSize=9, textColor=WHITE)
    s_hdr_wc= ParagraphStyle("dhwc",fontName="Helvetica-Bold", fontSize=9, textColor=WHITE, alignment=TA_CENTER)
    s_val_r = ParagraphStyle("dvr", fontName="Helvetica", fontSize=9, textColor=NAVY, alignment=TA_RIGHT)
    s_val_c = ParagraphStyle("dvc", fontName="Helvetica", fontSize=9, textColor=NAVY, alignment=TA_CENTER)
    s_tot   = ParagraphStyle("dtt", fontName="Helvetica-Bold", fontSize=9, textColor=INDIGO)
    s_tot_r = ParagraphStyle("dtr", fontName="Helvetica-Bold", fontSize=9, textColor=INDIGO, alignment=TA_RIGHT)

    PAGE_W = 17*cm  # A4 - 2cm*2 margins
    CW = [PAGE_W*0.55, PAGE_W*0.22, PAGE_W*0.23]

    dev_data = [[Paragraph("Concepto",s_hdr_w), Paragraph("Días/Horas",s_hdr_wc), Paragraph("Valor",s_hdr_wc)],
        [Paragraph(concepto_lbl,value_style), Paragraph(concepto_det,s_val_c), Paragraph(fmt_currency(concepto_val),s_val_r)],
        [Paragraph("Horas extras",value_style), Paragraph(f"{nomina.get('horas_extras',0)} hrs",s_val_c), Paragraph(fmt_currency(float(nomina.get('horas_extras',0))*float(nomina.get('valor_hora_extra',0))),s_val_r)],
        [Paragraph("Bonificaciones",value_style), Paragraph("—",s_val_c), Paragraph(fmt_currency(nomina.get("bonificaciones",0)),s_val_r)],
    ]
    if float(nomina.get("auxilio_transporte") or 0) > 0:
        dev_data.append([Paragraph("Auxilio de transporte",value_style),
                         Paragraph("No salarial",s_val_c),
                         Paragraph(fmt_currency(nomina.get("auxilio_transporte",0)),s_val_r)])
    dev_data.append(
        [Paragraph("TOTAL DEVENGADO",s_tot), Paragraph("",s_val_c), Paragraph(fmt_currency(nomina.get("salario_bruto",0)),s_tot_r)])
    dev_table = Table(dev_data, colWidths=CW)
    dev_table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),NAVY), ("TEXTCOLOR",(0,0),(-1,0),WHITE),
        ("ROWBACKGROUNDS",(0,1),(-1,-2),[WHITE,SILVER]),
        ("BACKGROUND",(0,-1),(-1,-1),INDIGO_L),
        ("BOX",(0,0),(-1,-1),1,colors.HexColor("#CBD5E1")),
        ("INNERGRID",(0,0),(-1,-1),0.5,colors.HexColor("#E2E8F0")),
        ("TOPPADDING",(0,0),(-1,-1),5), ("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),10), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ]))
    story.extend([dev_table, Spacer(1, 0.3*cm)])

    # ── DEDUCCIONES ────────────────────────────────────────────────────────
    story.append(Paragraph("● Deducciones", section_style))
    s_hdr_red = ParagraphStyle("dhr", fontName="Helvetica-Bold", fontSize=9, textColor=WHITE)
    ded_total_val = (float(nomina.get("salud",0)) + float(nomina.get("pension",0))
                     + float(nomina.get("fsp") or 0)
                     + float(nomina.get("retencion") or 0)
                     + float(nomina.get("prestamo",0)) + float(nomina.get("deducciones",0)))
    ded_data = [
        [Paragraph("Concepto",s_hdr_red), Paragraph("Detalle",s_hdr_wc), Paragraph("Valor",s_hdr_wc)],
        [Paragraph("Salud (Empleado)",value_style),    Paragraph("% del IBC",s_val_c),  Paragraph(fmt_currency(nomina.get("salud",0)),s_val_r)],
        [Paragraph("Pensión (Empleado)",value_style),  Paragraph("% del IBC",s_val_c),  Paragraph(fmt_currency(nomina.get("pension",0)),s_val_r)],
    ]
    if float(nomina.get("fsp") or 0) > 0:
        ded_data.append([Paragraph("Fondo Solidaridad Pensional",value_style),
                         Paragraph("% del IBC",s_val_c),
                         Paragraph(fmt_currency(nomina.get("fsp",0)),s_val_r)])
    if float(nomina.get("retencion") or 0) > 0:
        ded_data.append([Paragraph("Retención en la fuente",value_style),
                         Paragraph("Art. 383 ET",s_val_c),
                         Paragraph(fmt_currency(nomina.get("retencion",0)),s_val_r)])
    ded_data += [
        [Paragraph("Préstamo",value_style),            Paragraph("Cuota",s_val_c), Paragraph(fmt_currency(nomina.get("prestamo",0)),s_val_r)],
        [Paragraph("Otras deducciones",value_style),   Paragraph("—",s_val_c),     Paragraph(fmt_currency(nomina.get("deducciones",0)),s_val_r)],
        [Paragraph("TOTAL DEDUCCIONES",ParagraphStyle("tdr",fontName="Helvetica-Bold",fontSize=9,textColor=RED)),
         Paragraph("",s_val_c),
         Paragraph(fmt_currency(ded_total_val),ParagraphStyle("tdvr",fontName="Helvetica-Bold",fontSize=9,textColor=RED,alignment=TA_RIGHT))],
    ]
    ded_table = Table(ded_data, colWidths=CW)
    ded_table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#7F1D1D")), ("TEXTCOLOR",(0,0),(-1,0),WHITE),
        ("ROWBACKGROUNDS",(0,1),(-1,-2),[WHITE,RED_SOFT]),
        ("BACKGROUND",(0,-1),(-1,-1),RED_SOFT),
        ("BOX",(0,0),(-1,-1),1,colors.HexColor("#FECACA")),
        ("INNERGRID",(0,0),(-1,-1),0.5,colors.HexColor("#FEE2E2")),
        ("TOPPADDING",(0,0),(-1,-1),5), ("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),10), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
    ]))
    story.extend([ded_table, Spacer(1, 0.35*cm)])

    # ── NETO A PAGAR ───────────────────────────────────────────────────────
    neto_table = Table([[
        Paragraph("NETO A PAGAR", ParagraphStyle("nl", parent=styles["Normal"],
            fontName="Helvetica-Bold", fontSize=13, textColor=EMERALD, alignment=TA_LEFT)),
        Paragraph(fmt_currency(nomina.get("salario_neto",0)), ParagraphStyle("nv", parent=styles["Normal"],
            fontName="Helvetica-Bold", fontSize=16, textColor=EMERALD, alignment=TA_RIGHT, leading=19)),
    ]], colWidths=[10*cm, 7*cm])
    neto_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#ECFDF5")),
        ("BOX", (0,0), (-1,-1), 2, EMERALD),
        ("TOPPADDING", (0,0), (-1,-1), 9),
        ("BOTTOMPADDING", (0,0), (-1,-1), 9),
        ("LEFTPADDING", (0,0), (-1,-1), 16),
        ("RIGHTPADDING", (0,0), (-1,-1), 16),
    ]))
    story.append(neto_table)

    if nomina.get("observaciones"):
        story.append(Spacer(1, 0.25*cm))
        story.append(Paragraph(f"<b>Observaciones:</b> {nomina['observaciones']}", value_style))

    # ── FOOTER ─────────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.5*cm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CBD5E1")))
    story.append(Spacer(1, 0.15*cm))
    footer_style = ParagraphStyle("footer", parent=styles["Normal"],
                                   fontName="Helvetica", fontSize=7, textColor=SLATE, alignment=TA_CENTER)
    story.append(Paragraph(
        f"Documento generado el {datetime.now().strftime('%d/%m/%Y %H:%M')} • "
        f"Sistema de Gestión de Nómina • Este documento tiene validez como comprobante de pago.",
        footer_style
    ))

    # ── Líneas de firma ────────────────────────────────────────────────────
    story.append(Spacer(1, 0.8*cm))
    firma_data = [[
        Paragraph("_____________________________\nFirma Empleador", ParagraphStyle("firma",
            parent=styles["Normal"], fontName="Helvetica", fontSize=9,
            textColor=SLATE, alignment=TA_CENTER)),
        Paragraph("_____________________________\nFirma Empleado", ParagraphStyle("firma",
            parent=styles["Normal"], fontName="Helvetica", fontSize=9,
            textColor=SLATE, alignment=TA_CENTER)),
    ]]
    firma_table = Table(firma_data, colWidths=[8.5*cm, 8.5*cm])
    firma_table.setStyle(TableStyle([("TOPPADDING", (0,0), (-1,-1), 20)]))
    story.append(firma_table)

    doc.build(story)
    return output_path


def generar_reporte_nomina(empresa: dict, nominas: list, periodo: str, output_path: str = None, empresa_id: int = None) -> str:
    """Genera un reporte general de nómina de un período."""
    if not output_path:
        filename = f"reporte_nomina_{empresa['nombre'].replace(' ','_')}_{periodo}.pdf"
        output_path = os.path.join(get_reports_dir(empresa_id), filename)

    doc = SimpleDocTemplate(output_path, pagesize=letter,
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)
    story = []

    title_s = ParagraphStyle("T", fontName="Helvetica-Bold", fontSize=18,
                              textColor=WHITE, alignment=TA_CENTER, leading=22)
    sub_s   = ParagraphStyle("S", fontName="Helvetica", fontSize=10,
                              textColor=colors.HexColor("#C7D2FE"), alignment=TA_CENTER)

    # Header con logo
    header_content = []
    
    # Añadir logo si existe
    logo_path = empresa.get('logo_path')
    if logo_path and os.path.exists(logo_path):
        try:
            logo_img = Image(logo_path, width=2.5*cm, height=2.5*cm)
            header_content.append([logo_img, Paragraph(empresa["nombre"].upper(), title_s)])
        except:
            header_content.append([Paragraph("", title_s), Paragraph(empresa["nombre"].upper(), title_s)])
    else:
        header_content.append([Paragraph("", title_s), Paragraph(empresa["nombre"].upper(), title_s)])
    
    header = Table(header_content, colWidths=[2.5*cm, 15.5*cm])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), NAVY),
        ("TOPPADDING", (0,0), (-1,-1), 14),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING", (0,0), (-1,-1), 20),
        ("RIGHTPADDING", (0,0), (-1,-1), 20),
    ]))
    sub = Table([[Paragraph(f"NIT: {empresa['nit']}  •  {empresa.get('direccion','')}  •  {empresa.get('telefono','')}", sub_s)]], colWidths=[18*cm])
    sub.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), INDIGO),
        ("TOPPADDING", (0,0), (-1,-1), 7), ("BOTTOMPADDING", (0,0), (-1,-1), 7),
    ]))

    rpt_title = ParagraphStyle("rt", fontName="Helvetica-Bold", fontSize=13,
                                textColor=INDIGO, alignment=TA_CENTER)
    rpt_hdr = Table([[
        Paragraph(f"REPORTE GENERAL DE NÓMINA  •  Período: {periodo}", rpt_title),
    ]], colWidths=[18*cm])
    rpt_hdr.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), INDIGO_L),
        ("BOX", (0,0), (-1,-1), 1, INDIGO),
        ("TOPPADDING", (0,0), (-1,-1), 10), ("BOTTOMPADDING", (0,0), (-1,-1), 10),
    ]))
    story.extend([header, sub, Spacer(1, 0.3*cm), rpt_hdr, Spacer(1, 0.5*cm)])

    # Tabla de nóminas — ancho útil en letter con márgenes 1.5cm = 21.59-3 = 18.59cm
    PAGE_W = 18.5*cm
    s_h  = ParagraphStyle("rnh", fontName="Helvetica-Bold", fontSize=8, textColor=WHITE, alignment=TA_CENTER, leading=10)
    s_l  = ParagraphStyle("rnl", fontName="Helvetica", fontSize=8, textColor=NAVY, leading=10, wordWrap="LTR")
    s_r  = ParagraphStyle("rnr", fontName="Helvetica", fontSize=8, textColor=NAVY, alignment=TA_RIGHT, leading=10)
    s_c  = ParagraphStyle("rnc", fontName="Helvetica", fontSize=8, textColor=NAVY, alignment=TA_CENTER, leading=10)
    s_tb = ParagraphStyle("rntb",fontName="Helvetica-Bold", fontSize=8, textColor=INDIGO, alignment=TA_RIGHT, leading=10)

    col_w = [PAGE_W*0.04, PAGE_W*0.20, PAGE_W*0.14, PAGE_W*0.12,
             PAGE_W*0.06, PAGE_W*0.07, PAGE_W*0.13, PAGE_W*0.12, PAGE_W*0.12]

    data = [[Paragraph("#",s_h), Paragraph("Empleado",s_h), Paragraph("Cargo",s_h),
             Paragraph("Documento",s_h), Paragraph("Días",s_h), Paragraph("H.Extra",s_h),
             Paragraph("Bruto",s_h), Paragraph("Deducciones",s_h), Paragraph("Neto",s_h)]]

    total_bruto = total_neto = total_ded = 0
    for i, n in enumerate(nominas, 1):
        ded_total = (float(n.get("salud",0)) + float(n.get("pension",0))
                     + float(n.get("fsp") or 0) + float(n.get("retencion") or 0)
                     + float(n.get("prestamo",0)) + float(n.get("deducciones",0)))
        total_bruto += float(n.get("salario_bruto",0))
        total_neto  += float(n.get("salario_neto",0))
        total_ded   += ded_total
        data.append([
            Paragraph(str(i),s_c),
            Paragraph(n.get("empleado_nombre",""),s_l),
            Paragraph(n.get("cargo",""),s_l),
            Paragraph(n.get("documento",""),s_c),
            Paragraph(str(n.get("dias_trabajados",30)),s_c),
            Paragraph(str(n.get("horas_extras",0)),s_c),
            Paragraph(fmt_currency(n.get("salario_bruto",0)),s_r),
            Paragraph(fmt_currency(ded_total),s_r),
            Paragraph(fmt_currency(n.get("salario_neto",0)),s_r),
        ])

    data.append([Paragraph("",s_c), Paragraph("TOTALES",ParagraphStyle("tot",fontName="Helvetica-Bold",fontSize=8,textColor=INDIGO)),
                 Paragraph("",s_c), Paragraph("",s_c), Paragraph("",s_c), Paragraph("",s_c),
                 Paragraph(fmt_currency(total_bruto),s_tb),
                 Paragraph(fmt_currency(total_ded),s_tb),
                 Paragraph(fmt_currency(total_neto),s_tb)])

    tbl = Table(data, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),NAVY), ("TEXTCOLOR",(0,0),(-1,0),WHITE),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"), ("FONTSIZE",(0,0),(-1,0),8),
        ("ROWBACKGROUNDS",(0,1),(-1,-2),[WHITE,SILVER]),
        ("BACKGROUND",(0,-1),(-1,-1),INDIGO_L),
        ("BOX",(0,0),(-1,-1),1,colors.HexColor("#CBD5E1")),
        ("INNERGRID",(0,0),(-1,-1),0.3,colors.HexColor("#E2E8F0")),
        ("TOPPADDING",(0,0),(-1,-1),5), ("BOTTOMPADDING",(0,0),(-1,-1),5),
        ("LEFTPADDING",(0,0),(-1,-1),4), ("RIGHTPADDING",(0,0),(-1,-1),4),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
    ]))
    story.append(tbl)

    # Resumen
    story.append(Spacer(1, 0.8*cm))
    story.append(Paragraph("● Resumen del Período", ParagraphStyle("sec", fontName="Helvetica-Bold",
        fontSize=11, textColor=INDIGO)))
    res_data = [
        ["Total empleados pagados", str(len(nominas))],
        ["Total nómina bruta", fmt_currency(total_bruto)],
        ["Total deducciones", fmt_currency(total_ded)],
        ["Total nómina neta", fmt_currency(total_neto)],
    ]
    res_tbl = Table(res_data, colWidths=[8*cm, 6*cm])
    res_tbl.setStyle(TableStyle([
        ("FONTNAME", (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTNAME", (1,0), (1,-2), "Helvetica"),
        ("FONTNAME", (1,-1), (1,-1), "Helvetica-Bold"),
        ("TEXTCOLOR", (1,-1), (1,-1), EMERALD),
        ("FONTSIZE", (1,-1), (1,-1), 13),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [SILVER, WHITE, SILVER, colors.HexColor("#ECFDF5")]),
        ("BOX", (0,0), (-1,-1), 1, colors.HexColor("#CBD5E1")),
        ("INNERGRID", (0,0), (-1,-1), 0.5, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0,0), (-1,-1), 8), ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING", (0,0), (-1,-1), 12),
    ]))
    story.append(Spacer(1, 0.3*cm))
    story.append(res_tbl)

    story.append(Spacer(1, 0.8*cm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CBD5E1")))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')} • Sistema de Gestión de Nómina",
        ParagraphStyle("f", fontName="Helvetica", fontSize=7, textColor=SLATE, alignment=TA_CENTER)
    ))

    doc.build(story)
    return output_path


def generar_reporte_empleados(empleados: list, empresa_info: dict, output_path: str = None, empresa_id: int = None) -> str:
    """Genera un reporte PDF con la lista de empleados y su información."""
    if not output_path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"reporte_empleados_{empresa_info.get('nombre', 'empresa').replace(' ', '_')}_{timestamp}.pdf"
        output_path = os.path.join(get_reports_dir(empresa_id), filename)

    doc = SimpleDocTemplate(output_path, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)

    styles = getSampleStyleSheet()
    story = []

    # Estilos personalizados
    title_style = ParagraphStyle("title", parent=styles["Heading1"],
                                fontName="Helvetica-Bold", fontSize=18,
                                textColor=WHITE, alignment=TA_CENTER,
                                spaceAfter=12, leading=22)

    # Estilo blanco para texto sobre fondo oscuro
    white_style = ParagraphStyle("white", parent=styles["Normal"],
                                 fontName="Helvetica", fontSize=10,
                                 textColor=WHITE)

    # ── HEADER CON LOGO ─────────────────────────────────────────────────────
    header_content = []
    
    # Añadir logo si existe
    logo_path = empresa_info.get('logo_path')
    if logo_path and os.path.exists(logo_path):
        try:
            logo_img = Image(logo_path, width=2*cm, height=2*cm)
            header_content.append([logo_img, Paragraph("REPORTE DE EMPLEADOS", title_style)])
        except:
            header_content.append([Paragraph("", title_style), Paragraph("REPORTE DE EMPLEADOS", title_style)])
    else:
        header_content.append([Paragraph("", title_style), Paragraph("REPORTE DE EMPLEADOS", title_style)])
    
    header_table = Table(header_content, colWidths=[2*cm, 15*cm])
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), NAVY),
        ("ROUNDEDCORNERS", [8, 8, 8, 8]),
        ("TOPPADDING", (0,0), (-1,-1), 20),
        ("BOTTOMPADDING", (0,0), (-1,-1), 16),
        ("LEFTPADDING", (0,0), (-1,-1), 20),
        ("RIGHTPADDING", (0,0), (-1,-1), 20),
    ]))
    story.append(header_table)

    # Información de la empresa
    sub_header = Table([[
        Paragraph(f"{empresa_info.get('nombre', '')}  &nbsp;&nbsp;&nbsp;  NIT: {empresa_info.get('nit', '')}", white_style),
    ]], colWidths=[17*cm])
    sub_header.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), INDIGO),
        ("TOPPADDING", (0,0), (-1,-1), 10),
        ("BOTTOMPADDING", (0,0), (-1,-1), 10),
        ("LEFTPADDING", (0,0), (-1,-1), 20),
        ("RIGHTPADDING", (0,0), (-1,-1), 20),
    ]))
    story.append(sub_header)

    story.append(Spacer(1, 0.5*cm))

    # ── TABLA DE EMPLEADOS ───────────────────────────────────────────────────
    # Ancho útil A4 con márgenes 2cm = 21-4 = 17cm
    PAGE_W = 17*cm
    if empleados:
        s_eh = ParagraphStyle("eh", fontName="Helvetica-Bold", fontSize=8.5, textColor=WHITE, leading=11, wordWrap="LTR")
        s_en = ParagraphStyle("en", fontName="Helvetica", fontSize=8, textColor=NAVY, leading=11, wordWrap="LTR")
        s_ec = ParagraphStyle("ec", fontName="Helvetica", fontSize=8, textColor=NAVY, alignment=TA_CENTER, leading=11)
        s_er = ParagraphStyle("er", fontName="Helvetica", fontSize=8, textColor=NAVY, alignment=TA_RIGHT, leading=11)

        # Proporciones ajustadas al ancho útil
        cw = [PAGE_W*0.14, PAGE_W*0.13, PAGE_W*0.135, PAGE_W*0.065,
              PAGE_W*0.12, PAGE_W*0.10, PAGE_W*0.115, PAGE_W*0.115, PAGE_W*0.08]

        table_data = [[
            Paragraph("Nombre", s_eh), Paragraph("Documento", s_eh), Paragraph("Cargo", s_eh),
            Paragraph("Edad", s_eh), Paragraph("Salario", s_eh), Paragraph("T. Cuenta", s_eh),
            Paragraph("N° Cuenta", s_eh), Paragraph("F. Ingreso", s_eh), Paragraph("Estado", s_eh),
        ]]

        for emp in empleados:
            activo = emp.get("activo", 1)
            s_est = ParagraphStyle("est", fontName="Helvetica-Bold", fontSize=8,
                textColor=EMERALD if activo else RED, alignment=TA_CENTER, leading=11)
            vinc = emp.get("tipo_vinculacion","salario")
            if vinc == "prestacion":
                sal_lbl = fmt_currency(emp.get("tarifa_diaria") or emp.get("salario",0)) + " /día"
            else:
                sal_lbl = fmt_currency(emp.get("salario",0))
            table_data.append([
                Paragraph(emp.get("nombre",""), s_en),
                Paragraph(emp.get("documento",""), s_ec),
                Paragraph(emp.get("cargo",""), s_en),
                Paragraph(str(emp.get("edad","")) if emp.get("edad") else "—", s_ec),
                Paragraph(sal_lbl, s_er),
                Paragraph(emp.get("tipo_cuenta","") or "—", s_ec),
                Paragraph(emp.get("numero_cuenta","") or "—", s_ec),
                Paragraph(emp.get("fecha_ingreso","") or "—", s_ec),
                Paragraph("Activo" if activo else "Inactivo", s_est),
            ])

        empleados_table = Table(table_data, colWidths=cw, repeatRows=1)
        empleados_table.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),INDIGO), ("TEXTCOLOR",(0,0),(-1,0),WHITE),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[WHITE,SILVER]),
            ("BOX",(0,0),(-1,-1),1,colors.HexColor("#CBD5E1")),
            ("INNERGRID",(0,0),(-1,-1),0.4,colors.HexColor("#E2E8F0")),
            ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6),
            ("LEFTPADDING",(0,0),(-1,-1),5), ("RIGHTPADDING",(0,0),(-1,-1),5),
            ("VALIGN",(0,0),(-1,-1),"TOP"),
        ]))
        story.append(empleados_table)

        # Resumen
        story.append(Spacer(1, 0.5*cm))
        total_empleados = len(empleados)
        activos   = sum(1 for e in empleados if e.get("activo",1))
        inactivos = total_empleados - activos
        total_sal = sum(e.get("salario",0) for e in empleados if e.get("activo",1) and e.get("tipo_vinculacion","salario")=="salario")

        s_rk = ParagraphStyle("rk", fontName="Helvetica-Bold", fontSize=10, textColor=SLATE)
        s_rv = ParagraphStyle("rv", fontName="Helvetica", fontSize=10, textColor=NAVY, alignment=TA_RIGHT)
        s_rv_em = ParagraphStyle("rve", fontName="Helvetica-Bold", fontSize=11, textColor=EMERALD, alignment=TA_RIGHT)

        resumen_data = [
            [Paragraph("Total Empleados:", s_rk),       Paragraph(str(total_empleados), s_rv)],
            [Paragraph("Empleados Activos:", s_rk),     Paragraph(str(activos), s_rv)],
            [Paragraph("Empleados Inactivos:", s_rk),   Paragraph(str(inactivos), s_rv)],
            [Paragraph("Total Nómina Mensual (salarios):", s_rk), Paragraph(fmt_currency(total_sal), s_rv_em)],
        ]
        resumen_table = Table(resumen_data, colWidths=[9*cm, 5*cm])
        resumen_table.setStyle(TableStyle([
            ("ROWBACKGROUNDS",(0,0),(-1,-1),[SILVER,WHITE,SILVER,colors.HexColor("#ECFDF5")]),
            ("BOX",(0,0),(-1,-1),1,colors.HexColor("#CBD5E1")),
            ("INNERGRID",(0,0),(-1,-1),0.5,colors.HexColor("#E2E8F0")),
            ("TOPPADDING",(0,0),(-1,-1),8), ("BOTTOMPADDING",(0,0),(-1,-1),8),
            ("LEFTPADDING",(0,0),(-1,-1),12), ("RIGHTPADDING",(0,0),(-1,-1),12),
        ]))
        story.append(resumen_table)

    else:
        story.append(Paragraph("No hay empleados registrados.", ParagraphStyle(
            "ne", fontName="Helvetica", fontSize=12, textColor=SLATE, alignment=TA_CENTER)))

    # ── FOOTER ─────────────────────────────────────────────────────────────
    story.append(Spacer(1, 1*cm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CBD5E1")))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')} • Sistema de Gestión de Nómina",
        ParagraphStyle("f", fontName="Helvetica", fontSize=7, textColor=SLATE, alignment=TA_CENTER)
    ))

    doc.build(story)
    return output_path


# ─── REPORTE DE BITÁCORA ─────────────────────────────────────────────────────

def generar_reporte_bitacora(registros, empresa=None, empresa_id=None):
    """Genera PDF con el registro de auditoría — diseño profesional, sin textos cruzados."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre = (empresa.get("nombre","Empresa") if empresa else "Sistema").replace(" ","_")
    output_path = os.path.join(get_reports_dir(empresa_id), f"Bitacora_{nombre}_{ts}.pdf")

    doc = SimpleDocTemplate(output_path, pagesize=A4,
        topMargin=2.5*cm, bottomMargin=1.8*cm,
        leftMargin=1.5*cm, rightMargin=1.5*cm)

    PAGE_W = A4[0] - 3*cm   # ancho útil

    # ── Estilos ──────────────────────────────────────────────────────────────
    s_title = ParagraphStyle("title", fontName="Helvetica-Bold",
        fontSize=20, textColor=INDIGO, spaceAfter=4, leading=24)
    s_sub   = ParagraphStyle("sub",   fontName="Helvetica",
        fontSize=10, textColor=SLATE, spaceAfter=2, leading=13)
    s_hdr   = ParagraphStyle("hdr",   fontName="Helvetica-Bold",
        fontSize=8,  textColor=WHITE, alignment=TA_CENTER, leading=10)
    s_cell  = ParagraphStyle("cell",  fontName="Helvetica",
        fontSize=7.5, textColor=colors.HexColor("#1E293B"),
        leading=10, wordWrap="LTR")
    s_cell_c= ParagraphStyle("cellc", fontName="Helvetica",
        fontSize=7.5, textColor=colors.HexColor("#1E293B"),
        leading=10, alignment=TA_CENTER, wordWrap="LTR")
    s_foot  = ParagraphStyle("foot",  fontName="Helvetica",
        fontSize=7.5, textColor=SLATE, alignment=TA_CENTER)

    # Colores
    COL_HDR  = INDIGO
    COL_ALT  = colors.HexColor("#F1F5F9")
    COL_LINE = colors.HexColor("#CBD5E1")
    COL_WH   = WHITE

    story = []

    # ── Encabezado ────────────────────────────────────────────────────────────
    logo_widget = None
    if empresa and empresa.get("logo_path") and os.path.exists(empresa["logo_path"]):
        try:
            logo_widget = Image(empresa["logo_path"], width=2*cm, height=2*cm)
        except: pass

    # Fila con logo + título
    if logo_widget:
        hdr_data = [[logo_widget,
            [Paragraph("Bitácora de Auditoría", s_title),
             Paragraph(f"{empresa.get('nombre','')}  ·  NIT: {empresa.get('nit','')}", s_sub),
             Paragraph(f"Período: {datetime.now().strftime('%B %Y')}", s_sub)]]]
        hdr_t = Table(hdr_data, colWidths=[2.5*cm, PAGE_W-2.5*cm])
        hdr_t.setStyle(TableStyle([
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#F8FAFC")),
            ("BOX",(0,0),(-1,-1),0.5,COL_LINE),
            ("LEFTPADDING",(0,0),(-1,-1),10),
            ("RIGHTPADDING",(0,0),(-1,-1),10),
            ("TOPPADDING",(0,0),(-1,-1),8),
            ("BOTTOMPADDING",(0,0),(-1,-1),8),
        ]))
        story.append(hdr_t)
    else:
        story.append(Paragraph("Bitácora de Auditoría", s_title))
        if empresa:
            story.append(Paragraph(
                f"{empresa.get('nombre','')}  ·  NIT: {empresa.get('nit','')}", s_sub))
    story.append(Spacer(1, 0.5*cm))

    # ── Resumen rápido ────────────────────────────────────────────────────────
    total = len(registros)
    usuarios = len(set(r.get("usuario_nom","") for r in registros))
    resumen = Table([[
        Paragraph(f"<b>Total registros</b><br/>{total}", ParagraphStyle(
            "rc", fontName="Helvetica", fontSize=9, textColor=INDIGO,
            alignment=TA_CENTER, leading=12)),
        Paragraph(f"<b>Usuarios activos</b><br/>{usuarios}", ParagraphStyle(
            "rc2", fontName="Helvetica", fontSize=9, textColor=SLATE,
            alignment=TA_CENTER, leading=12)),
        Paragraph(f"<b>Generado</b><br/>{datetime.now().strftime('%d/%m/%Y %H:%M')}", ParagraphStyle(
            "rc3", fontName="Helvetica", fontSize=9, textColor=SLATE,
            alignment=TA_CENTER, leading=12)),
    ]], colWidths=[PAGE_W/3]*3)
    resumen.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),1,INDIGO),
        ("INNERGRID",(0,0),(-1,-1),0.5,COL_LINE),
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#EEF2FF")),
        ("TOPPADDING",(0,0),(-1,-1),8),
        ("BOTTOMPADDING",(0,0),(-1,-1),8),
        ("ROUNDEDCORNERS",[4]),
    ]))
    story.append(resumen)
    story.append(Spacer(1, 0.4*cm))

    # ── Tabla de registros ────────────────────────────────────────────────────
    # Anchos proporcionales al ancho útil
    cw = [2.7*cm, 2.5*cm, 5.8*cm, PAGE_W-2.7*cm-2.5*cm-5.8*cm]

    tdata = [[
        Paragraph("Fecha", s_hdr),
        Paragraph("Usuario", s_hdr),
        Paragraph("Acción", s_hdr),
        Paragraph("Detalle", s_hdr),
    ]]

    for i, r in enumerate(registros):
        fecha = (r.get("fecha","") or "")[:19].replace("T"," ")
        usuario = r.get("usuario_nom","—") or "—"
        accion  = r.get("accion","") or ""
        detalle = r.get("detalle","") or ""
        tdata.append([
            Paragraph(fecha,   s_cell_c),
            Paragraph(usuario, s_cell_c),
            Paragraph(accion,  s_cell),
            Paragraph(detalle, s_cell),
        ])

    tabla = Table(tdata, colWidths=cw, repeatRows=1)
    row_styles = [
        # Encabezado
        ("BACKGROUND",(0,0),(-1,0), COL_HDR),
        ("TEXTCOLOR",(0,0),(-1,0),  WHITE),
        ("FONTNAME",(0,0),(-1,0),   "Helvetica-Bold"),
        ("TOPPADDING",(0,0),(-1,0), 7),
        ("BOTTOMPADDING",(0,0),(-1,0), 7),
        # Filas
        ("FONTSIZE",(0,1),(-1,-1),  7.5),
        ("TOPPADDING",(0,1),(-1,-1), 5),
        ("BOTTOMPADDING",(0,1),(-1,-1), 5),
        ("LEFTPADDING",(0,0),(-1,-1), 5),
        ("RIGHTPADDING",(0,0),(-1,-1), 5),
        ("VALIGN",(0,0),(-1,-1),    "TOP"),
        ("GRID",(0,0),(-1,-1),      0.4, COL_LINE),
        ("LINEBELOW",(0,0),(-1,0),  1.5, COL_HDR),
    ]
    # Filas alternas
    for i in range(1, len(tdata)):
        if i % 2 == 0:
            row_styles.append(("BACKGROUND",(0,i),(-1,i), COL_ALT))
        else:
            row_styles.append(("BACKGROUND",(0,i),(-1,i), COL_WH))

    tabla.setStyle(TableStyle(row_styles))
    story.append(tabla)

    # ── Pie de página ─────────────────────────────────────────────────────────
    story.append(Spacer(1, 0.6*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=COL_LINE))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        f"Generado el {datetime.now().strftime('%d/%m/%Y a las %H:%M')}  ·  "
        f"Total: {total} registros  ·  NóminaApp", s_foot))

    doc.build(story)
    return output_path


# ─── CERTIFICADO LABORAL ─────────────────────────────────────────────────────

def generar_certificado_laboral(empleado, empresa, empresa_id=None):
    """Genera PDF certificado laboral — diseño profesional con membrete completo."""
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre= empleado.get("nombre","Empleado").replace(" ","_")
    output_path = os.path.join(get_reports_dir(empresa_id), f"Certificado_{nombre}_{ts}.pdf")

    doc = SimpleDocTemplate(output_path, pagesize=A4,
        topMargin=2*cm, bottomMargin=2*cm, leftMargin=2.5*cm, rightMargin=2.5*cm)
    story  = []
    PAGE_W = A4[0] - 5*cm   # 16.7cm útiles

    MESES = ["enero","febrero","marzo","abril","mayo","junio",
             "julio","agosto","septiembre","octubre","noviembre","diciembre"]
    hoy    = datetime.now()
    fecha_texto = f"{hoy.day} de {MESES[hoy.month-1]} de {hoy.year}"

    # ── Membrete ──────────────────────────────────────────────────────────────
    s_emp_name = ParagraphStyle("cn", fontName="Helvetica-Bold", fontSize=15,
        textColor=WHITE, alignment=TA_CENTER, leading=19)
    s_emp_sub  = ParagraphStyle("cs", fontName="Helvetica", fontSize=9,
        textColor=colors.HexColor("#C7D2FE"), alignment=TA_CENTER)

    logo_cell = ""
    if empresa.get("logo_path") and os.path.exists(empresa["logo_path"]):
        try:
            logo_cell = Image(empresa["logo_path"], width=2.2*cm, height=2.2*cm)
        except: pass

    hdr_data = [[logo_cell,
        [Paragraph(empresa.get("nombre","").upper(), s_emp_name),
         Spacer(1,3),
         Paragraph(f"NIT: {empresa.get('nit','')}   ·   {empresa.get('direccion','')}   ·   Tel: {empresa.get('telefono','')}", s_emp_sub)]
    ]]
    hdr_t = Table(hdr_data, colWidths=[2.8*cm, PAGE_W-2.8*cm])
    hdr_t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), NAVY),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),14),
        ("BOTTOMPADDING",(0,0),(-1,-1),14),
        ("LEFTPADDING",(0,0),(-1,-1),14),
    ]))
    story.append(hdr_t)

    # Banda INDIGO con título
    banda = Table([[Paragraph("CERTIFICADO LABORAL", ParagraphStyle(
        "ct", fontName="Helvetica-Bold", fontSize=13,
        textColor=WHITE, alignment=TA_CENTER))]],
        colWidths=[PAGE_W+2.8*cm])
    banda.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),INDIGO),
        ("TOPPADDING",(0,0),(-1,-1),9),
        ("BOTTOMPADDING",(0,0),(-1,-1),9),
    ]))
    story.append(banda)
    story.append(Spacer(1, 1.2*cm))

    # ── Cuerpo ────────────────────────────────────────────────────────────────
    s_body = ParagraphStyle("cb", fontName="Helvetica", fontSize=11,
        textColor=colors.HexColor("#1E293B"), alignment=TA_JUSTIFY, leading=20, spaceAfter=10)

    fi = empleado.get("fecha_ingreso","—") or "—"
    # Calcular tiempo de servicio
    try:
        fi_date = datetime.strptime(fi, "%Y-%m-%d")
        delta   = hoy - fi_date
        anios   = delta.days // 365
        meses_s = (delta.days % 365) // 30
        tiempo  = f"{anios} año(s) y {meses_s} mes(es)"
    except: tiempo = "—"

    cargo   = empleado.get("cargo","—")
    salario = fmt_currency(empleado.get("salario",0))
    tc      = empleado.get("tipo_cuenta","") or ""
    nc      = empleado.get("numero_cuenta","") or ""
    vinc    = empleado.get("tipo_vinculacion","salario")

    parrafo1 = (
        f"La empresa <b>{empresa.get('nombre','')}</b>, identificada con NIT "
        f"<b>{empresa.get('nit','')}</b>, certifica que el/la señor(a) "
        f"<b>{empleado.get('nombre','')}</b>, identificado(a) con documento de "
        f"identidad No. <b>{empleado.get('documento','')}</b>, labora en nuestra "
        f"organización desde el <b>{fi}</b>, con un tiempo de servicio de "
        f"<b>{tiempo}</b>."
    )
    if vinc == "salario":
        parrafo2 = (
            f"El/la empleado(a) desempeña actualmente el cargo de <b>{cargo}</b>, "
            f"con una asignación salarial mensual de <b>{salario}</b>."
        )
    else:
        tarifa = fmt_currency(empleado.get("tarifa_diaria") or empleado.get("salario",0))
        parrafo2 = (
            f"El/la empleado(a) desempeña actualmente el cargo de <b>{cargo}</b>, "
            f"bajo modalidad de prestación de servicios con una tarifa de <b>{tarifa}</b> por día."
        )
    parrafo3 = "Esta certificación se expide a solicitud del interesado para los fines que estime convenientes."

    story.append(Paragraph(parrafo1, s_body))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(parrafo2, s_body))
    if tc and nc:
        story.append(Spacer(1, 0.2*cm))
        story.append(Paragraph(
            f"La cuenta de pago registrada es: <b>{tc} No. {nc}</b>.", s_body))
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(parrafo3, s_body))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        f"Se expide en {empresa.get('municipio','') or 'Colombia'} "
        f"el día {fecha_texto}.", s_body))

    story.append(Spacer(1, 2.5*cm))

    # ── Firma ─────────────────────────────────────────────────────────────────
    s_firma = ParagraphStyle("cf", fontName="Helvetica", fontSize=10,
        textColor=SLATE, alignment=TA_CENTER, leading=14)
    s_firma_b= ParagraphStyle("cfb",fontName="Helvetica-Bold", fontSize=11,
        textColor=NAVY, alignment=TA_CENTER)

    firma_data = [[
        [Paragraph("_______________________________", s_firma),
         Spacer(1,3),
         Paragraph(empresa.get("nombre","").upper(), s_firma_b),
         Paragraph(f"NIT: {empresa.get('nit','')}", s_firma),
         Paragraph("Representante Legal", s_firma)],

        [Paragraph("_______________________________", s_firma),
         Spacer(1,3),
         Paragraph(empleado.get("nombre","").upper(), s_firma_b),
         Paragraph(f"C.C. {empleado.get('documento','')}", s_firma),
         Paragraph("Empleado(a)", s_firma)],
    ]]
    ft = Table(firma_data, colWidths=[PAGE_W/2+1.4*cm, PAGE_W/2+1.4*cm])
    ft.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("TOPPADDING",(0,0),(-1,-1),0),
    ]))
    story.append(ft)

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 1*cm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#CBD5E1")))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        f"Documento generado el {datetime.now().strftime('%d/%m/%Y %H:%M')}  ·  "
        f"NóminaApp — Sistema de Gestión de Nómina",
        ParagraphStyle("f", fontName="Helvetica", fontSize=7,
            textColor=SLATE, alignment=TA_CENTER)))

    doc.build(story)
    return output_path