from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor


def issue_text(block, issues):
    return '\n'.join(dict.fromkeys(reason for start, end, reason in issues
                                  if block.start < end and block.end > start))


def draw_issue_badge(painter, rect):
    painter.save()
    badge = QRectF(rect.right() - 13, rect.top(), 13, 16)
    painter.fillRect(badge, QColor('#b45309'))
    painter.setPen(QColor('#ffffff'))
    font = painter.font()
    font.setBold(True)
    font.setPointSize(10)
    painter.setFont(font)
    painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, '!')
    painter.restore()
