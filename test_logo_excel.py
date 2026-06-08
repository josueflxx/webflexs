"""Generate a standalone test Excel with the exact same logo logic as the production code."""
import os
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flexs_project.settings.local')
import django
django.setup()

from io import BytesIO
from PIL import Image as PILImage
from openpyxl import Workbook
from openpyxl.drawing.image import Image
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from django.conf import settings

logo_path = os.path.join(settings.BASE_DIR, 'core', 'static', 'core', 'img', 'flexs-logo.png')
print(f"Logo: {logo_path} (exists={os.path.exists(logo_path)})")

wb = Workbook()
ws = wb.active
ws.title = "INDICE"
ws.sheet_view.showGridLines = False
ws.sheet_view.zoomScale = 90
ws.sheet_properties.tabColor = "FF6B3A"

# --- Build all cells and merges FIRST ---
ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=5)
ws["A1"] = "Catalogo FLEXS"
ws["A1"].font = Font(name="Segoe UI", color="FFFFFF", bold=True, size=16)
ws["A1"].fill = PatternFill(fill_type="solid", fgColor="FF6B3A")
ws["A1"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
ws.row_dimensions[1].height = 50  # Tall enough for the logo

ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=5)
ws["A2"] = "Lista digital de productos activos, visibles para clientes y con precio publicable."
ws["A2"].font = Font(name="Segoe UI", color="E5E7EB", italic=True)
ws["A2"].fill = PatternFill(fill_type="solid", fgColor="111827")
ws["A2"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
ws.row_dimensions[2].height = 22

# Stat cards
card_fill = PatternFill(fill_type="solid", fgColor="F8FAFC")
thin_border = Border(
    left=Side(style="thin", color="D1D5DB"),
    right=Side(style="thin", color="D1D5DB"),
    top=Side(style="thin", color="D1D5DB"),
    bottom=Side(style="thin", color="D1D5DB"),
)
labels = ["Productos", "Hojas", "Version", "Generado", "Vigente desde"]
values = ["1250", "8", "catalogo-20260528", "28/05/2026 14:30", "28/05/2026 14:30"]
for col_idx, (label, value) in enumerate(zip(labels, values), start=1):
    lc = ws.cell(row=4, column=col_idx, value=label)
    vc = ws.cell(row=5, column=col_idx, value=value)
    for c in (lc, vc):
        c.fill = card_fill
        c.border = thin_border
        c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    lc.font = Font(name="Segoe UI", color="374151", bold=True)
    vc.font = Font(name="Segoe UI", color="111827", bold=True, size=14)

# Column widths
ws.column_dimensions["A"].width = 38
ws.column_dimensions["B"].width = 18
ws.column_dimensions["C"].width = 22
ws.column_dimensions["D"].width = 30
ws.column_dimensions["E"].width = 20
ws.column_dimensions["F"].width = 35

# --- NOW insert logo AFTER all cells/merges ---
print("Inserting logo with RGBA->RGB conversion via BytesIO...")
try:
    pil_img = PILImage.open(logo_path)
    print(f"  Original mode: {pil_img.mode}, size: {pil_img.size}")

    if pil_img.mode == 'RGBA':
        background = PILImage.new('RGB', pil_img.size, (255, 255, 255))
        background.paste(pil_img, mask=pil_img.split()[3])
        pil_img.close()
        pil_img = background
        print(f"  Converted to RGB")

    logo_buffer = BytesIO()
    pil_img.save(logo_buffer, 'PNG')
    pil_img.close()
    logo_buffer.seek(0)
    print(f"  BytesIO size: {logo_buffer.getbuffer().nbytes} bytes")

    img = Image(logo_buffer)
    img.width = 240
    img.height = 50
    ws.add_image(img, 'F1')
    print(f"  Logo added at F1 successfully!")
except Exception as e:
    print(f"  ERROR: {e}")
    import traceback
    traceback.print_exc()

output_path = os.path.join(settings.BASE_DIR, 'test_logo_final.xlsx')
wb.save(output_path)
print(f"\nSaved: {output_path}")
print("Open this file in Excel to verify the logo appears at F1!")
