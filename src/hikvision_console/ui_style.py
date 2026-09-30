STYLE = """
QWidget { color: #d9e2ed; font-family: 'Inter', 'Segoe UI', 'PingFang SC', sans-serif; font-size: 13px; }
QMainWindow, QDialog { background: #0c1119; }
QWidget#Sidebar { background: #111923; border-right: 1px solid #263142; }
QWidget#Header { background: #111923; border-bottom: 1px solid #263142; }
QLabel#Brand { font-size: 21px; font-weight: 750; letter-spacing: 2px; color: #f1f5fa; }
QLabel#Subtitle { color: #8191a6; font-size: 11px; }
QLabel#PageTitle { font-size: 23px; font-weight: 650; }
QLabel#Accent { color: #55ddbf; }
QLabel#Muted { color: #8191a6; }
QLabel#Error { color: #ffad83; }
QLabel#TileTitle { font-weight: 600; color: #eff5fb; }
QLabel#Placeholder { color: #55647a; font-size: 18px; }
QPushButton, QToolButton { background: #1b2736; border: 1px solid #2b3a4e; border-radius: 6px; padding: 7px 12px; }
QPushButton:hover, QToolButton:hover { background: #26364a; border-color: #58718c; }
QPushButton:pressed { background: #31465f; }
QPushButton:checked { background: #16463e; border-color: #37bda0; color: #8ff0da; }
QPushButton:disabled { color: #506079; background: #141c28; border-color: #202c3b; }
QPushButton#Primary { background: #38c9a7; color: #06251e; border: none; font-weight: 650; }
QPushButton#Primary:hover { background: #67ddc1; }
QPushButton#Nav { text-align: left; padding: 12px 14px; border: none; background: transparent; }
QPushButton#Nav:checked { background: #203b3b; color: #7ae4cb; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit, QTimeEdit, QDateTimeEdit {
 background: #131e2b; border: 1px solid #304057; border-radius: 5px; padding: 6px; selection-background-color: #286c62;
}
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #172334; selection-background-color: #28564f; }
QTreeWidget, QListWidget, QTableWidget { background: #101821; border: 1px solid #233145; border-radius: 6px; alternate-background-color: #15202d; }
QTreeWidget::item, QListWidget::item { padding: 7px 3px; }
QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected { background: #24463f; }
QHeaderView::section { background: #1a2737; color: #a6b8cd; border: none; padding: 8px; }
QFrame#Tile { background: #111b27; border: 1px solid #2a3c50; border-radius: 7px; }
QFrame#Tile[focused="true"] { border: 1px solid #50d8b4; }
QFrame#Tile[warning="true"] { border: 1px solid #de9460; }
QFrame#VideoSurface { background: #05080d; border: none; border-radius: 0; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: #101721; width: 8px; }
QScrollBar::handle:vertical { background: #35485d; min-height: 24px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QGroupBox { border: 1px solid #2a3a4f; border-radius: 6px; margin-top: 12px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; color: #adbed2; }
QCheckBox { spacing: 7px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QProgressBar { background: #182637; border: none; border-radius: 4px; text-align: center; height: 18px; }
QProgressBar::chunk { background: #269a82; border-radius: 4px; }
QTabWidget::pane { border: 1px solid #29384b; }
QTabBar::tab { background: #172334; padding: 9px 18px; }
QTabBar::tab:selected { background: #26554d; }
QTextEdit, QPlainTextEdit { background: #101821; border: 1px solid #26364c; }
QMenu { background: #172333; border: 1px solid #34445b; }
QMenu::item { padding: 8px 22px; }
QMenu::item:selected { background: #27584e; }
QToolTip { background: #203248; color: #ecf2f9; border: 1px solid #45617f; padding: 5px; }
"""
