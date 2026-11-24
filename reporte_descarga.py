"""Generación del Excel de reporte de descarga para Mesa de Entradas."""
import io
import uuid
from datetime import datetime

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

import database as db


def generar_excel_descarga(archivos: list, agente: dict) -> tuple:
    """Genera el Excel de reporte y registra la operación en descargas_mesa.

    archivos: lista de dicts con archivo_nombre, lpu, dni, expediente_ppn,
              nota_id, bloque_id y tipo_archivo ("ADJUNTO" o "FIRMADO").
    agente: dict con id (int) y nombre (str).

    Retorna: (bytes del Excel, nombre sugerido del archivo)
    """
    paquete_id = str(uuid.uuid4())
    fecha_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    fecha_nombre = datetime.now().strftime("%Y%m%d_%H%M%S")

    db.registrar_descarga_mesa(paquete_id, archivos, agente)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Reporte de descarga"

    estilo_encabezado = Font(bold=True, color="FFFFFF")
    fondo_encabezado = PatternFill(fill_type="solid", fgColor="1F4E79")
    borde = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    centrado = Alignment(horizontal="center", vertical="center")

    ws["A1"] = "Reporte de descarga DGPDH"
    ws["A1"].font = Font(bold=True, size=13)
    ws.merge_cells("A1:G1")
    ws["A2"] = f"Fecha y hora: {fecha_str}"
    ws.merge_cells("A2:G2")
    ws["A3"] = f"Agente: {agente['nombre']}"
    ws.merge_cells("A3:G3")
    ws["A4"] = f"ID de paquete: {paquete_id}"
    ws.merge_cells("A4:G4")

    encabezados = [
        "Nombre del archivo", "LPU", "DNI",
        "Expediente PPN", "Tipo", "Agente descarga", "Fecha y hora",
    ]
    for col, titulo in enumerate(encabezados, start=1):
        c = ws.cell(row=6, column=col, value=titulo)
        c.font = estilo_encabezado
        c.fill = fondo_encabezado
        c.alignment = centrado
        c.border = borde

    for fila, archivo in enumerate(archivos, start=7):
        for col, valor in enumerate([
            archivo.get("archivo_nombre", ""),
            archivo.get("lpu") or "",
            archivo.get("dni") or "",
            archivo.get("expediente_ppn") or "",
            archivo.get("tipo_archivo", ""),
            agente["nombre"],
            fecha_str,
        ], start=1):
            c = ws.cell(row=fila, column=col, value=valor)
            c.border = borde
            c.alignment = Alignment(vertical="center")

    for col, ancho in enumerate([45, 20, 15, 20, 12, 30, 22], start=1):
        ws.column_dimensions[ws.cell(row=6, column=col).column_letter].width = ancho

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue(), f"descarga_mesa_{fecha_nombre}.xlsx"
