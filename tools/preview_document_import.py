"""Render the importer with a synthetic PDF only; never opens the user's database."""
from pathlib import Path
import os


def main():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ.setdefault('SPEDI_LANGUAGE', 'pl')
    from PySide6.QtCore import QRectF
    from PySide6.QtWidgets import QApplication
    from reportlab.pdfgen.canvas import Canvas
    from markus_dispatcher.ui.document_import_dialog import DocumentImportDialog, FIELDS
    from markus_dispatcher.ui.theme import apply_theme

    output = Path(__file__).resolve().parents[1] / 'work' / 'document-import-preview'
    output.mkdir(parents=True, exist_ok=True)
    document = output / 'synthetic-order.pdf'
    canvas = Canvas(str(document), pagesize=(600, 800))
    canvas.setFont('Helvetica-Bold', 20)
    canvas.drawString(40, 755, 'DEMO LOGISTICS')
    canvas.setFont('Helvetica', 11)
    canvas.drawString(40, 725, 'TRANSPORT ORDER: DEMO/1042/2026')
    canvas.drawString(40, 695, 'Client: Synthetic Logistics')
    for x, label, address, city, time in ((40, 'LOADING', 'Demo Street 12', '00-001 Warsaw, PL', '26.10.2026 08:00'),
                                        (320, 'UNLOADING', 'Example Street 45', '10115 Berlin, DE', '27.10.2026 10:00')):
        canvas.rect(x-10, 485, 250, 170)
        canvas.setFont('Helvetica-Bold', 12)
        canvas.drawString(x, 630, label)
        canvas.setFont('Helvetica', 11)
        for y, text in ((600, address), (580, city), (540, time)):
            canvas.drawString(x, y, text)
    canvas.drawString(40, 445, 'Cargo: 8 pallets, 2400 kg')
    canvas.drawString(40, 425, 'Freight: 650.00 EUR')
    canvas.setFont('Helvetica-Bold', 12)
    canvas.drawString(40, 370, 'PROOF OF DELIVERY')
    canvas.setFont('Helvetica', 11)
    canvas.drawString(40, 340, 'Signed CMR and WZ in one PDF within 24h after unloading.')
    canvas.drawString(40, 320, 'Send to pod@example.invalid. Late submission: 25 EUR.')
    canvas.setFont('Helvetica', 9)
    canvas.drawString(40, 60, 'Synthetic data for interface verification. No real customer information.')
    canvas.save()
    app = QApplication.instance() or QApplication([])
    from PySide6.QtGui import QFontDatabase
    font = Path('C:/Windows/Fonts/segoeui.ttf')
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
    apply_theme(app)
    dialog = DocumentImportDialog()
    dialog.show()
    dialog.open_path(document)
    dialog.zoom_combo.setCurrentIndex(0)
    for key, value in {'ref_numbers':'DEMO/1042/2026', 'client_name':'Synthetic Logistics',
                       'load_address':'Demo Street 12\n00-001 Warsaw, PL', 'load_datetime':'26.10.2026 08:00',
                       'unload_address':'Example Street 45\n10115 Berlin, DE'}.items():
        dialog.edits[key].setPlainText(value)
    dialog._activate(next(i for i, (key, _) in enumerate(FIELDS) if key == 'load_address'))
    dialog._region(QRectF(30/600, 180/800, 250/600, 55/800))
    for _ in range(3):
        app.processEvents()
    dialog.grab().save(str(output / 'importer-desktop.png'))
    dialog.resize(980, 720)
    for _ in range(3):
        app.processEvents()
    dialog.grab().save(str(output / 'importer-small.png'))
    dialog.reject()
    print('Synthetic importer previews rendered.')


if __name__ == '__main__':
    main()
